"""Những record đi qua biên giữa loop và ba service.

Tách riêng khỏi `loop.py` vì có hai consumer: loop (nó là caller) và
`tools/registry.py` (nó trả về `ToolResult`). Bắt registry import từ `loop` là
sai hướng phụ thuộc — registry không biết gì về loop.

Protocol thì KHÔNG ở đây: chỉ `run_turn` dùng chúng, còn implementation không
import lại bao giờ (Protocol là structural typing), nên chúng thuộc về loop.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Những gì đi qua biên giữa loop và ba service. Loop sở hữu các type này vì loop
# là consumer; implementation chỉ cần khớp cấu trúc.


@dataclass(frozen=True, slots=True)
class ToolCall:
    """Một tool call do model sinh ra.

    `arguments_json` là STRING thô từ model, chưa parse, có thể là JSON hỏng.
    Loop không parse nó — việc đó thuộc tool registry (xem `Tools.execute`).
    Đối chiếu: parseArguments() ở tool-calls.ts:105.
    """

    id: str
    name: str
    arguments_json: str


@dataclass(frozen=True, slots=True)
class AssistantMessage:
    """Một lượt trả lời của model."""

    text: str = ""
    tool_calls: tuple[ToolCall, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class ToolResult:
    """Kết quả một tool call, đã ở dạng model đọc được.

    `is_error=True` KHÔNG phải lỗi hệ thống — nó là một message hợp lệ gửi lại
    cho model để model tự sửa (args sai schema, tool fail, bị deny...).
    """

    content: str
    is_error: bool = False

