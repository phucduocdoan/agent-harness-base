"""Test cho bài tập ReplayLLM — xem docs/bai-tap-replay.md.

Viết TRƯỚC khi có `mini_harness/llm/replay.py`. Cả file đang `skip`; tạo được
file đó là 7 test này tự chạy, và đó là tiêu chí "xong".
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from mini_harness.core.loop import run_turn
from mini_harness.core.session import Session
from mini_harness.core.types import AssistantMessage, ToolCall
from mini_harness.tools.calculator import calculator_tool
from mini_harness.tools.registry import ToolRegistry
from fakes import FakeLLM

replay = pytest.importorskip(
    "mini_harness.llm.replay",
    reason="chưa làm bài tập — xem docs/bai-tap-replay.md",
)
ReplayLLM = replay.ReplayLLM


def _ghi_log(tmp_path: Path, *events: dict[str, Any]) -> Path:
    """Ghi một log jsonl y hệt cái Session sinh ra."""
    path = tmp_path / "rec.jsonl"
    path.write_text(
        "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events), encoding="utf-8"
    )
    return path


@pytest.mark.asyncio
async def test_phat_lai_assistant_dau_tien(tmp_path: Path) -> None:
    log = _ghi_log(
        tmp_path,
        {"type": "user", "content": "2+2?"},
        {"type": "assistant", "content": "Bằng 4.", "tool_calls": []},
    )
    reply = await ReplayLLM(log).generate(system="", messages=[], tools=[])
    assert reply.text == "Bằng 4."
    assert reply.tool_calls == ()


@pytest.mark.asyncio
async def test_bo_qua_event_khong_phai_assistant(tmp_path: Path) -> None:
    """user và tool_result không phải lượt nói của model — không được phát lại."""
    log = _ghi_log(
        tmp_path,
        {"type": "user", "content": "bỏ qua tôi"},
        {"type": "tool_result", "call_id": "c1", "name": "x", "content": "bỏ qua tôi",
         "is_error": False},
        {"type": "assistant", "content": "chỉ dòng này", "tool_calls": []},
    )
    reply = await ReplayLLM(log).generate(system="", messages=[], tools=[])
    assert reply.text == "chỉ dòng này"


@pytest.mark.asyncio
async def test_phat_lai_dung_thu_tu(tmp_path: Path) -> None:
    log = _ghi_log(
        tmp_path,
        {"type": "assistant", "content": "một", "tool_calls": []},
        {"type": "assistant", "content": "hai", "tool_calls": []},
    )
    llm = ReplayLLM(log)
    assert (await llm.generate(system="", messages=[], tools=[])).text == "một"
    assert (await llm.generate(system="", messages=[], tools=[])).text == "hai"


@pytest.mark.asyncio
async def test_doi_shape_tool_calls(tmp_path: Path) -> None:
    """Log dùng key `arguments`; core dùng `arguments_json`, và là tuple[ToolCall]."""
    log = _ghi_log(
        tmp_path,
        {"type": "assistant", "content": "", "tool_calls": [
            {"id": "call_1", "name": "calculator", "arguments": '{"expression": "2+2"}'},
            {"id": "call_2", "name": "calculator", "arguments": '{"expression": "3+3"}'},
        ]},
    )
    reply = await ReplayLLM(log).generate(system="", messages=[], tools=[])
    assert reply.text == ""
    assert isinstance(reply.tool_calls, tuple)
    assert reply.tool_calls == (
        ToolCall(id="call_1", name="calculator", arguments_json='{"expression": "2+2"}'),
        ToolCall(id="call_2", name="calculator", arguments_json='{"expression": "3+3"}'),
    )


@pytest.mark.asyncio
async def test_on_text_duoc_goi(tmp_path: Path) -> None:
    """Phát lại phải thấy text hiện dần, không phải hiện một cục lúc cuối."""
    log = _ghi_log(
        tmp_path, {"type": "assistant", "content": "chào bạn", "tool_calls": []}
    )
    manh: list[str] = []
    reply = await ReplayLLM(log, on_text=manh.append).generate(
        system="", messages=[], tools=[]
    )
    assert "".join(manh) == "chào bạn" == reply.text


@pytest.mark.asyncio
async def test_het_kich_ban_thi_bao_loi(tmp_path: Path) -> None:
    """Phiên mới đi lệch phiên cũ là thông tin cần biết ngay, không được im lặng.

    Test không quy định loại exception — miễn là nó không lặng lẽ trả về gì đó.
    """
    log = _ghi_log(tmp_path, {"type": "assistant", "content": "hết đây", "tool_calls": []})
    llm = ReplayLLM(log)
    await llm.generate(system="", messages=[], tools=[])
    with pytest.raises(Exception):
        await llm.generate(system="", messages=[], tools=[])


@pytest.mark.asyncio
async def test_chay_duoc_voi_run_turn_va_khop_ban_goc(tmp_path: Path) -> None:
    """Tiêu chí thật: ghi một phiên bằng FakeLLM, replay lại, log phải trùng khít.

    Không nhánh `if` nào cho replay: `run_turn` gọi ReplayLLM y như gọi AzureLLM.
    """
    tools = ToolRegistry()
    tools.register(calculator_tool())
    goc_path = tmp_path / "goc.jsonl"

    goc = Session(log_path=goc_path)
    await run_turn(
        llm=FakeLLM([
            AssistantMessage(text="", tool_calls=(ToolCall(
                id="call_1", name="calculator", arguments_json='{"expression": "6*7"}'),)),
            AssistantMessage(text="Bằng 42.", tool_calls=()),
        ]),
        tools=tools, session=goc, system="test", user_input="6*7?",
    )

    lai = Session()
    ket_qua = await run_turn(
        llm=ReplayLLM(goc_path), tools=tools, session=lai,
        system="test", user_input="6*7?",
    )
    assert ket_qua == "Bằng 42."
    assert lai.events == goc.events
