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

    Thuần hiển thị: không ai quyết định gì dựa trên nó, và `agent_loop.py`
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


def restore_stdin() -> None:
    """Trả stdin về blocking mode trước khi thoát.

    `connect_read_pipe` đặt O_NONBLOCK lên fd 0. Trên terminal, fd đó DÙNG
    CHUNG với shell cha và cờ không tự khôi phục khi process thoát -> shell
    sau đó báo "read error: Resource temporarily unavailable". Với stdin là
    pipe thì vô hại, nên bug này chỉ lộ ra khi chạy thật trên TTY.
    """
    if _STDIN_FLAGS is not None:
        fcntl.fcntl(sys.stdin, fcntl.F_SETFL, _STDIN_FLAGS)


async def read_line() -> str:
    """Đọc một dòng stdin qua event loop, KHÔNG qua thread.

    `asyncio.to_thread(input, ...)` là cái bẫy ở đây: huỷ chỉ nhả cái `await`,
    thread vẫn nằm trong read(2), mà `asyncio.run` join executor lúc shutdown
    -> Ctrl-C ở prompt approval làm process treo vĩnh viễn. Nối stdin vào loop
    thì huỷ nhả được ngay.
    """
    global _STDIN, _STDIN_FLAGS
    if _STDIN is None:
        _STDIN_FLAGS = fcntl.fcntl(sys.stdin, fcntl.F_GETFL)
        _STDIN = asyncio.StreamReader()
        await asyncio.get_running_loop().connect_read_pipe(
            lambda: asyncio.StreamReaderProtocol(_STDIN), sys.stdin
        )
    line = await _STDIN.readline()
    if not line:
        raise EOFError("stdin đã đóng")
    return line.decode()
