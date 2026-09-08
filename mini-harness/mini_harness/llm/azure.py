"""Provider Azure OpenAI."""

from __future__ import annotations

import os
from typing import Any

from mini_harness.core.types import AssistantMessage
from mini_harness.llm.stream import OnText, generate_streamed


class AzureLLM:
    """Gọi Azure OpenAI thật.

    Khác DeepSeekLLM đúng ở phần dựng client: Azure định danh model bằng
    DEPLOYMENT NAME (tên bạn đặt khi deploy), không phải tên model, và bắt buộc
    có `api_version`. Từ chỗ `generate()` trở đi thì giống hệt — cùng wire format.

    Đó là ý nghĩa của seam này: thêm provider thứ ba chỉ thêm code ở file này.
    """

    def __init__(
        self,
        *,
        deployment: str | None = None,
        api_key: str | None = None,
        endpoint: str | None = None,
        api_version: str | None = None,
        on_text: OnText | None = None,
    ) -> None:
        from openai import AsyncAzureOpenAI

        key = api_key or os.environ.get("AZURE_OPENAI_API_KEY")
        url = endpoint or os.environ.get("AZURE_OPENAI_ENDPOINT")
        version = api_version or os.environ.get("AZURE_API_VERSION")
        name = deployment or os.environ.get("AZURE_OPENAI_DEPLOYMENT")
        missing = [
            label for label, value in (
                ("AZURE_OPENAI_API_KEY", key), ("AZURE_OPENAI_ENDPOINT", url),
                ("AZURE_API_VERSION", version), ("AZURE_OPENAI_DEPLOYMENT", name),
            ) if not value
        ]
        if missing:
            raise RuntimeError(f"thiếu {', '.join(missing)}")
        self._deployment = name
        self._on_text = on_text
        self._client = AsyncAzureOpenAI(api_key=key, azure_endpoint=url, api_version=version)

    async def generate(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> AssistantMessage:
        return await generate_streamed(
            self._client, self._deployment,
            system=system, messages=messages, tools=tools, on_text=self._on_text,
        )
