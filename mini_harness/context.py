"""Environment context — những gì agent cần biết về chỗ nó đang chạy.

Không nằm trong `core/` vì nó CHẠM thế giới thật: đọc đồng hồ, liệt kê thư
mục. `core/` phải chạy được khi không có thế giới nào cả — đó là lý do test
của loop và session không cần mock gì.

Mỗi hàm ở đây trả về một section text, và `core/prompt.assemble()` ráp chúng
lại. Thêm một nguồn context mới = thêm một hàm ở đây + một dòng ở `app.py`.

Đối chiếu harness thật: packages/context/{time-context, file-reference}
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path


def time_context(now: datetime | None = None) -> str:
    """Mốc thời gian lúc phiên bắt đầu.

    Chữ "session started at" là CÓ CHỦ Ý, không phải "the current time is".
    System prompt được ráp một lần rồi giữ nguyên cả phiên để prompt cache còn
    ăn được; sau hai tiếng trò chuyện thì "bây giờ là" đã sai, còn "phiên bắt
    đầu lúc" vẫn đúng mãi. Nói đúng cái mình biết, đừng nói cái nghe tiện hơn.

    Muốn model biết giờ hiện tại thật thì đi đường khác: chèn một message vào
    lịch sử hội thoại — đúng cách harness thật làm ở `packages/context/
    time-context`, nó append `UserMessage` chứ không viết lại system prompt.
    Viết lại system prompt mỗi step là đổi prefix mỗi request, tức là cache
    miss mỗi request.
    """
    stamp = (now or datetime.now()).astimezone()
    return f"Session started at {stamp:%Y-%m-%d %H:%M %Z}."


def workspace_context(sandbox: Path, limit: int = 20) -> str:
    """Thư mục agent được ghi, và những gì đang có trong đó.

    `limit` không phải để cho đẹp: thư mục này là của người dùng, 5000 file thì
    system prompt phình ra vài trăm nghìn token — mà ngân sách trong
    `session.py` KHÔNG hề biết chuyện đó, nó chỉ đếm message list. Mọi thứ
    không nằm trong message list đều phải tự giới hạn ở chỗ nó sinh ra.
    """
    if not sandbox.is_dir():
        return f"Workspace: {sandbox} (does not exist yet; writing creates it)."
    names = sorted(entry.name for entry in sandbox.iterdir())
    listing = ", ".join(names[:limit]) if names else "(empty)"
    if len(names) > limit:
        listing += f", ... (+{len(names) - limit} more)"
    return f"Workspace: {sandbox}\nFiles currently there: {listing}"
