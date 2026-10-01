"""Những record đi qua biên giữa loop và ba service.

Tách riêng khỏi `loop.py` vì có hai consumer: loop (nó là caller) và
`tools/registry.py` (nó trả về `ToolResult`). Bắt registry import từ `loop` là
sai hướng phụ thuộc — registry không biết gì về loop.

Protocol thì KHÔNG ở đây: chỉ `run_turn` dùng chúng, còn implementation không
import lại bao giờ (Protocol là structural typing), nên chúng thuộc về loop.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

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
    # Số token của REQUEST đã sinh ra lượt trả lời này, do API tự báo. Không
    # phải thuộc tính của câu trả lời, nhưng đây là chỗ duy nhất nó về được:
    # một lần `generate` trả về đúng một message.
    #
    # `None` là hợp lệ và phải chịu được: provider có thể không báo usage, và
    # ReplayLLM thì không có request nào để mà đo. Thiếu số thật thì tầng trên
    # quay về ước lượng, không crash.
    prompt_tokens: int | None = None


@dataclass(frozen=True, slots=True)
class ToolResult:
    """Kết quả một tool call, đã ở dạng model đọc được.

    `is_error=True` KHÔNG phải lỗi hệ thống — nó là một message hợp lệ gửi lại
    cho model để model tự sửa (args sai schema, tool fail, bị deny...).
    """

    content: str
    is_error: bool = False



@dataclass(frozen=True, slots=True)
class CompactionPlan:
    """Dự định nén: nén tới đâu, và gửi gì đi để xin bản tóm tắt.

    Session dựng ra record này vì chỉ Session mới ĐO được (ngân sách, phép
    chiếu, phép neo token). `core/compaction.py` tiêu thụ nó vì chỉ nó mới GỌI
    được model. Tách ra làm hai chỗ để không bên nào phải biết việc của bên
    kia — và record này là đúng cái đi qua biên đó, nên nó thuộc về file này.

    `messages` đã ở wire format và CỐ Ý là tiền tố byte-for-byte của request
    gần nhất: cùng thứ tự, cùng nội dung, chỉ ngắn hơn. Nhờ vậy prompt cache
    của provider dùng lại được tới sát message instruction. Đối chiếu
    compaction-basic: "Summarization reuses the provider's warm prefix".
    """

    # Số event ở ĐẦU log mà bản tóm tắt sẽ đứng thay.
    covers: int
    messages: list[dict[str, Any]]
