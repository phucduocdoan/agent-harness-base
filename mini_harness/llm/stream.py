"""Phần dùng chung của mọi provider nói wire format OpenAI.

Đây là biên (seam) quan trọng nhất của harness: `core/loop.py` không biết
provider nào, `tools/registry.py` cũng không. Chỉ package này biết wire format.
Thêm provider thứ ba = thêm một file cạnh file này, không sửa gì ở nơi khác.

Đối chiếu harness thật: packages/llm/llm-deepseek/src/serialize.ts + translate.ts
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from mini_harness.core.types import AssistantMessage, ToolCall


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

    def feed(self, chunk: Any) -> None:
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
        )


async def generate_streamed(
    client: Any,
    model: str,
    *,
    system: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    on_text: OnText | None,
) -> AssistantMessage:
    """Một model call ở chế độ stream. Dùng chung cho mọi provider OpenAI-wire."""
    accumulator = StreamAccumulator(on_text)
    stream = await client.chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": system}, *messages],
        stream=True,
        # Gửi `tools=[]` là lỗi ở một số provider; không có tool thì bỏ hẳn key.
        **({"tools": to_wire_tools(tools)} if tools else {}),
    )
    # `async with` để Ctrl-B giữa stream đóng hẳn connection thay vì bỏ treo.
    async with stream as events:
        async for chunk in events:
            accumulator.feed(chunk)
    return accumulator.finish()
