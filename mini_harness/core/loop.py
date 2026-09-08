"""Agent loop: control flow của một turn.

File này KHÔNG import bất kỳ implementation nào (không openai, không jsonschema).
Nó chỉ khai báo Protocol cho ba thứ nó cần — LLM, Tools, Session — rồi lái vòng lặp.
Đổi provider LLM hay thêm tool đều không cần sửa file này.

Đối chiếu harness thật: packages/core/agent-loop/src/agent.ts
"""

from __future__ import annotations

import asyncio
from typing import Any, Protocol

from mini_harness.core.types import AssistantMessage, ToolResult

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


class Session(Protocol):
    """Append-only event log của một cuộc hội thoại."""

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
) -> str:
    """Chạy một turn tới khi model trả lời không kèm tool call.

    Một turn = nhiều step. Một step = một model call + các tool call của nó.

    `max_steps` là cần thiết, không phải phòng xa: khi args sai schema, model
    nhận lỗi và thử lại — nếu nó thử mãi thì turn không bao giờ dừng.
    Harness thật đặt việc này ở plugin riêng (packages/guard/).

    Returns:
        Text của lượt trả lời cuối.

    Raises:
        MaxStepsExceeded: hết `max_steps` mà model vẫn còn gọi tool.
    """
    session.append({"type": "user", "content": user_input})

    for _step in range(max_steps):
        reply = await llm.generate(
            system=system,
            messages=session.to_messages(),
            tools=tools.schemas(),
        )
        session.append({
            "type": "assistant",
            "content": reply.text,
            "tool_calls": [
                {"id": c.id, "name": c.name, "arguments": c.arguments_json}
                for c in reply.tool_calls
            ],
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
