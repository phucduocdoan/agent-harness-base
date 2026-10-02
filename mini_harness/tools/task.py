"""Tool task: uỷ quyền một việc tự-chứa cho sub-agent, chạy trong cửa sổ RIÊNG.

`core/session.py` có ba phép cắt cho hội thoại ĐÃ phình: bỏ turn cũ, cắt ruột
tool result, nén thành tóm tắt. Cả ba chữa SAU khi log đã to. Tool này là một
trục khác: việc giao cho sub-agent (tìm kiếm, đọc một trang dài, quét nhiều
tài liệu) chạy trong session CON của chính nó — cha chỉ nhận lại đúng một đoạn
text cuối, nên log của cha không hề phình lên dù con đi bao nhiêu step. Không
phải "cắt bớt cái đã to" mà là "đừng để nó to lên ở chỗ cha" ngay từ đầu, vì
cửa sổ ngữ cảnh là tài nguyên tính theo TỪNG REQUEST.

Dễ lẫn với trục trên: hạn mức token/phút (xem app.py, `MAX_TOKENS`). Đó là tài
nguyên CHUNG của cả deployment — con vẫn gọi model qua đúng `llm` của cha, nên
mỗi step của con vẫn trừ vào đúng hạn mức đó. Uỷ quyền không làm hạn mức này
rộng ra; nó chỉ giữ cửa sổ của cha khỏi phình. Hai thứ đo cùng bằng "token"
nhưng không cùng một trục, và gộp chung là nguồn nhầm lẫn phổ biến nhất của
tính năng này.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from mini_harness.core.loop import run_turn
from mini_harness.core.session import Session
from mini_harness.tools.registry import ToolDefinition, define_tool

# tên profile -> (session con ĐÃ dựng, tool registry của con, system prompt của con).
# `spawn` do app.py đưa xuống, nhờ vậy file này KHÔNG import app.py: wiring
# (ngân sách token của con, log file, on_event, event {"type":"agent"}) vẫn
# tập trung đúng một chỗ, tool chỉ gọi nó.
Spawn = Callable[[str], tuple[Session, Any, str]]

# Trần step của MỘT lượt con. Thấp hơn hẳn mặc định của `run_turn` (20): một
# sub-agent nhận việc tự-chứa, không phải một hội thoại nhiều vòng qua lại với
# người dùng — kẹt ở đây thường là dấu hiệu việc giao không đủ rõ, không phải
# việc cần thêm step.
_MAX_STEPS = 10


def task_tool(
    *,
    spawn: Spawn,
    llm: Any,
    delegatable: tuple[str, ...],
    runs: list[Session],
) -> ToolDefinition:
    """Khai báo tool `task`, với `spawn`/`llm`/`delegatable`/`runs` nhận TỪ NGOÀI.

    `llm` là provider KHÔNG có `on_text` (ở app.py chính là `summarizer`): con
    không có kênh hiển thị riêng, nó chỉ trả về đúng một đoạn text cuối, và
    dùng provider có `on_text` sẽ làm text của con rò thẳng ra terminal của
    cha — đúng lỗi mà `run_turn` đã né cho phép nén (xem docstring của nó).
    Cùng một `llm` đó cũng được dùng làm `summarizer` cho con, vì con cũng cần
    nén nếu việc nó làm đủ dài.

    `delegatable` đi thẳng vào JSON Schema qua `"enum"`, nên registry CỦA CHA
    đã chặn tên profile sai trước khi `execute` chạy — `_task` dưới đây không
    kiểm tra lại tên, vì tới được đó nghĩa là validator đã cho qua rồi.

    `runs` do app.py sở hữu; mỗi lần `task` chạy, session con được append vào
    đây. Đây là cách `/task` xem lại được một lượt uỷ quyền, kể cả khi phiên
    không chạy `--session` nên không có gì trên đĩa.
    """

    async def _task(args: dict[str, Any]) -> str:
        session, tools, system = spawn(args["agent"])
        runs.append(session)
        text = await run_turn(
            llm=llm, tools=tools, session=session, system=system,
            user_input=args["task"], max_steps=_MAX_STEPS, summarizer=llm,
        )
        # Model rỗng là một kết quả HỢP LỆ (con chỉ gọi tool rồi dừng mà không
        # tóm tắt lại), nhưng trả thẳng "" cho cha thì nhìn như tool im lặng
        # thất bại. Nói rõ ra thay vì để cha đoán.
        return text or "(the sub-agent finished without producing any text)"

    # KHÔNG bắt `asyncio.CancelledError` ở đây: `run_turn` đã tự vá session
    # của chính con (`abort_pending_tool_calls()` rồi `raise`) — đó chính là
    # `session` ở trên, nên không có gì cho một handler thứ hai làm. Bắt thêm
    # ở đây là nuốt mất Ctrl-C của người dùng.
    #
    # KHÔNG bắt `MaxStepsExceeded` ở đây: `ToolRegistry.execute` của CHA đã
    # bọc mọi `Exception` (và `MaxStepsExceeded` kế thừa `RuntimeError`) thành
    # `ToolResult(is_error=True)` để model cha đọc được. `CancelledError` kế
    # thừa `BaseException` nên không rơi vào bọc đó — Ctrl-C vẫn xuyên qua
    # đúng một tầng registry, không phải hai.
    return define_tool(
        name="task",
        description=(
            "Delegate a self-contained piece of work to a sub-agent that runs in "
            "its own context window. The sub-agent sees nothing of this "
            "conversation and returns only its final answer, so `task` must "
            "contain everything it needs to know. Prefer it for work whose "
            "intermediate results you do not need: searching, reading long "
            "pages, scanning many documents."
        ),
        parameters={
            "agent": {
                "type": "string",
                "enum": list(delegatable),
                "required": True,
                "description": "Which sub-agent to run.",
            },
            "task": {
                "type": "string",
                "required": True,
                "description": "The full instruction for the sub-agent, written "
                                "as if to someone who has read none of this "
                                "conversation.",
            },
        },
        execute=_task,
    )
