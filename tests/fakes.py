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
