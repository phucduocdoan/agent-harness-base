"""Provider DeepSeek."""

from __future__ import annotations

import os
from typing import Any

from mini_harness.core.types import AssistantMessage
from mini_harness.llm.stream import OnText, generate_streamed


class DeepSeekLLM:
    """Gọi DeepSeek API thật qua openai SDK (DeepSeek dùng chung wire format).

    API key đọc từ env `DEEPSEEK_API_KEY`, không bao giờ hardcode.
    """

    def __init__(
        self,
        *,
        model: str = "deepseek-chat",
        api_key: str | None = None,
        on_text: OnText | None = None,
    ) -> None:
        from openai import AsyncOpenAI  # import trong hàm: chạy provider khác thì không cần openai

        key = api_key or os.environ.get("DEEPSEEK_API_KEY")
        if not key:
            raise RuntimeError("thiếu DEEPSEEK_API_KEY")
        self._model = model
        self._on_text = on_text
        self._client = AsyncOpenAI(api_key=key, base_url="https://api.deepseek.com")

    async def generate(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> AssistantMessage:
        return await generate_streamed(
            self._client, self._model,
            system=system, messages=messages, tools=tools, on_text=self._on_text,
        )
