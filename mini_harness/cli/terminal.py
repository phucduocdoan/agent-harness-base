"""Mọi thứ dính tới terminal thật: đọc stdin, in text stream, hỏi duyệt.

Gom vào một chỗ vì cả ba đều là I/O trên fd 0/1 và đều là chỗ dễ sai nhất khi
huỷ (Ctrl-C). Không file nào trong `core/` biết module này tồn tại.
"""

from __future__ import annotations

import asyncio
import fcntl
import json
import sys
from typing import Any

class TerminalStream:
    """In text của model ra ngay khi từng mảnh tới.

    Thuần hiển thị: không ai quyết định gì dựa trên nó, và `core/loop.py`
    không biết nó tồn tại. Nhãn `assistant: ` in lười — bước nào model chỉ gọi
    tool mà không nói gì thì không in nhãn rỗng.
    """

    def __init__(self) -> None:
        self._open = False

    def __call__(self, fragment: str) -> None:
        if not self._open:
            print("assistant: ", end="", flush=True)
            self._open = True
        print(fragment, end="", flush=True)

    def close(self) -> None:
        """Đóng dòng đang in dở, nếu có."""
        if self._open:
            print()
            self._open = False


async def ask_terminal(name: str, args: dict[str, Any]) -> str | None:
    """Kênh approval của CLI này. Trả None = cho phép, string = lý do từ chối."""
    # In lại args ở đây là CÓ CHỦ Ý dù `on_event` vừa echo tool call: echo in
    # `arguments` THÔ từ model, còn dòng này in args ĐÃ VALIDATE — đúng cái sẽ
    # chạy. Hai thứ có thể khác nhau (JSON trùng key: raw hiện cả hai, parse chỉ
    # giữ cái sau). User phải duyệt cái SẼ CHẠY, không phải cái model gõ ra.
    print(f"  duyệt: {name}({json.dumps(args, ensure_ascii=False)})")
    print("  cho phép? [y/N] ", end="", flush=True)
    answer = await read_line()
    return None if answer.strip().lower() in {"y", "yes"} else "user declined"


_STDIN: asyncio.StreamReader | None = None
_STDIN_FLAGS: int | None = None
# fd SỐ NGUYÊN, không phải object `sys.stdin`: xem `restore_stdin`.
_STDIN_FD: int | None = None


def restore_stdin() -> None:
    """Trả stdin về blocking mode trước khi thoát.

    `connect_read_pipe` đặt O_NONBLOCK lên fd 0. Trên terminal, fd đó DÙNG
    CHUNG với shell cha và cờ không tự khôi phục khi process thoát -> shell
    sau đó báo "read error: Resource temporarily unavailable". Với stdin là
    pipe thì vô hại, nên bug này chỉ lộ ra khi chạy thật trên TTY.

    Phải dùng fd số nguyên, KHÔNG dùng `sys.stdin`: khi stdin là pipe, sau khi
    đọc xong dòng cuối thì reader callback vẫn còn đăng ký trên fd 0, epoll
    (level-triggered) tiếp tục báo readable vì đã EOF, nên event loop tự chạy
    `_read_ready` một vòng nữa, đọc `b''`, và transport tự đóng — đóng luôn
    chính object `sys.stdin` đã truyền vào `connect_read_pipe`
    (asyncio/unix_events.py: `_call_connection_lost` -> `self._pipe.close()`).
    Tất cả xảy ra SAU khi `main()` return. May là CPython mở sys.stdin với
    `closefd=False`, nên đóng object không đóng fd 0 ở tầng OS: cờ vẫn cần trả
    lại, và trả qua số fd thì vẫn được, còn qua object thì `ValueError`.
    """
    if _STDIN_FLAGS is not None:
        fcntl.fcntl(_STDIN_FD, fcntl.F_SETFL, _STDIN_FLAGS)


async def read_line() -> str:
    """Đọc một dòng stdin qua event loop, KHÔNG qua thread.

    `asyncio.to_thread(input, ...)` là cái bẫy ở đây: huỷ chỉ nhả cái `await`,
    thread vẫn nằm trong read(2), mà `asyncio.run` join executor lúc shutdown
    -> Ctrl-C ở prompt approval làm process treo vĩnh viễn. Nối stdin vào loop
    thì huỷ nhả được ngay.
    """
    global _STDIN, _STDIN_FLAGS, _STDIN_FD
    if _STDIN is None:
        _STDIN_FD = sys.stdin.fileno()
        _STDIN_FLAGS = fcntl.fcntl(_STDIN_FD, fcntl.F_GETFL)
        _STDIN = asyncio.StreamReader()
        await asyncio.get_running_loop().connect_read_pipe(
            lambda: asyncio.StreamReaderProtocol(_STDIN), sys.stdin
        )
    line = await _STDIN.readline()
    if not line:
        raise EOFError("stdin đã đóng")
    return line.decode()
