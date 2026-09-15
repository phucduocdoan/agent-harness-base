"""Provider replay: phát lại assistant event từ một log cũ. Không cần API key.

Cặp còn lại của `Session.resume`. `resume` đọc log để nói tiếp; `replay` đọc log
để nói LẠI — cùng một dữ liệu, hai cách dùng, và cả hai chỉ có được vì log lưu
event chứ không lưu message list.

Đối chiếu harness thật: packages/test-support/llm-replay (1344 dòng, "keyless
snapshot-test LLM replay" — dựng kịch bản model call từ session log rồi gắn vào
một phiên mới).

Giới hạn cần biết trước khi tin nó: log chỉ ghi những gì ĐÃ xảy ra. Một request
từng timeout hay từng treo không để lại event nào, nên replay thuần từ log không
bao giờ dựng lại được đường lỗi. Harness thật giải bằng file override riêng —
"Throw and hang cases require an explicit override because a session log cannot
reconstruct them alone."
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from mini_harness.core.types import AssistantMessage, ToolCall
from mini_harness.llm.stream import OnText


class ReplayExhausted(RuntimeError):
    """Phiên mới gọi model nhiều lần hơn phiên đã ghi."""


class ReplayLLM:
    """Đọc log một lần trong __init__, mỗi `generate` trả về lượt kế tiếp."""

    def __init__(self, log_path: Path, *, on_text: OnText | None = None) -> None:
        self._log_path = Path(log_path)
        self._on_text = on_text
        self._script = [
            _to_message(event)
            for event in _read_events(self._log_path)
            # Chỉ `assistant` là lượt nói của model. `user` và `tool_result` là
            # phần phiên MỚI tự sinh ra; phát lại chúng là ghi đè lịch sử mới
            # bằng lịch sử cũ.
            if event["type"] == "assistant"
        ]
        self._sent = 0

    async def generate(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> AssistantMessage:
        """Bỏ qua cả ba tham số, có chủ ý.

        Kịch bản đã cố định từ lúc ghi: `system` không nằm trong log (nó được
        ghép vào ở stream.py, ngoài session), còn `messages`/`tools` chỉ ảnh
        hưởng tới câu trả lời khi có model thật để mà ảnh hưởng. Implement một
        Protocol không bắt buộc phải dùng hết tham số của nó — nhưng bắt buộc
        phải nói rõ vì sao không dùng.
        """
        if self._sent >= len(self._script):
            # Ném, không trả về message rỗng: hết kịch bản nghĩa là phiên mới đã
            # đi lệch phiên cũ, và đó đúng là thứ cần biết ngay. Trả về êm thấm
            # sẽ che mất chính cái mà replay sinh ra để phát hiện.
            raise ReplayExhausted(
                f"{self._log_path} chỉ có {len(self._script)} lượt assistant, "
                f"nhưng phiên này gọi model lần thứ {self._sent + 1}"
            )
        reply = self._script[self._sent]
        self._sent += 1
        # Gọi một phát cả câu, KHÔNG cắt nhỏ giả vờ streaming: log không lưu
        # ranh giới delta, nên mọi cách cắt đều là bịa. Cái đang phát lại là nội
        # dung, không phải nhịp gõ.
        if self._on_text is not None and reply.text:
            self._on_text(reply.text)
        return reply


def _read_events(log_path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in log_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _to_message(event: dict[str, Any]) -> AssistantMessage:
    """Event -> AssistantMessage: đi NGƯỢC chiều `Session.to_messages()`.

    `arguments` giữ nguyên dạng string, không `json.loads`: nó có thể là JSON
    hỏng do chính model sinh ra, và phát lại phải trung thực kể cả cái hỏng —
    đó mới là lúc replay đáng giá nhất.
    """
    return AssistantMessage(
        text=event["content"],
        tool_calls=tuple(
            ToolCall(id=call["id"], name=call["name"], arguments_json=call["arguments"])
            for call in event.get("tool_calls") or ()
        ),
    )
