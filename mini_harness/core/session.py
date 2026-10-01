"""Session — event log append-only, và phép chiếu (projection) sang message list.

Đây là chỗ dễ làm sai nhất khi tự viết harness: người ta hay lưu luôn
`list[dict]` đúng format OpenAI rồi append vào đó. Harness thật KHÔNG làm vậy:
`session.append('tool/result', ...)` ghi một EVENT vào log, còn message list gửi
lên model là thứ được CHIẾU RA từ log (packages/core/session/src/surface.ts).

Tách hai thứ này ra mới có: resume, replay, checkpoint, và đổi cách render
history mà không mất dữ liệu gốc.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Result giả ghi cho tool_call chưa kịp chạy. Text lấy đúng harness thật
# (tool-calls.ts:253) vì nó cũng là model-facing text.
ABORTED_BEFORE_DISPATCH = "Error: tool call aborted before dispatch"


@dataclass(slots=True)
class Session:
    """Log append-only các event của một hội thoại.

    Hiện thực Session Protocol trong core/loop.py. Không validate event: agent
    loop là caller duy nhất và nó tự biết mình ghi gì.
    """

    events: list[dict[str, Any]] = field(default_factory=list)
    # Có path thì mỗi event được ghi ngay thành một dòng JSONL. Append-only
    # thật: crash giữa turn vẫn còn nguyên phần đã ghi. Ghi cả cục lúc kết thúc
    # thì mất sạch — mà crash giữa turn là chuyện thường của agent.
    log_path: Path | None = None
    # Nơi UI nghe để hiển thị. Session là nguồn sự thật duy nhất của mọi thứ
    # model thấy, nên echo lên màn hình phải PHÁI SINH từ đây — không phải từ
    # agent loop (loop không sở hữu việc hiển thị) và cũng không phải từ tool
    # registry (nó không biết mình đang chạy trong phiên nào).
    on_event: Callable[[dict[str, Any]], None] | None = None
    # Ngân sách token cho message list. None = không cắt gì (mặc định, vì
    # `core/` không có quyền đoán cửa sổ của model nào). Chủ sở hữu con số này
    # là app.py — nó mới biết đang chạy provider nào.
    max_tokens: int | None = None

    def append(self, event: dict[str, Any]) -> None:
        self.events.append(event)
        if self.log_path is not None:
            with self.log_path.open("a", encoding="utf-8") as log:
                log.write(json.dumps(event, ensure_ascii=False) + "\n")
        # Phát SAU khi event đã vào list và đã xuống đĩa: chỉ echo cái đã
        # commit, không bao giờ echo một thứ rồi mới ghi (hoặc ghi hỏng).
        # Đối chiếu luật "publish state only at its commit point".
        if self.on_event is not None:
            self.on_event(event)

    @classmethod
    def resume(cls, log_path: Path) -> Session:
        """Đọc lại log và trả về session ghi tiếp vào chính file đó.

        Log có thể kết thúc giữa turn: một `assistant` có tool_calls mà thiếu
        `tool_result` (bị kill, crash, cancel). Gửi nguyên trạng đó lên API là
        request KHÔNG HỢP LỆ — mọi tool_call bắt buộc phải có result.

        Nên ở đây phải REPAIR. Và repair bằng cách append event thật vào log,
        không phải bịa ra lúc chiếu: nhờ vậy log luôn là bản ghi trung thực của
        cái model đã thực sự nhìn thấy. Đối chiếu tool-calls.ts:8 —
        "Abort records synthetic error results for skipped calls so replay
        stays valid."
        """
        events = [
            json.loads(line)
            for line in log_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        session = cls(events=events, log_path=log_path)
        session.abort_pending_tool_calls()
        return session

    def abort_pending_tool_calls(self) -> int:
        """Ghi result giả cho mọi tool_call còn treo. Trả về số call đã vá.

        Hai chỗ gọi, cùng một lý do: (1) resume một log bị cắt giữa turn,
        (2) user huỷ giữa lúc tool đang chạy. Cả hai đều để lại log thiếu
        result, và cả hai đều làm request kế tiếp không hợp lệ nếu không vá.
        Đối chiếu: appendSkippedToolCall() ở tool-calls.ts:250.
        """
        pending = self._orphan_tool_calls()
        for call_id, name in pending:
            self.append({
                "type": "tool_result", "call_id": call_id, "name": name,
                "content": ABORTED_BEFORE_DISPATCH, "is_error": True,
            })
        return len(pending)

    def _orphan_tool_calls(self) -> list[tuple[str, str]]:
        """tool_call nào chưa có result, theo đúng thứ tự model đã gọi."""
        answered = {
            event["call_id"] for event in self.events if event["type"] == "tool_result"
        }
        return [
            (call["id"], call["name"])
            for event in self.events
            if event["type"] == "assistant"
            for call in event.get("tool_calls") or ()
            if call["id"] not in answered
        ]

    # ------------------------------------------------------- ngân sách token
    # Ước lượng THÔ, cố ý nghiêng về phía đoán thừa: đoán thiếu nghĩa là tưởng
    # còn chỗ trong khi đã tràn — đúng cái crash đang muốn tránh. Đo bằng
    # tiktoken o200k_base: tiếng Anh 4.87 ký tự/token, tiếng Việt 3.35, JSON
    # 2.53. Lấy theo trường hợp tệ nhất.
    #
    # Không import tiktoken ở đây dù nó chính xác hơn: `core/` mà biết
    # tokenizer của một provider cụ thể là phá seam. Harness thật giải bằng
    # cách khác và tốt hơn — đọc `usage` mà API TRẢ VỀ ở response trước, tức là
    # số thật chứ không phải số đoán. Muốn nâng cấp thì đi đường đó.
    _CHARS_PER_TOKEN = 2.5
    # Mỗi message còn tốn phần bao: role, delimiter của wire format.
    _TOKENS_PER_MESSAGE = 4

    def _within_budget(self) -> list[dict[str, Any]]:
        """Bỏ bớt turn cũ cho vừa `max_tokens`. KHÔNG sửa `self.events`."""
        if self.max_tokens is None:
            return self.events
        turns = _split_turns(self.events)
        # Bỏ TRỌN từng turn từ cũ nhất. Cắt lẻ từng event sẽ để lại
        # `tool_result` mồ côi không có `tool_call` đi kèm, và API từ chối
        # nguyên request — hỏng nặng hơn hẳn so với việc tràn cửa sổ.
        while len(turns) > 1 and self._estimate(turns) > self.max_tokens:
            turns.pop(0)
        # Luôn giữ turn cuối cùng kể cả khi một mình nó đã vượt ngân sách: bỏ
        # nốt thì chẳng còn gì để hỏi model. Quá cỡ ở đây là việc của tầng
        # khác (cắt nhỏ chính câu hỏi), không phải của compaction.
        return [event for turn in turns for event in turn]

    def _estimate(self, turns: list[list[dict[str, Any]]]) -> int:
        count = 0
        for turn in turns:
            for event in turn:
                text = json.dumps(event, ensure_ascii=False)
                count += int(len(text) / self._CHARS_PER_TOKEN) + self._TOKENS_PER_MESSAGE
        return count

    def to_messages(self) -> list[dict[str, Any]]:
        """Chiếu log thành message list đúng wire format của OpenAI/DeepSeek.

        Một chiều: event -> message. Không có đường ngược lại, và không sửa
        `self.events`. Gọi bao nhiêu lần cũng ra cùng kết quả.

        Có `max_tokens` thì phép chiếu này LOSSY: turn cũ bị bỏ khỏi message
        list, nhưng vẫn nằm nguyên trong log. Đó chính là lý do tách log khỏi
        projection ngay từ đầu — cắt ngữ cảnh mà không mất dữ liệu.
        """
        messages: list[dict[str, Any]] = []
        for event in self._within_budget():
            kind = event["type"]
            if kind == "user":
                messages.append({"role": "user", "content": event["content"]})
            elif kind == "assistant":
                message: dict[str, Any] = {"role": "assistant", "content": event["content"]}
                if event["tool_calls"]:
                    message["tool_calls"] = [
                        {
                            "id": call["id"],
                            "type": "function",
                            "function": {"name": call["name"], "arguments": call["arguments"]},
                        }
                        for call in event["tool_calls"]
                    ]
                messages.append(message)
            elif kind == "tool_result":
                # Wire format KHÔNG có field `is_error`. Đó là lý do tools.py
                # nhét "Error: " vào chính content: model chỉ đọc được content,
                # nên thông tin "cái này lỗi" phải nằm trong text, không phải
                # metadata. `is_error` trong event chỉ để UI/replay dùng.
                messages.append({
                    "role": "tool",
                    "tool_call_id": event["call_id"],
                    "content": event["content"],
                })
            elif kind == "agent":
                # Event METADATA: ghi vào log nhưng KHÔNG gửi cho model. Nó trả
                # lời câu "log này chạy bằng persona nào" — câu mà resume và
                # replay cần, còn model thì không (persona đã nằm sẵn trong
                # system prompt rồi, chiếu thêm lần nữa là nói hai lần).
                #
                # Đây là event đầu tiên thuộc nhóm "ghi mà không chiếu". Harness
                # thật có cả một họ như vậy: turn/start, step/start,
                # compaction/*. Nhóm này là lý do `to_messages()` phải là phép
                # CHIẾU có chọn lọc chứ không phải phép đổi dạng 1-1.
                continue
            else:
                raise ValueError(f"event type không biết: {kind!r}")
        return messages


def _split_turns(events: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Chẻ log thành từng turn. Một turn bắt đầu ở mỗi event `user`.

    Định nghĩa này làm cho "bỏ trọn một turn" không bao giờ tạo ra mồ côi:
    `assistant` và `tool_result` luôn nằm cùng nhóm với `user` đã sinh ra chúng.
    """
    turns: list[list[dict[str, Any]]] = []
    for event in events:
        if event["type"] == "user" or not turns:
            turns.append([])
        turns[-1].append(event)
    return turns
