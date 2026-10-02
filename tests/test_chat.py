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
from fakes import FakeLLM, StreamingFakeLLM


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


# -------------------------------------------------------------------- /compact


def _duoi_nguong() -> Session:
    """Session chưa tới ngưỡng tự nén, nhưng đã có gì đó để nén.

    Hai điều kiện phải cùng đúng thì test mới chứng minh được điều nó nói:
    dưới `_COMPACTION_THRESHOLD` (0.8) để đường tự động nằm im, và trên
    `_COMPACTION_RETAIN` (0.16) để còn turn cũ ngoài vùng giữ nguyên văn.
    """
    session = Session(max_tokens=1_500)
    for i in range(3):
        session.append({"type": "user", "content": f"hỏi {i} " + "x" * 250})
        session.append({"type": "assistant", "content": f"đáp {i} " + "y" * 250,
                        "tool_calls": []})
    return session


@pytest.mark.asyncio
async def test_compact_nen_ngay_du_chua_toi_nguong(capsys: Any) -> None:
    """Gõ `/compact` là bỏ qua ngưỡng — đó là toàn bộ khác biệt với đường tự động."""
    session = _duoi_nguong()
    assert session.plan_compaction() is None, "chưa tới ngưỡng thì tự động phải nằm im"

    llm = FakeLLM([AssistantMessage(text="checkpoint của ba turn")])
    assert await chat(**_chat_kwargs(llm, session, "/compact")) == 0

    assert any(event["type"] == "compaction" for event in session.events)
    assert "đã nén" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_compact_khong_phai_mot_turn(capsys: Any) -> None:
    """`/compact` không được ghi gì vào log như một câu hỏi của người dùng."""
    session = _duoi_nguong()
    llm = FakeLLM([AssistantMessage(text="checkpoint")])
    await chat(**_chat_kwargs(llm, session, "/compact"))

    assert all(event["content"] != "/compact" for event in session.events)


@pytest.mark.asyncio
async def test_compact_khong_co_ngan_sach_thi_noi_thang(capsys: Any) -> None:
    """Im lặng không làm gì là tệ nhất: người gõ sẽ tưởng là đã nén."""
    session = Session()
    session.append({"type": "user", "content": "hỏi"})
    llm = FakeLLM([AssistantMessage(text="không bao giờ được gọi")])

    await chat(**_chat_kwargs(llm, session, "/compact"))

    ra = capsys.readouterr().out
    assert "không nén được" in ra and "ngân sách" in ra
    assert all(event["type"] != "compaction" for event in session.events)


@pytest.mark.asyncio
async def test_compact_hong_thi_in_ly_do_va_chat_van_chay(capsys: Any) -> None:
    """Hỏng là chuyện của một lệnh, không phải của cả phiên."""
    class LLMHong:
        async def generate(self, **_kwargs: Any) -> AssistantMessage:
            raise RuntimeError("502 Bad Gateway")

    session = _duoi_nguong()
    assert await chat(**_chat_kwargs(LLMHong(), session, "/compact")) == 0
    ra = capsys.readouterr().out
    assert "không nén được" in ra and "502 Bad Gateway" in ra


@pytest.mark.asyncio
async def test_ban_tom_tat_khong_duoc_in_ra_man_hinh(capsys: Any) -> None:
    """Checkpoint là bookkeeping nội bộ, không phải lượt trả lời của model.

    Lỗi này tìm được bằng chạy thật chứ không phải bằng đọc code: `app.py`
    dựng provider MỘT lần với `on_text=display`, và phép nén dùng lại đúng
    object đó — nên cả màn hình bị đổ nguyên bản checkpoint. Test dùng
    `StreamingFakeLLM` vì chỉ fake có `on_text` mới tái hiện được.
    """
    session = _duoi_nguong()
    display = TerminalStream()
    # Model hội thoại: CÓ kênh hiển thị, và không được hỏi gì trong test này.
    llm = StreamingFakeLLM([], on_text=display)
    summarizer = FakeLLM([AssistantMessage(text="CHECKPOINT-KHONG-DUOC-HIEN")])

    kwargs = _chat_kwargs(llm, session, "/compact")
    kwargs["display"] = display
    kwargs["summarizer"] = summarizer
    assert await chat(**kwargs) == 0

    nen = [event for event in session.events if event["type"] == "compaction"]
    assert nen and nen[-1]["content"] == "CHECKPOINT-KHONG-DUOC-HIEN", (
        "phép nén phải đi qua summarizer"
    )
    assert llm.requests == [], "model hội thoại không được dùng để tóm tắt"
    assert "CHECKPOINT-KHONG-DUOC-HIEN" not in capsys.readouterr().out


@pytest.mark.asyncio
async def test_nen_tu_dong_giua_turn_cung_khong_in_ra(capsys: Any) -> None:
    """Đường tự động rò ra màn hình còn tệ hơn: nó chen vào GIỮA câu trả lời.

    Chạy thật in ra `assistant: <nguyên checkpoint><câu trả lời>` dính liền —
    user không có cách nào biết đâu là câu trả lời của mình.
    """
    session = _duoi_nguong()
    session.max_tokens = 400  # trên ngưỡng -> step đầu của turn sẽ tự nén
    display = TerminalStream()
    llm = StreamingFakeLLM([AssistantMessage(text="đáp")], on_text=display)
    summarizer = FakeLLM([AssistantMessage(text="CHECKPOINT-TU-DONG")])

    kwargs = _chat_kwargs(llm, session, "hỏi tiếp", "/quit")
    kwargs["display"] = display
    kwargs["summarizer"] = summarizer
    assert await chat(**kwargs) == 0

    assert any(event["type"] == "compaction" for event in session.events)
    assert len(summarizer.requests) == 1
    ra = capsys.readouterr().out
    assert "CHECKPOINT-TU-DONG" not in ra
    assert "đáp" in ra, "câu trả lời thật thì vẫn phải hiện"


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


# ------------------------------------------------- lỗi provider giữa một turn
# Bản thân việc retry transient đã nằm trong openai SDK (2 lần, backoff có
# jitter, tôn trọng Retry-After). Nhưng SDK ngừng retry khi stream ĐÃ bắt đầu,
# và lỗi lúc đó là `httpx.RemoteProtocolError` — không phải `openai.APIError`.
# Nó đi xuyên mọi handler và giết cả vòng chat. Đó là cái test này giữ.


class _FailsOnce:
    """Nổ ở lần generate đầu, lần sau trả lời bình thường."""

    def __init__(self, error: Exception, then: AssistantMessage) -> None:
        self._error = error
        self._then = then
        self.calls = 0

    async def generate(self, **_kwargs: Any) -> AssistantMessage:
        self.calls += 1
        if self.calls == 1:
            raise self._error
        return self._then


@pytest.mark.asyncio
async def test_provider_error_kills_the_turn_not_the_session() -> None:
    session = Session()
    # Cố ý dùng exception LẠ: `cli/` không được phép biết openai hay httpx là
    # gì, nên nó phải sống sót trước loại lỗi nó chưa từng nghe tên.
    llm = _FailsOnce(RuntimeError("peer closed connection"), AssistantMessage(text="ừ"))
    code = await chat(**_chat_kwargs(llm, session, "câu hỏng", "câu lành", "/quit"))

    assert code == 0, "một turn lỗi không được làm sập REPL"
    assert llm.calls == 2, "turn sau vẫn phải chạy"
    # Turn hỏng chỉ để lại event `user` — assistant chưa bao giờ về nên không
    # có gì để ghi. Log vẫn hợp lệ, resume được.
    assert [event["type"] for event in session.events] == [
        "user", "user", "assistant",
    ]
