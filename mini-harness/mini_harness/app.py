"""Wiring: chỗ DUY NHẤT biết đủ mọi implementation.

Nhìn import của file này là biết harness gồm những gì — và đó chính là công
việc của nó. Mọi file khác chỉ biết Protocol hoặc biết đúng láng giềng của nó:
`core/loop.py` không biết Azure tồn tại, `tools/write_file.py` không biết
sandbox nằm ở đâu, `cli/chat.py` không biết system prompt viết gì. Tất cả
những quyết định đó tập trung ở đây.

Đối chiếu harness thật: cordis.yml + packages/cli/src/wire.ts
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from mini_harness.cli.chat import chat, echo_tool_activity, print_log
from mini_harness.cli.terminal import TerminalStream, ask_terminal
from mini_harness.core.loop import run_turn
from mini_harness.core.session import Session
from mini_harness.llm.azure import AzureLLM
from mini_harness.llm.deepseek import DeepSeekLLM
from mini_harness.tools.calculator import calculator_tool
from mini_harness.tools.registry import Approver, ToolRegistry
from mini_harness.tools.write_file import write_file_tool

# Thư mục gốc của project (package nằm trong nó).
ROOT = Path(__file__).resolve().parent.parent
SANDBOX = ROOT / "sandbox"

SYSTEM = (
    "You are a helpful assistant. Use the calculator tool for any arithmetic "
    "instead of computing it yourself. Answer in Vietnamese."
)

# Provider nào tồn tại: khai báo ở đây, một chỗ duy nhất.
PROVIDERS = {
    "--azure": AzureLLM,
    "--deepseek": DeepSeekLLM,
}


def build_tools(approver: Approver | None = None) -> ToolRegistry:
    """Đăng ký tool. Liệt kê tay, KHÔNG auto-discover.

    Quét thư mục để tự nạp tool nghe tiện hơn nhưng phá đúng cái tính chất
    đang giữ: đọc một file là biết harness có những gì. Thêm nữa, tool nạp
    ngầm mà lỗi thì lặng lẽ biến mất; dòng `register` ở đây thì fail rõ ràng.
    """
    registry = ToolRegistry(approver=approver)
    registry.register(calculator_tool())
    # SANDBOX truyền từ đây vì đây là chỗ duy nhất biết harness đang chạy ở đâu.
    registry.register(write_file_tool(SANDBOX))
    return registry


async def main() -> int:
    load_dotenv(ROOT.parent / ".env")
    argv = sys.argv[1:]

    session_path: Path | None = None
    if "--session" in argv:
        index = argv.index("--session")
        if index + 1 >= len(argv):
            print("--session cần một đường dẫn", file=sys.stderr)
            return 1
        session_path = Path(argv[index + 1])
        del argv[index : index + 2]

    flags = [arg for arg in argv if arg in PROVIDERS]
    rest = [arg for arg in argv if arg not in PROVIDERS]
    if not flags:
        print(f"cần chọn provider: {' | '.join(PROVIDERS)}", file=sys.stderr)
        return 1

    # Không truyền câu hỏi -> chat mode.
    question: str | None = rest[0] if rest else None
    display = TerminalStream()
    try:
        llm: Any = PROVIDERS[flags[0]](on_text=display)
    except RuntimeError as error:
        print(f"không dựng được provider: {error}", file=sys.stderr)
        return 1

    if session_path is None:
        session = Session()
    elif session_path.exists():
        session = Session.resume(session_path)
        print(f"(resume {len(session.events)} event từ {session_path})")
    else:
        session = Session(log_path=session_path)

    tools = build_tools(ask_terminal)
    if question is None:
        # Chỉ chat mode cần echo: one-shot in `print_log` ở cuối là đủ.
        session.on_event = echo_tool_activity(display)
        print("chat mode — Ctrl-C huỷ turn đang chạy; Ctrl-C ở prompt trống, "
              "Ctrl-D hoặc /quit để thoát")
        return await chat(
            llm=llm, tools=tools, session=session, system=SYSTEM, display=display,
        )

    print(f"user: {question}")
    try:
        await run_turn(
            llm=llm, tools=tools, session=session,
            system=SYSTEM, user_input=question,
        )
    except asyncio.CancelledError:
        # Ctrl-C: asyncio.run huỷ task này, nên nó tới đây dưới dạng
        # CancelledError (không phải KeyboardInterrupt). run_turn đã vá log
        # xong trước khi nhả exception, nên log trên đĩa đã hợp lệ để resume.
        display.close()
        print("\n(đã huỷ)")
        print_log(session)
        return 130
    # Text đã in dần trong lúc stream, chỉ cần đóng dòng.
    display.close()
    print_log(session)
    return 0
