"""Phần dùng chung của mọi provider nói wire format OpenAI.

Đây là biên (seam) quan trọng nhất của harness: `core/loop.py` không biết
provider nào, `tools/registry.py` cũng không. Chỉ package này biết wire format.
Thêm provider thứ ba = thêm một file cạnh file này, không sửa gì ở nơi khác.

Đối chiếu harness thật: packages/llm/llm-deepseek/src/serialize.ts + translate.ts
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from mini_harness.core.types import (
    AssistantMessage,
    StreamCancelled,
    StreamInterrupted,
    ToolCall,
)
from mini_harness.llm.retry import OnRetry, with_retry


def to_wire_tools(schemas: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """ToolSchema trung lập -> wire format của OpenAI/DeepSeek.

    Đối chiếu: serialize.ts:349.
    """
    return [{"type": "function", "function": schema} for schema in schemas]


# ------------------------------------------------------------------ streaming
# Loop cần một AssistantMessage HOÀN CHỈNH mới quyết định được (còn tool call
# hay không), nên stream phải gộp lại trước khi generate() trả về. Vì vậy
# streaming KHÔNG đổi contract của loop: nó chỉ thêm một kênh hiển thị chạy
# song song. `core/loop.py` không biết provider có stream hay không.
# Đối chiếu: AssistantStreamAccumulator ở core/agent-loop/src/assistant-stream.ts.

# Nhận từng mảnh text ngay khi nó tới. Chỉ để hiển thị — không ai quyết định gì
# dựa trên nó.
OnText = Callable[[str], None]


class StreamAccumulator:
    """Gộp delta của một stream thành đúng một AssistantMessage."""

    def __init__(self, on_text: OnText | None = None) -> None:
        self._on_text = on_text
        self._text: list[str] = []
        # index -> mảnh đang gom. Dùng dict thay vì list vì delta có thể tới
        # không theo thứ tự index.
        self._calls: dict[int, dict[str, str]] = {}
        self._prompt_tokens: int | None = None

    def feed(self, chunk: Any) -> None:
        # Usage tới ở MỘT chunk riêng gần cuối stream, và chunk đó có `choices`
        # rỗng — nên nó phải được đọc trước vòng lặp dưới, không phải trong.
        # `getattr` vì chunk thường không có field này; `if usage` vì một số
        # provider gửi `usage=None` ở mọi chunk cho tới chunk cuối.
        usage = getattr(chunk, "usage", None)
        if usage is not None:
            self._prompt_tokens = usage.prompt_tokens
        # `choices` rỗng là hợp lệ: Azure gửi một chunk đầu chỉ chứa kết quả
        # content filter của prompt.
        for choice in chunk.choices or ():
            delta = getattr(choice, "delta", None)
            if delta is None:
                continue
            if delta.content:
                self._text.append(delta.content)
                if self._on_text is not None:
                    self._on_text(delta.content)
            for call in delta.tool_calls or ():
                # Khoá theo `index`, KHÔNG theo `id`: `id` và `name` chỉ tới ở
                # delta đầu của mỗi call, các delta sau chỉ có index + một mảnh
                # arguments. Đối chiếu: translate.ts:177.
                slot = self._calls.setdefault(call.index, {"id": "", "name": "", "arguments": ""})
                if call.id:
                    slot["id"] = call.id
                function = getattr(call, "function", None)
                if function is not None:
                    if function.name:
                        slot["name"] = function.name
                    if function.arguments:
                        slot["arguments"] += function.arguments

    @property
    def text(self) -> str:
        """Phần text ĐÃ đi qua `on_text`, tức đã nằm trên màn hình người dùng.

        Đọc được cả khi stream chưa kết thúc — và đó là lý do nó tồn tại: lúc
        stream vỡ thì `finish()` không còn nghĩa (message đó không hoàn chỉnh,
        không ai được dùng nó như một lượt trả lời), nhưng câu hỏi "người dùng
        đã đọc tới đâu" thì vẫn phải trả lời được.
        """
        return "".join(self._text)

    def finish(self) -> AssistantMessage:
        """Chốt stream thành message.

        `arguments` vẫn là string thô, KHÔNG parse ở đây — gom xong có thể ra
        JSON hỏng, và đó là lỗi model phải tự sửa qua error result của registry.
        """
        return AssistantMessage(
            text="".join(self._text),
            tool_calls=tuple(
                ToolCall(id=slot["id"], name=slot["name"], arguments_json=slot["arguments"])
                for _index, slot in sorted(self._calls.items())
            ),
            prompt_tokens=self._prompt_tokens,
        )


async def generate_streamed(
    client: Any,
    model: str,
    *,
    system: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    on_text: OnText | None,
    on_retry: OnRetry | None = None,
) -> AssistantMessage:
    """Một model call ở chế độ stream. Dùng chung cho mọi provider OpenAI-wire.

    Thử lại nằm ở ĐÂY, bọc quanh một lần gọi, chứ không ở `core/loop.py` bọc
    quanh cả turn. Một turn đã chạy tool thì chạy lại là chạy lại cả tool —
    ghi file hai lần, gửi request hai lần. Còn một lần `create()` hỏng trước
    token đầu thì chưa để lại dấu vết nào ở bất cứ đâu. Thử lại chỉ an toàn ở
    mức nhỏ nhất đó.

    `lambda` chứ không phải coroutine dựng sẵn: mỗi lần thử phải là một
    `StreamAccumulator` mới tinh. Dùng lại accumulator cũ là nối text của lần
    hỏng vào text của lần thành công.
    """
    return await with_retry(
        lambda: _one_attempt(
            client, model, system=system, messages=messages, tools=tools, on_text=on_text
        ),
        on_retry=on_retry,
    )


async def _one_attempt(
    client: Any,
    model: str,
    *,
    system: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    on_text: OnText | None,
) -> AssistantMessage:
    """Đúng một lần gửi request và gộp stream về.

    Hai `except` ở cuối là chỗ hàm này trả lời một câu mà không ai khác trả lời
    được: **đã có chữ nào ra màn hình chưa.** Chỉ accumulator biết, vì chỉ nó
    cầm `on_text`. Và câu trả lời đó quyết định hai thứ ở hai tầng khác nhau:
    `with_retry` có được thử lại không, và `run_turn` có phải ghi lại đoạn text
    mồ côi không. Cả hai đọc nó qua KIỂU exception, không qua cờ.

    Chưa phát chữ nào thì ném lại nguyên exception gốc, không bọc: lúc đó
    không có gì mồ côi để mang theo, mà `APIConnectionError` hiện nguyên tên
    của nó ở màn hình người dùng vẫn dễ hiểu hơn một lớp bọc của bản này.
    """
    accumulator = StreamAccumulator(on_text)
    try:
        stream = await client.chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": system}, *messages],
            stream=True,
            # Xin API báo lại số token nó THẬT SỰ đọc. Ở chế độ stream phải xin
            # tường minh, vì mặc định stream không trả usage. Số này là cái neo
            # duy nhất để sửa sai số của bộ đoán ở tầng trên; xem
            # `Session._calibrate`.
            stream_options={"include_usage": True},
            # Gửi `tools=[]` là lỗi ở một số provider; không có tool thì bỏ hẳn key.
            **({"tools": to_wire_tools(tools)} if tools else {}),
        )
        # `async with` để Ctrl-B giữa stream đóng hẳn connection thay vì bỏ treo.
        async with stream as events:
            async for chunk in events:
                accumulator.feed(chunk)
    except asyncio.CancelledError:
        if accumulator.text:
            raise StreamCancelled(accumulator.text) from None
        raise
    except Exception as error:
        if accumulator.text:
            raise StreamInterrupted(accumulator.text, error) from error
        raise
    return accumulator.finish()
