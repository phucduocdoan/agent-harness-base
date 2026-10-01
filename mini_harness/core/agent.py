"""AgentProfile — một "loại agent" là DỮ LIỆU, không phải một class con.

Phản xạ đầu tiên khi cần research agent và tutor agent là viết
`class TutorAgent(BaseAgent)`. Harness thật không làm vậy. Trong
packages/preset/agent-presets, một preset là:

    readonly id: string        // addressed by id everywhere
    readonly content: string   // "The composition exactly as stored"

Tức là một bản cấu hình có tên, không phải một nhánh code. Khác biệt không
phải chuyện thẩm mỹ: kế thừa nghĩa là mỗi agent mới thêm một đường chạy mới,
mà mỗi đường chạy mới là một chỗ để bug trốn — agent thứ ba sẽ là đường thứ ba
chưa ai test. Dữ liệu thì giữ nguyên MỘT đường chạy cho mọi agent: thêm agent
là thêm một giá trị, không thêm dòng logic nào.

Phép thử của thiết kế này: thêm một loại agent mà không sửa `core/loop.py`,
`core/session.py` hay `tools/registry.py` một dòng nào.

Đối chiếu harness thật: packages/core/agent + packages/preset/agent-presets
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AgentProfile:
    """Mô tả đủ để dựng một agent: nó là ai, và nó được cầm những gì.

    `frozen` có chủ ý: profile là hằng cấu hình, sửa được lúc chạy thì câu
    "log này chạy bằng agent nào" không còn trả lời chắc chắn được nữa.

    Cố tình KHÔNG có ở đây: `model` và `max_steps`. Cả hai đều đã có chủ sở
    hữu (provider chọn ở `app.py`, step limit mặc định ở `run_turn`), và thêm
    field chỉ để "cho đầy đủ" là đoán trước nhu cầu chưa có. Khi nào có tutor
    thật sự cần ít step hơn thì thêm — lúc đó mới biết thêm đúng cái gì.
    """

    #: Tên dùng ở cờ `--agent` và ghi vào log.
    name: str
    #: Phần prose model-facing. Đây là chỗ DUY NHẤT phân biệt hai agent về hành vi.
    persona: str
    #: Tên các tool agent này được cầm. Tuple rỗng = không tool nào, hợp lệ.
    tools: tuple[str, ...]
