"""Agent loop: control flow của một turn.

File này KHÔNG import bất kỳ implementation nào (không openai, không jsonschema).
Nó chỉ khai báo Protocol cho ba thứ nó cần — LLM, Tools, Session — rồi lái vòng lặp.
Đổi provider LLM hay thêm tool đều không cần sửa file này.

Đối chiếu harness thật: packages/core/agent-loop/src/agent.ts
"""

from __future__ import annotations

import asyncio
from typing import Any, Protocol

from mini_harness.core.compaction import CompactionSession, Summarizer, maybe_compact
from mini_harness.core.types import (
    AssistantMessage,
    StreamCancelled,
    StreamInterrupted,
    ToolResult,
)

# ------------------------------------------------------------------- protocols
# Contract loop cần. Ba file kia implement, không cần import ngược lại chỗ này
# (Protocol là structural typing — không cần kế thừa).


class LLM(Protocol):
    """Model interface. Một lần gọi = một request."""

    async def generate(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> AssistantMessage:
        """Gọi model một lần.

        `tools` là ToolSchema trung lập (name/description/parameters) — CHƯA
        phải wire format. Việc bọc thành `{type:'function', function:{...}}` là
        việc của provider, vì mỗi provider bọc một kiểu.
        Đối chiếu: llm-deepseek/serialize.ts:349.
        """
        ...


class Tools(Protocol):
    """Tool registry."""

    def schemas(self) -> list[dict[str, Any]]:
        """JSON Schema của các tool đang visible, để gửi cho model.

        Chỉ được trả name/description/parameters — metadata nội bộ không lọt ra.
        Đối chiếu: schemaOf() ở core/tools/src/index.ts:1246.
        """
        ...

    async def execute(self, name: str, arguments_json: str) -> ToolResult:
        """Chạy một tool call và LUÔN trả về ToolResult — không bao giờ raise.

        Đây là điều kiện để cơ chế sửa-schema hoạt động: tool không tồn tại,
        JSON hỏng, args sai schema, tool tự throw — tất cả thành
        `ToolResult(is_error=True)` để model đọc được ở lượt sau.
        Đối chiếu: toolErrorResult() ở core/tools/src/index.ts:1860.
        """
        ...


class Session(CompactionSession, Protocol):
    """Append-only event log của một cuộc hội thoại.

    Kế thừa `CompactionSession` thay vì chép lại hai method của nó: chủ sở hữu
    contract đó là `core/compaction.py` (nó là nơi dùng), loop chỉ chuyển tiếp.
    """

    def append(self, event: dict[str, Any]) -> None:
        """Ghi thêm một event. Không sửa, không xoá."""
        ...

    def to_messages(self) -> list[dict[str, Any]]:
        """Project event log thành message list cho model.

        Tách khỏi `append` có chủ đích: event log là nguồn sự thật, message list
        là thứ phái sinh. Đây là cái làm resume/replay khả thi.
        Đối chiếu: core/session/src/surface.ts.
        """
        ...

    def abort_pending_tool_calls(self) -> int:
        """Ghi result giả cho mọi tool_call còn treo, trả về số call đã vá.

        Loop gọi khi bị huỷ. Loop KHÔNG tự dựng event giả — nó không sở hữu
        model-facing text; nó chỉ biết "lúc này log đang thiếu result".
        """
        ...


class MaxStepsExceeded(RuntimeError):
    """Turn không kết thúc trong giới hạn step cho phép."""


# ------------------------------------------------------------------- the loop


async def run_turn(
    *,
    llm: LLM,
    tools: Tools,
    session: Session,
    system: str,
    user_input: str,
    max_steps: int = 20,
    summarizer: Summarizer | None = None,
) -> str:
    """Chạy một turn tới khi model trả lời không kèm tool call.

    Một turn = nhiều step. Một step = một model call + các tool call của nó.

    `max_steps` là cần thiết, không phải phòng xa: khi args sai schema, model
    nhận lỗi và thử lại — nếu nó thử mãi thì turn không bao giờ dừng.
    Harness thật đặt việc này ở plugin riêng (packages/guard/).

    `summarizer` là model dùng cho phép NÉN, tách khỏi `llm` dùng cho hội thoại.
    Mặc định None = dùng chính `llm`. Hai Protocol này vốn đã khác nhau (`LLM`
    và `Summarizer`); việc app thường đưa cùng một object vào cả hai chỗ là
    trùng hợp, không phải contract — và đo thật thì chính sự trùng hợp đó là
    lỗi: provider của hội thoại mang theo kênh hiển thị, nên bản tóm tắt bị in
    thẳng ra terminal, dính liền vào câu trả lời của model.

    Returns:
        Text của lượt trả lời cuối.

    Raises:
        MaxStepsExceeded: hết `max_steps` mà model vẫn còn gọi tool.
    """
    session.append({"type": "user", "content": user_input})

    for _step in range(max_steps):
        # Nén TRƯỚC khi gọi model, và ở mỗi step chứ không mỗi turn: tool
        # result là nguồn phình nhanh nhất, mà chúng sinh ra giữa turn. Đợi
        # tới đầu turn sau thì request vượt cửa sổ đã đi rồi.
        #
        # Loop không hỏi "cần nén không" — nó không biết ngân sách, và theo
        # đúng luật cũ thì nó cũng không được diễn giải con số token nào.
        # Nó chỉ chuyển cho `maybe_compact` ba thứ chỉ mình nó cầm: llm,
        # system prompt, và tập tool schema của request kế tiếp.
        await maybe_compact(session=session, llm=summarizer or llm,
                            system=system, tools=tools.schemas())

        # Stream có thể vỡ SAU khi vài chữ đã lên màn hình: `on_text` đẩy
        # từng mảnh ra terminal ngay lúc nó tới, còn event `assistant` dưới
        # kia chỉ được ghi khi `generate()` đã trả về trọn vẹn. Để exception
        # bay thẳng lên là để người dùng đọc một đoạn text không tồn tại ở
        # bất cứ đâu — và `--replay` sau đó phát lại một hội thoại khác với
        # hội thoại họ vừa xem. Ghi trước, rồi mới cho bay.
        #
        # Hai nhánh KHÔNG gộp được, dù text y hệt nhau. Chúng khác ở chỗ quan
        # trọng nhất: `assistant` thì model đọc lại được, `assistant_attempt`
        # thì không. Chia theo NGUYÊN NHÂN, không theo nội dung — xem
        # core/types.py. Đối chiếu: agent.ts:386 vs agent.ts:428.
        try:
            reply = await llm.generate(
                system=system,
                messages=session.to_messages(),
                tools=tools.schemas(),
            )
        except StreamCancelled as cancelled:
            # Chưa chữ nào ra màn hình thì không có gì để ghi. Đây là trường
            # hợp thường gặp nhất (huỷ lúc còn đang chờ token đầu), và ghi một
            # event rỗng vào đó chỉ làm log bẩn.
            if cancelled.text:
                session.append({
                    "type": "assistant",
                    "content": cancelled.text,
                    "tool_calls": [],
                    # Để log không khai man rằng model đã nói xong câu đó.
                    "interrupted": True,
                })
            raise
        except StreamInterrupted as broken:
            if broken.text:
                session.append({"type": "assistant_attempt", "content": broken.text})
            raise
        session.append({
            "type": "assistant",
            "content": reply.text,
            "tool_calls": [
                {"id": c.id, "name": c.name, "arguments": c.arguments_json}
                for c in reply.tool_calls
            ],
            # Số đo của request vừa rồi, ghi vào log như một phần của sự thật.
            # Loop chỉ CHUYỂN nó, không diễn giải: nó không biết ngân sách là
            # bao nhiêu, và cũng không nên biết.
            "prompt_tokens": reply.prompt_tokens,
        })

        # Không còn tool call — model đã trả lời xong. Đây là điều kiện dừng duy nhất.
        # Đối chiếu: agent.ts:470.
        if not reply.tool_calls:
            return reply.text

        # Huỷ (Ctrl-C) hay gặp nhất ở đây, vì tool là phần chạy lâu nhất.
        # Lúc đó assistant event ĐÃ vào log với đủ tool_calls, nhưng những call
        # chưa chạy thì chưa có result -> log không hợp lệ để gửi lại.
        # Vá trước khi cho exception bay lên, rồi vẫn re-raise: huỷ vẫn là huỷ.
        # Đối chiếu: appendSkippedToolCall() ở tool-calls.ts:250.
        try:
            for call in reply.tool_calls:
                result = await tools.execute(call.name, call.arguments_json)
                session.append({
                    "type": "tool_result",
                    "call_id": call.id,
                    "name": call.name,
                    "content": result.content,
                    "is_error": result.is_error,
                })
        except asyncio.CancelledError:
            session.abort_pending_tool_calls()
            raise

    raise MaxStepsExceeded(f"turn không kết thúc sau {max_steps} step")
