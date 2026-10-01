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
import os
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from mini_harness.cli.chat import chat, echo_tool_activity, print_log
from mini_harness.cli.terminal import TerminalStream, ask_terminal
from mini_harness.context import time_context, workspace_context
from mini_harness.core.agent import AgentProfile
from mini_harness.core.loop import run_turn
from mini_harness.core.prompt import assemble
from mini_harness.core.session import Session
from mini_harness.llm.azure import AzureLLM
from mini_harness.llm.deepseek import DeepSeekLLM
from mini_harness.llm.replay import ReplayLLM
from mini_harness.profiles import DEFAULT_AGENT, PROFILES
from mini_harness.tools.calculator import calculator_tool
from mini_harness.tools.registry import Approver, ToolRegistry
from mini_harness.tools.web_search import web_search_tool
from mini_harness.tools.write_file import write_file_tool

# Thư mục gốc của project (package nằm trong nó, .env nằm cạnh package).
ROOT = Path(__file__).resolve().parent.parent
SANDBOX = ROOT / "sandbox"

# Ngân sách token cho message list. Con số này ở ĐÂY chứ không ở core/ vì chỉ
# app.py mới biết đang chạy provider nào và cửa sổ của nó bao nhiêu.
#
# Để thấp hơn cửa sổ thật (128k của gpt-4o) rất nhiều, có chủ ý: bộ đếm trong
# session chỉ là ước lượng, và nó KHÔNG đếm hai thứ cũng chiếm chỗ trong cùng
# request — system prompt và tool schema. Phần dư là chỗ cho chúng cộng với
# output model sắp sinh ra.
MAX_TOKENS = 60_000

# Provider nào tồn tại: khai báo ở đây, một chỗ duy nhất.
# ReplayLLM không nằm trong dict này vì cờ của nó ĂN MỘT GIÁ TRỊ (`--replay
# <log>`), còn dict này ánh xạ cờ-không-giá-trị -> class dựng bằng `on_text`.
# Nhét nó vào đây thì phải thêm một nhánh đặc biệt lúc dựng, dài hơn là để riêng.
PROVIDERS = {
    "--azure": AzureLLM,
    "--deepseek": DeepSeekLLM,
}


def build_tools(
    allow: tuple[str, ...], approver: Approver | None = None
) -> ToolRegistry:
    """Đăng ký tool mà profile cho phép. Liệt kê tay, KHÔNG auto-discover.

    Quét thư mục để tự nạp tool nghe tiện hơn nhưng phá đúng cái tính chất
    đang giữ: đọc một file là biết harness có những gì. Thêm nữa, tool nạp
    ngầm mà lỗi thì lặng lẽ biến mất; dòng `register` ở đây thì fail rõ ràng.

    Lọc ở đây chứ không lọc trong `ToolRegistry`: registry vốn ĐÃ là "tập tool
    visible của một agent" (xem docstring của nó). Một agent một registry thì
    không cần khái niệm visibility nào mới — cái cần chỉ là dựng registry khác
    nhau cho profile khác nhau, và đó là việc của file wiring này.

    Raises:
        ValueError: profile gọi tên tool không tồn tại, hoặc gọi một tool cần
            credential mà credential chưa có. Cả hai fail ngay lúc khởi động,
            vì để nó im lặng thì agent chạy được nhưng thiếu tool, và triệu
            chứng sẽ là "model tự dưng ngu đi" — loại bug tốn giờ nhất.
    """
    # SANDBOX truyền từ đây vì đây là chỗ duy nhất biết harness đang chạy ở đâu.
    available = {
        definition.name: definition
        for definition in (calculator_tool(), write_file_tool(SANDBOX))
    }
    # Credential chỉ đòi khi profile THẬT SỰ cần: `tutor` không phải có
    # TAVILY_API_KEY mới chạy được. Đây cũng là chỗ duy nhất đọc biến môi
    # trường cho tool — `tools/web_search.py` nhận key qua tham số.
    #
    # Thiếu key thì DỪNG, không phải lặng lẽ bỏ tool đi. Research-Agents chọn
    # hướng ngược lại (`is_available` -> `get_tools()` trả []), hợp lý với một
    # app nhiều nguồn mà mất một nguồn vẫn chạy được. Ở đây thì không: một
    # `research` agent không có web_search vẫn sẽ trả lời, bằng trí nhớ, và
    # không ai nhìn ra được sự khác biệt cho tới khi kiểm chứng từng câu.
    if "web_search" in allow:
        api_key = os.environ.get("TAVILY_API_KEY")
        if not api_key:
            raise ValueError(
                "tool web_search cần TAVILY_API_KEY trong .env (lấy ở tavily.com)"
            )
        available["web_search"] = web_search_tool(api_key)
    unknown = sorted(set(allow) - set(available))
    if unknown:
        raise ValueError(f"profile gọi tool không có: {unknown}")
    registry = ToolRegistry(approver=approver)
    for name in allow:
        registry.register(available[name])
    return registry


def build_system_prompt(profile: AgentProfile) -> str:
    """Ráp system prompt cho một profile. Gọi MỘT lần cho cả phiên.

    Một lần, không phải mỗi step: system prompt là prefix của mọi request
    trong phiên, và prompt cache chỉ ăn khi prefix giống hệt nhau. Ráp lại mỗi
    step (kèm đồng hồ chạy) là tự tay làm hỏng cache của chính mình.

    Mô tả workspace chỉ gửi cho agent thật sự ghi được vào đó. Kể cho một agent
    không có `write_file` về thư mục nó không đụng tới được là mời nó thử.
    """
    return assemble(
        profile.persona,
        time_context(),
        workspace_context(SANDBOX) if "write_file" in profile.tools else None,
    )


async def main() -> int:
    load_dotenv(ROOT / ".env")
    argv = sys.argv[1:]

    session_path: Path | None = None
    if "--session" in argv:
        index = argv.index("--session")
        if index + 1 >= len(argv):
            print("--session cần một đường dẫn", file=sys.stderr)
            return 1
        session_path = Path(argv[index + 1])
        del argv[index : index + 2]

    replay_path: Path | None = None
    if "--replay" in argv:
        index = argv.index("--replay")
        if index + 1 >= len(argv):
            print("--replay cần một đường dẫn log", file=sys.stderr)
            return 1
        replay_path = Path(argv[index + 1])
        if not replay_path.exists():
            print(f"không có log để phát lại: {replay_path}", file=sys.stderr)
            return 1
        del argv[index : index + 2]

    profile = PROFILES[DEFAULT_AGENT]
    if "--agent" in argv:
        index = argv.index("--agent")
        if index + 1 >= len(argv):
            print(f"--agent cần một tên: {' | '.join(PROFILES)}", file=sys.stderr)
            return 1
        name = argv[index + 1]
        if name not in PROFILES:
            print(f"không có agent {name!r}; có: {' | '.join(PROFILES)}",
                  file=sys.stderr)
            return 1
        profile = PROFILES[name]
        del argv[index : index + 2]

    flags = [arg for arg in argv if arg in PROVIDERS]
    rest = [arg for arg in argv if arg not in PROVIDERS]
    if not flags and replay_path is None:
        print(f"cần chọn provider: {' | '.join(PROVIDERS)} | --replay <log>",
              file=sys.stderr)
        return 1

    # Không truyền câu hỏi -> chat mode.
    question: str | None = rest[0] if rest else None
    display = TerminalStream()
    try:
        llm: Any = (
            ReplayLLM(replay_path, on_text=display)
            if replay_path is not None
            else PROVIDERS[flags[0]](on_text=display)
        )
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

    # Đặt sau mọi nhánh dựng session, kể cả `resume`: ngân sách là chính sách
    # lúc chạy, không phải thuộc tính của cái log trên đĩa.
    session.max_tokens = MAX_TOKENS

    # Ghi agent vào log, một lần cho mỗi session. Không ghi thì `--resume` và
    # `--replay` sẽ dựng lại hội thoại cũ bằng persona HIỆN TẠI — replay vừa
    # ghim model lại để còn đúng một biến, mà prompt vẫn trôi thì vẫn hai.
    recorded = next(
        (event["name"] for event in session.events if event["type"] == "agent"), None
    )
    if recorded is None:
        session.append({"type": "agent", "name": profile.name})
    elif recorded != profile.name:
        # Harness thật TỪ CHỐI hẳn ở đây ('agent-preset/locked': composition
        # đóng băng khi hội thoại đã bắt đầu). Ở repo học thì cảnh báo hợp hơn:
        # chạy lại một log cũ bằng persona khác chính là một thí nghiệm đáng
        # làm. Nhưng phải nói ra, vì im lặng thì kết quả so sánh sẽ vô nghĩa.
        print(f"(log tạo bởi agent {recorded!r}, đang chạy {profile.name!r})")

    system = build_system_prompt(profile)
    try:
        tools = build_tools(profile.tools, ask_terminal)
    except ValueError as error:
        # Thiếu credential là lỗi của người chạy, không phải bug — traceback ở
        # đây chỉ làm người đọc phải lội tìm dòng cuối.
        print(error, file=sys.stderr)
        return 1
    if question is None:
        # Chỉ chat mode cần echo: one-shot in `print_log` ở cuối là đủ.
        session.on_event = echo_tool_activity(display)
        print("chat mode — Ctrl-C huỷ turn đang chạy; Ctrl-C ở prompt trống, "
              "Ctrl-D hoặc /quit để thoát")
        return await chat(
            llm=llm, tools=tools, session=session, system=system, display=display,
        )

    print(f"user: {question}")
    try:
        await run_turn(
            llm=llm, tools=tools, session=session,
            system=system, user_input=question,
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
