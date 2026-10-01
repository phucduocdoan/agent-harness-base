"""Test cho compaction — cắt ngữ cảnh ở phép chiếu, không đụng vào log.

Bất biến quan trọng nhất không phải "có cắt", mà là "cắt xong request vẫn
HỢP LỆ": mọi `tool_call` còn lại phải còn `tool_result` của nó. Cắt lẻ từng
event sẽ phá đúng chỗ đó, nên ở đây đơn vị cắt là trọn một turn.
"""

from __future__ import annotations

from mini_harness.core.session import Session


def _turn(tag: str, *, size: int = 1, with_tool: bool = False) -> list[dict]:
    """Một turn hoàn chỉnh. `size` để bơm cho nó nặng lên."""
    padding = tag * size
    events: list[dict] = [{"type": "user", "content": f"hỏi {padding}"}]
    if with_tool:
        events += [
            {"type": "assistant", "content": "", "tool_calls": [
                {"id": f"call_{tag}", "name": "calculator",
                 "arguments": '{"expression": "1+1"}'}]},
            {"type": "tool_result", "call_id": f"call_{tag}",
             "name": "calculator", "content": "2.0", "is_error": False},
        ]
    events.append({"type": "assistant", "content": f"đáp {padding}", "tool_calls": []})
    return events


def _flat(*turns: list[dict]) -> list[dict]:
    return [event for turn in turns for event in turn]


def test_khong_co_ngan_sach_thi_khong_cat_gi() -> None:
    """Mặc định `max_tokens=None` — hành vi cũ phải y nguyên."""
    events = _flat(_turn("a"), _turn("b"), _turn("c"))
    assert len(Session(events=events).to_messages()) == len(events)


def test_vuot_ngan_sach_thi_bo_turn_cu_nhat() -> None:
    events = _flat(_turn("a", size=200), _turn("b", size=200), _turn("c", size=200))
    full = Session(events=events)
    budget = len(full.to_messages()[0]["content"])  # chắc chắn nhỏ hơn tổng
    session = Session(events=events, max_tokens=budget)

    kept = [m["content"] for m in session.to_messages()]
    assert any("c" * 200 in c for c in kept), "turn mới nhất phải còn"
    assert not any("a" * 200 in c for c in kept), "turn cũ nhất phải bị bỏ"


def test_log_khong_bi_dong_vao() -> None:
    """Cắt là chuyện của phép chiếu. Log vẫn là bản ghi đầy đủ."""
    events = _flat(_turn("a", size=500), _turn("b", size=500))
    session = Session(events=events, max_tokens=10)
    truoc = len(session.events)
    session.to_messages()
    assert len(session.events) == truoc
    # Và gọi lại vẫn ra đúng kết quả đó — projection thuần, không tích luỹ.
    assert session.to_messages() == session.to_messages()


def test_turn_cuoi_luon_duoc_giu_du_mot_minh_no_da_vuot() -> None:
    """Bỏ nốt turn cuối thì không còn gì để hỏi model — thà tràn còn hơn rỗng."""
    session = Session(events=_flat(_turn("a"), _turn("b", size=5000)), max_tokens=1)
    messages = session.to_messages()
    assert messages, "không được trả về rỗng"
    assert messages[0]["role"] == "user"
    assert "b" * 5000 in messages[0]["content"]


def test_cat_xong_khong_con_tool_result_mo_coi() -> None:
    """Bất biến cốt tử: thiếu một result thì API từ chối CẢ request."""
    events = _flat(
        _turn("a", size=300, with_tool=True),
        _turn("b", size=300, with_tool=True),
        _turn("c", size=300, with_tool=True),
    )
    session = Session(events=events, max_tokens=600)
    messages = session.to_messages()

    da_goi = {
        call["id"]
        for message in messages
        for call in message.get("tool_calls") or ()
    }
    da_tra = {m["tool_call_id"] for m in messages if m["role"] == "tool"}
    assert da_goi == da_tra, f"mồ côi: {da_goi ^ da_tra}"
    # Và phải thực sự có cắt, nếu không test này chẳng chứng minh gì.
    assert len(messages) < len(events)


def test_message_list_luon_bat_dau_bang_user() -> None:
    """Hệ quả của việc cắt theo turn — không bao giờ mở đầu bằng `tool`."""
    events = _flat(*[_turn(tag, size=300, with_tool=True) for tag in "abcd"])
    session = Session(events=events, max_tokens=800)
    assert session.to_messages()[0]["role"] == "user"
