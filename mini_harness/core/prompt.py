"""Prompt assembly: ráp system prompt từ nhiều nguồn, theo một thứ tự cố định.

Một hằng chuỗi phẳng là đủ khi chỉ có một agent. Từ hai agent trở lên thì
không: mỗi agent cần persona riêng nhưng dùng chung phần mô tả môi trường, và
phần chung đó không nên bị chép hai lần.

File này THUẦN — không đọc giờ, không đọc đĩa, không biết agent nào tồn tại.
Nguồn của các section nằm ở `context.py` (môi trường) và `profiles.py`
(persona); đây chỉ là chỗ ráp. Tách vậy để `core/` vẫn chạy được mà không cần
thế giới thật, đúng như `loop.py` và `session.py`.

Đối chiếu harness thật: packages/core/system-prompt
"""

from __future__ import annotations


def assemble(*sections: str | None) -> str:
    """Ghép các section theo đúng thứ tự truyền vào, bỏ qua cái rỗng.

    Thứ tự là hợp đồng của caller chứ không phải của hàm này, và nó có hệ quả
    thật: prompt cache bám vào PREFIX ổn định của prompt, nên phần ít đổi nhất
    phải đi trước. Persona (cố định theo agent) trước, mô tả môi trường (đổi
    theo phiên) sau.

    `None` được chấp nhận để caller viết thẳng `x if dieu_kien else None` mà
    không phải dựng list trung gian — một section vắng mặt là chuyện thường:
    agent không có quyền ghi file thì không cần biết thư mục ghi nằm ở đâu.
    """
    return "\n\n".join(
        section.strip() for section in sections if section and section.strip()
    )
