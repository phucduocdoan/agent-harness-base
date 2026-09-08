"""Test cho vòng chat (`chat()` trong mini_harness/cli/chat.py) — semantics của Ctrl-C.

Ba bất biến, và cả ba đều dùng SIGINT THẬT (`os.kill(os.getpid(), SIGINT)`):
signal handler được `chat()` cài qua `loop.add_signal_handler` chính là thứ
đang được test. Mock nó đi thì test còn lại chẳng chứng minh gì cả.

`chat()` nhận `read_line` từ ngoài nên kịch bản người dùng gõ gì được viết ở đây
mà không cần TTY.
"""

from __future__ import annotations

import asyncio
import os
import signal
from typing import Any

import pytest

from mini_harness.cli.chat import chat
from mini_harness.cli.terminal import TerminalStream
from mini_harness.core.session import ABORTED_BEFORE_DISPATCH, Session
from mini_harness.core.types import AssistantMessage, ToolCall
from mini_harness.tools.calculator import calculator_tool
from mini_harness.tools.registry import ToolRegistry, define_tool
from fakes import FakeLLM


# ------------------------------------------------------------------- fixtures


def _typing(*lines: str):
    """Giả người dùng gõ lần lượt các dòng, hết thì như Ctrl-D."""
    pending = list(lines)

    async def read_line() -> str:
        if not pending:
            raise EOFError("stdin đã đóng")
        return pending.pop(0) + "\n"

    return read_line


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(calculator_tool())
    return registry


class _SigintDuringGenerate:
    """Model giả gửi SIGINT cho chính process rồi treo — mô phỏng Ctrl-C giữa turn."""

    def __init__(self, then: AssistantMessage) -> None:
        self._then = then
        self.calls = 0

    async def generate(self, **_kwargs: Any) -> AssistantMessage:
        self.calls += 1
        if self.calls == 1:
            os.kill(os.getpid(), signal.SIGINT)
            await asyncio.sleep(60)  # không bao giờ tới đích
        return self._then


def _chat_kwargs(llm: Any, session: Session, *lines: str) -> dict[str, Any]:
    return {
        "llm": llm,
        "tools": _registry(),
        "session": session,
        "system": "test",
        "display": TerminalStream(),
        "read_line": _typing(*lines),
    }


# ------------------------------------------------------------------ multi-turn


@pytest.mark.asyncio
async def test_turns_share_one_session() -> None:
    """Nhiều turn trong MỘT process phải cùng một event log — đó là lý do REPL tồn tại."""
    session = Session()
    llm = FakeLLM([AssistantMessage(text="ừ"), AssistantMessage(text="ừ")])
    code = await chat(**_chat_kwargs(llm, session, "câu một", "câu hai", "/quit"))
    assert code == 0
    assert len(llm.requests) == 2
    assert [event["type"] for event in session.events] == [
        "user", "assistant", "user", "assistant",
    ]
    # Turn thứ hai NHÌN THẤY turn thứ nhất.
    assert session.events[2]["content"] == "câu hai"


@pytest.mark.asyncio
async def test_blank_line_is_not_a_turn() -> None:
    session = Session()
    llm = FakeLLM([])
    await chat(**_chat_kwargs(llm, session, "", "   ", "/quit"))
    assert len(llm.requests) == 0
    assert session.events == []


@pytest.mark.asyncio
async def test_ctrl_d_exits_with_zero() -> None:
    session = Session()
    code = await chat(**_chat_kwargs(FakeLLM([]), session))
    assert code == 0


# ----------------------------------------------------------------- Ctrl-C lần 1
# Đang chạy turn -> huỷ TURN, giữ session, quay lại prompt.


@pytest.mark.asyncio
async def test_sigint_during_a_turn_does_not_end_the_chat(capsys: Any) -> None:
    session = Session()
    llm = _SigintDuringGenerate(AssistantMessage(text="turn sau vẫn chạy"))
    code = await chat(**_chat_kwargs(llm, session, "câu bị huỷ", "câu sau", "/quit"))

    assert code == 0                       # /quit, KHÔNG phải chết vì Ctrl-C
    assert llm.calls == 2                  # turn sau thực sự đã gọi model
    assert "đã huỷ turn" in capsys.readouterr().out
    # Huỷ giữa generate: chưa có assistant event nào -> không có gì phải vá.
    assert [event["type"] for event in session.events] == [
        "user", "user", "assistant",
    ]


@pytest.mark.asyncio
async def test_sigint_mid_tool_leaves_a_usable_session() -> None:
    """Huỷ khi tool đang chạy: log được vá NGAY trong turn bị huỷ, nên turn sau gửi đi được."""
    session = Session()
    calls = (ToolCall(id="c1", name="calculator", arguments_json='{"expression": "1+1"}'),)
    llm = FakeLLM([
        AssistantMessage(tool_calls=calls),
        AssistantMessage(text="xong"),
    ])

    async def hang(_args: dict[str, Any]) -> str:
        os.kill(os.getpid(), signal.SIGINT)
        await asyncio.sleep(60)
        return "không bao giờ tới đây"

    tools = ToolRegistry()
    tools.register(define_tool(
        name="calculator", description="Treo mãi.",
        parameters={"expression": {"type": "string", "required": True}}, execute=hang,
    ))

    code = await chat(
        llm=llm, tools=tools, session=session, system="test",
        display=TerminalStream(),
        read_line=_typing("tính 1+1", "còn đó không", "/quit"),
    )
    assert code == 0
    types = [event["type"] for event in session.events]
    assert types == ["user", "assistant", "tool_result", "user", "assistant"]
    assert session.events[2]["content"] == ABORTED_BEFORE_DISPATCH
    # to_messages() chạy được = log hợp lệ để gửi lại cho model.
    called = sum(len(e.get("tool_calls", [])) for e in session.events if e["type"] == "assistant")
    answered = sum(1 for e in session.events if e["type"] == "tool_result")
    assert called == answered


# ----------------------------------------------------------------- Ctrl-C lần 2
# Ở prompt trống -> thoát chương trình.


@pytest.mark.asyncio
async def test_sigint_at_the_prompt_exits_with_130() -> None:
    async def read_line() -> str:
        os.kill(os.getpid(), signal.SIGINT)
        await asyncio.sleep(60)
        return "không bao giờ tới đây\n"

    code = await chat(
        llm=FakeLLM([]), tools=_registry(), session=Session(), system="test",
        display=TerminalStream(), read_line=read_line,
    )
    assert code == 130
