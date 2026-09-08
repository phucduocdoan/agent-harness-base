"""Entry point: `python3 -m mini_harness --azure`.

Chỉ chứa phần bọc process — thứ không thuộc về logic nào cả: đổi exit code của
Ctrl-C, và trả stdin về blocking mode. Để riêng khỏi `app.py` vì `app.main()`
là một coroutine trả về int, test gọi được; còn phần dưới đây thì không.
"""

from __future__ import annotations

import asyncio

from mini_harness.app import main
from mini_harness.cli.terminal import restore_stdin

if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except KeyboardInterrupt:
        # asyncio.run raise lại KeyboardInterrupt sau khi task đã dừng. Không
        # có gì phải dọn ở đây nữa, chỉ tránh in traceback cho một cú Ctrl-C.
        raise SystemExit(130) from None
    finally:
        restore_stdin()
