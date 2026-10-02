"""Fake dùng chung cho nhiều test file.

Nằm trong tests/ chứ không phải trong package vì nó không có consumer
production nào — harness thật giải bài "chạy không cần API key" bằng
recorded-session replay, không bằng fake provider ship kèm.
"""

from __future__ import annotations

from typing import Any

from mini_harness.core.types import AssistantMessage
from mini_harness.llm.stream import to_wire_tools


class FakeLLM:
    """Model giả, chạy theo kịch bản định trước.

    KHÔNG phải mock của HTTP API — nó là kịch bản HÀNH VI của model. Nhờ vậy mới
    viết được test cho thứ quan trọng nhất: "model trả args sai schema, đọc
    error result, rồi tự sửa" (success criterion #2). Với API thật thì hành vi
    đó không deterministic nên không test được.
    """

    def __init__(self, script: list[AssistantMessage]) -> None:
        self._script = list(script)
        # Lưu lại request để test assert được model đã NHÌN THẤY gì —
        # nhất là error result có thực sự tới tay model hay không.
        self.requests: list[dict[str, Any]] = []

    async def generate(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> AssistantMessage:
        self.requests.append({"system": system, "messages": messages, "tools": to_wire_tools(tools)})
        if not self._script:
            raise AssertionError(
                f"FakeLLM hết kịch bản ở request thứ {len(self.requests)} — "
                "loop gọi nhiều lần hơn dự kiến"
            )
        return self._script.pop(0)


class StreamingFakeLLM(FakeLLM):
    """FakeLLM nhưng CÓ đẩy text qua `on_text`, đúng như provider thật.

    Tồn tại vì một lỗi chỉ hiện ra khi kênh hiển thị có thật: `FakeLLM` không
    đẩy gì ra `on_text`, nên nó không phân biệt nổi "text đi ra màn hình" với
    "text chỉ về tới caller". Mà đó đúng là chỗ phép nén từng rò ra terminal.
    Đối chiếu `StreamAccumulator.feed` ở llm/stream.py: mảnh `delta.content`
    nào cũng đi qua `on_text` trước khi được gộp lại.
    """

    def __init__(self, script: list[AssistantMessage], on_text: Any = None) -> None:
        super().__init__(script)
        self._on_text = on_text

    async def generate(self, **kwargs: Any) -> AssistantMessage:
        reply = await super().generate(**kwargs)
        if self._on_text is not None and reply.text:
            self._on_text(reply.text)
        return reply
