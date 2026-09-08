"""Test cho mini_harness/core/session.py — chỉ kiểm phép chiếu event -> message."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mini_harness.core.session import ABORTED_BEFORE_DISPATCH, Session


def _session_with_one_tool_round() -> Session:
    """Đúng chuỗi event mà agent_loop.run_turn() ghi cho 1 vòng tool call."""
    session = Session()
    session.append({"type": "user", "content": "100*1.1 bằng mấy?"})
    session.append({
        "type": "assistant",
        "content": "Để tôi tính.",
        "tool_calls": [{"id": "c1", "name": "calculator", "arguments": '{"expression":"100*1.1"}'}],
    })
    session.append({
        "type": "tool_result",
        "call_id": "c1",
        "name": "calculator",
        "content": "110.0",
        "is_error": False,
    })
    session.append({"type": "assistant", "content": "110.", "tool_calls": []})
    return session


def test_projection_matches_wire_format() -> None:
    assert _session_with_one_tool_round().to_messages() == [
        {"role": "user", "content": "100*1.1 bằng mấy?"},
        {
            "role": "assistant",
            "content": "Để tôi tính.",
            "tool_calls": [{
                "id": "c1",
                "type": "function",
                "function": {"name": "calculator", "arguments": '{"expression":"100*1.1"}'},
            }],
        },
        {"role": "tool", "tool_call_id": "c1", "content": "110.0"},
        {"role": "assistant", "content": "110."},
    ]


def test_assistant_without_tool_calls_omits_the_key() -> None:
    # Gửi `tool_calls: []` lên API là lỗi; phải bỏ hẳn key.
    session = Session()
    session.append({"type": "assistant", "content": "xong", "tool_calls": []})
    assert session.to_messages() == [{"role": "assistant", "content": "xong"}]


def test_is_error_does_not_leak_into_the_wire_message() -> None:
    session = Session()
    session.append({
        "type": "tool_result",
        "call_id": "c1",
        "name": "calculator",
        "content": 'Error: invalid arguments: "expression" is required',
        "is_error": True,
    })
    (message,) = session.to_messages()
    assert "is_error" not in message
    # Model chỉ thấy được content, nên dấu hiệu lỗi phải nằm trong text.
    assert message["content"].startswith("Error: ")


def test_projection_is_pure() -> None:
    session = _session_with_one_tool_round()
    before = len(session.events)
    first = session.to_messages()
    second = session.to_messages()
    assert first == second
    assert len(session.events) == before
    # Không share object với log: sửa message không làm bẩn event gốc.
    first[0]["content"] = "đã sửa"
    assert session.events[0]["content"] == "100*1.1 bằng mấy?"


def test_unknown_event_type_fails_loudly() -> None:
    session = Session()
    session.append({"type": "thinking", "content": "..."})
    with pytest.raises(ValueError, match="thinking"):
        session.to_messages()


# ------------------------------------------------------------------ durability


def test_append_writes_one_jsonl_line_per_event(tmp_path: Path) -> None:
    log = tmp_path / "session.jsonl"
    session = Session(log_path=log)
    session.append({"type": "user", "content": "chào"})
    session.append({"type": "assistant", "content": "chào bạn", "tool_calls": []})
    lines = log.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    # Ghi ngay lúc append, không đợi kết thúc turn: crash giữa turn vẫn còn.
    assert json.loads(lines[0]) == {"type": "user", "content": "chào"}


def test_resume_round_trips_a_complete_log(tmp_path: Path) -> None:
    log = tmp_path / "session.jsonl"
    original = _session_with_one_tool_round()
    original.log_path = log
    for event in list(original.events):
        Session(log_path=log).append(event)  # ghi lại y nguyên
    resumed = Session.resume(log)
    assert resumed.events == original.events
    assert resumed.to_messages() == original.to_messages()


def test_resume_repairs_a_turn_killed_before_the_tool_result(tmp_path: Path) -> None:
    """Log kết thúc giữa turn -> phải vá, nếu không request sẽ không hợp lệ."""
    log = tmp_path / "session.jsonl"
    crashed = Session(log_path=log)
    crashed.append({"type": "user", "content": "100*1.1?"})
    crashed.append({
        "type": "assistant",
        "content": "",
        "tool_calls": [{"id": "c1", "name": "calculator", "arguments": "{}"}],
    })
    # <- process bị kill ở đây, không có tool_result nào cho c1.

    resumed = Session.resume(log)
    (synthetic,) = [event for event in resumed.events if event["type"] == "tool_result"]
    assert synthetic["call_id"] == "c1"
    assert synthetic["content"] == ABORTED_BEFORE_DISPATCH
    assert synthetic["is_error"] is True
    # Repair là event THẬT trong log, không phải vá lúc chiếu.
    assert len(log.read_text(encoding="utf-8").splitlines()) == 3

    # Và đây là invariant thật sự cần: mọi tool_call đều có tool message.
    messages = resumed.to_messages()
    called = {
        call["id"]
        for message in messages
        for call in message.get("tool_calls") or ()
    }
    answered = {
        message["tool_call_id"] for message in messages if message["role"] == "tool"
    }
    assert called == answered


def test_resume_leaves_answered_calls_alone(tmp_path: Path) -> None:
    log = tmp_path / "session.jsonl"
    session = Session(log_path=log)
    for event in _session_with_one_tool_round().events:
        session.append(event)
    before = len(session.events)
    assert len(Session.resume(log).events) == before



# --------------------------------------------------------------------- listener
# `on_event` là chỗ UI nghe. Bất biến duy nhất, nhưng là bất biến quan trọng:
# nó phát SAU commit, nên không bao giờ echo một thứ chưa được ghi.


def test_on_event_fires_after_the_event_is_committed(tmp_path) -> None:
    log_path = tmp_path / "s.jsonl"
    observed: list[tuple[int, int]] = []
    session = Session(log_path=log_path)

    def listen(_event: dict[str, object]) -> None:
        # Lúc listener chạy: event đã trong list VÀ đã xuống đĩa.
        observed.append((len(session.events), len(log_path.read_text().splitlines())))

    session.on_event = listen
    session.append({"type": "user", "content": "một"})
    session.append({"type": "user", "content": "hai"})
    assert observed == [(1, 1), (2, 2)]


def test_on_event_sees_every_event_in_order() -> None:
    seen: list[str] = []
    session = Session(on_event=lambda event: seen.append(event["type"]))
    session.append({"type": "user", "content": "x"})
    session.append({"type": "assistant", "content": "", "tool_calls": [{"id": "c1"}]})
    session.append({"type": "tool_result", "call_id": "c1", "content": "ok"})
    assert seen == ["user", "assistant", "tool_result"]


def test_abort_repair_also_reaches_the_listener() -> None:
    """Result giả cũng là event thật -> UI phải thấy nó, không được im lặng."""
    seen: list[str] = []
    session = Session()
    session.append({"type": "user", "content": "x"})
    session.append({
        "type": "assistant", "content": "",
        "tool_calls": [{"id": "c1", "name": "t", "arguments": "{}"}],
    })
    session.on_event = lambda event: seen.append(event["content"])
    assert session.abort_pending_tool_calls() == 1
    assert seen == [ABORTED_BEFORE_DISPATCH]
