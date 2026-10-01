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


# ------------------------------------------------- cắt ruột một tool result


def _tool_turn(tag: str, content: str) -> list[dict]:
    """Một turn có đúng một tool result mang `content`."""
    return [
        {"type": "user", "content": f"hỏi {tag}"},
        {"type": "assistant", "content": "", "tool_calls": [
            {"id": f"call_{tag}", "name": "web_search", "arguments": '{"query": "x"}'}]},
        {"type": "tool_result", "call_id": f"call_{tag}", "name": "web_search",
         "content": content, "is_error": False},
        {"type": "assistant", "content": f"đáp {tag}", "tool_calls": []},
    ]


def _tool_message(session: Session) -> dict:
    (message,) = [m for m in session.to_messages() if m["role"] == "tool"]
    return message


def test_tool_result_duoi_tran_khong_bi_dong_vao() -> None:
    events = _tool_turn("a", "ngắn thôi")
    session = Session(events=events, max_tool_result_chars=1_000)
    assert _tool_message(session)["content"] == "ngắn thôi"


def test_tool_result_qua_kho_giu_dau_va_duoi() -> None:
    """Giữ cả đuôi, không cắt cụt: kết luận của một trang nằm ở cuối."""
    body = "ĐẦU" + "x" * 50_000 + "ĐUÔI"
    session = Session(events=_tool_turn("a", body), max_tool_result_chars=1_000)
    content = _tool_message(session)["content"]

    assert content.startswith("ĐẦU")
    assert content.endswith("ĐUÔI")
    assert len(content) < 1_400, "giữ gần đúng trần, cộng marker"


def test_cat_roi_thi_phai_noi_ra_la_da_cat() -> None:
    """Im lặng thì model đọc một kết quả cụt như thể nó đầy đủ."""
    session = Session(events=_tool_turn("a", "x" * 50_000), max_tool_result_chars=1_000)
    content = _tool_message(session)["content"]
    assert "hidden" in content
    assert str(50_000 - 1_000) in content, "phải nói mất bao nhiêu ký tự"


def test_marker_mang_theo_locator() -> None:
    """Cắt mà không để lại locator thì phần giữa coi như mất, dù log còn giữ.

    Locator là cái biến "đã huỷ" thành "đã cất": `call_id` để tìm lại tool
    result, dải offset để biết xin khúc nào.
    """
    session = Session(events=_tool_turn("abc123", "x" * 50_000),
                      max_tool_result_chars=1_000)
    content = _tool_message(session)["content"]
    assert 'call_id "call_abc123"' in content
    assert "666-49666" in content, "phải nói khúc nào đang bị giấu"
    assert "50000-character" in content


def test_marker_khong_nhac_ten_tool_nao() -> None:
    """Session không biết agent đang cầm tool gì, nên nói tên tool là nói liều.

    `tutor` không có tool nào; một marker bảo nó gọi `read_spill` là chỉ nó
    vào một thứ không tồn tại. Session nêu sự thật + locator, tool tự dạy cách
    dùng locator — đúng đường biên upstream vạch cho `spill/`.
    """
    session = Session(events=_tool_turn("a", "x" * 50_000), max_tool_result_chars=1_000)
    assert "read_spill" not in _tool_message(session)["content"]


def test_log_van_giu_ban_day_du_sau_khi_chieu() -> None:
    """Đây là lý do việc cắt phải nằm ở phép chiếu chứ không ở trong tool."""
    body = "x" * 50_000
    session = Session(events=_tool_turn("a", body), max_tool_result_chars=1_000)
    session.to_messages()
    (event,) = [e for e in session.events if e["type"] == "tool_result"]
    assert event["content"] == body


def test_cat_ruot_chay_truoc_khi_bo_turn() -> None:
    """Thứ tự quan trọng: cắt trước thì không phải vứt turn nào cả.

    Nếu `_estimate` đếm tool result ở cỡ ĐẦY ĐỦ (100k ký tự ≈ 40k token) thì
    turn "a" chắc chắn bị bỏ. Cắt trước thì cả hai turn cùng vừa.
    """
    events = _flat(_turn("a"), _tool_turn("b", "x" * 100_000))
    session = Session(events=events, max_tool_result_chars=1_000, max_tokens=2_000)
    kept = [m.get("content") or "" for m in session.to_messages()]
    assert any("hỏi a" in content for content in kept), "turn a không đáng bị bỏ"


# ---------------------------------------------------- overhead đo từ `usage`


def test_chua_do_duoc_gi_thi_ngan_sach_nhu_cu() -> None:
    """Không provider nào báo usage thì hành vi phải y hệt trước đây."""
    events = _flat(_turn("a", size=100), _turn("b", size=100))
    session = Session(events=list(events), max_tokens=2_000)
    assert len(session.to_messages()) == len(events)


def test_usage_cua_api_lam_ngan_sach_chat_lai() -> None:
    """System prompt + tool schema không nằm trong message list nhưng vẫn tốn chỗ.

    Message list ở đây chỉ đoán ~260 token, mà API báo request thật tốn 2500.
    Phần dư 2240 chính là hai thứ kia — và nó phải vào ngân sách, nếu không thì
    harness tưởng còn thừa chỗ trong khi đã sát trần.
    """
    events = _flat(_turn("a", size=100), _turn("b", size=100))
    session = Session(events=list(events), max_tokens=2_000)
    session.append({"type": "assistant", "content": "", "tool_calls": [],
                    "prompt_tokens": 2_500})

    kept = [m.get("content") or "" for m in session.to_messages()]
    assert not any("a" * 100 in content for content in kept), "turn cũ phải bị bỏ"
    assert any("b" * 100 in content for content in kept), "turn mới nhất vẫn giữ"
    # Và log thì không mất gì cả.
    assert len(session.events) == len(events) + 1


def test_prompt_tokens_none_thi_quay_ve_doan_thuan() -> None:
    """ReplayLLM không có request nào để đo — không được vì thế mà hỏng."""
    events = _flat(_turn("a", size=100))
    session = Session(events=list(events), max_tokens=2_000)
    session.append({"type": "assistant", "content": "x", "tool_calls": [],
                    "prompt_tokens": None})
    assert len(session.to_messages()) == len(events) + 1


def test_log_cu_khong_co_field_prompt_tokens_van_doc_duoc() -> None:
    """Log ghi trước thay đổi này không có key đó. `resume` phải chạy được."""
    session = Session(events=[], max_tokens=2_000)
    session.append({"type": "user", "content": "chào"})
    session.append({"type": "assistant", "content": "ừ", "tool_calls": []})
    assert len(session.to_messages()) == 2


def test_hieu_chinh_duoc_phep_am() -> None:
    """Bộ đoán cố ý đoán THỪA, nên hiệu chỉnh thường âm — và phải được phép âm.

    Đo thật trên một phiên `research`: API báo 4147 token cho một message list
    mà bộ đoán tự tính 6703. Chặn hiệu chỉnh ở 0 là vứt bỏ phép neo đúng lúc
    nó cần nhất, và harness quay lại tin một con số cao hơn thực tế 60%.
    """
    events = _tool_turn("a", "x" * 15_000)
    session = Session(events=list(events), max_tokens=60_000,
                      max_tool_result_chars=20_000)
    doan = session._guess(events)
    assert doan > 4_000, "dựng test sai: phải đủ lớn để bộ đoán vọt lên"

    session.append({"type": "assistant", "content": "", "tool_calls": [],
                    "prompt_tokens": 4_147})
    # Neo đúng: ước lượng của chính tập event vừa đo phải khớp số API báo.
    assert session._estimate(session._for_model(events)) == 4_147


def test_resume_hoc_lai_phep_neo_tu_log(tmp_path) -> None:
    """Số API đã đo nằm NGAY TRONG LOG — resume mà bỏ qua là tự làm mình mù.

    Đo thật trên một log `research`: không chạy lại phép neo thì session ước
    lượng 11167 token cho một request mà API đã báo 8954, thừa 25%, và hệ quả
    là bỏ turn cũ sớm hơn cần thiết ngay ở request đầu sau resume.

    Tool result ở đây CỐ Ý vượt trần: phép neo phải chiếu log qua đúng chính
    sách cắt đang chạy, nên ngân sách phải tới nơi TRƯỚC khi neo. Dựng session
    rồi mới gán ngân sách thì neo chạy trên bản không cắt — đo thật ra -27194,
    và ước lượng request kế tiếp thành số âm.
    """
    log_path = tmp_path / "cu.jsonl"
    goc = Session(log_path=log_path, max_tokens=60_000, max_tool_result_chars=20_000)
    for event in _tool_turn("a", "x" * 50_000):
        goc.append(event)
    goc.append({"type": "assistant", "content": "", "tool_calls": [],
                "prompt_tokens": 4_147})

    tiep = Session.resume(log_path, max_tokens=60_000, max_tool_result_chars=20_000)
    assert tiep._calibration == goc._calibration
    assert tiep._estimate(tiep._for_model(tiep.events[:-1])) == 4_147


def test_log_khong_co_so_do_nao_thi_resume_khong_neo() -> None:
    """Log cũ, hoặc log của `--replay`: không có gì để neo, và đó là hợp lệ."""
    session = Session(events=[{"type": "user", "content": "chào"}])
    session._recalibrate()
    assert session._calibration == 0
