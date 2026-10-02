"""Test cho compaction — cắt ngữ cảnh ở phép chiếu, không đụng vào log.

Bất biến quan trọng nhất không phải "có cắt", mà là "cắt xong request vẫn
HỢP LỆ": mọi `tool_call` còn lại phải còn `tool_result` của nó. Cắt lẻ từng
event sẽ phá đúng chỗ đó, nên ở đây đơn vị cắt là trọn một turn.
"""

from __future__ import annotations

import pytest

from mini_harness.core.compaction import _INSTRUCTION, maybe_compact
from mini_harness.core.session import Session
from mini_harness.core.types import AssistantMessage, ToolCall
from mini_harness.llm.stream import to_wire_tools
from fakes import FakeLLM


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


# ------------------------------------------------- nén bằng summary do model viết


def _compaction(summary: str, covers: int) -> dict:
    return {"type": "compaction", "content": summary, "covers": covers}


def test_summary_dung_thay_khuc_dau_chu_khong_phai_bi_bo() -> None:
    """Khác biệt duy nhất đáng kể giữa nén và bỏ: nội dung cũ còn nói được."""
    # covers=2 = trọn turn "a" (user + assistant), turn "b" ở ngoài.
    events = _flat(_turn("a"), _turn("b")) + [_compaction("A hỏi về X", covers=2)]
    messages = Session(events=events).to_messages()

    assert messages[0]["role"] == "user"
    assert "A hỏi về X" in messages[0]["content"]
    assert "<compacted-summary>" in messages[0]["content"]
    # Turn "b" nằm sau `covers` nên phải còn NGUYÊN VĂN.
    assert any("hỏi b" in m["content"] for m in messages[1:])
    # Turn "a" nằm trong vùng bị nén nên không còn nguyên văn ở đâu cả.
    assert not any("hỏi a" in m["content"] for m in messages[1:])


def test_event_compaction_khong_bao_gio_di_len_wire() -> None:
    """Nó là event metadata. Chiếu nguyên nó đi là gửi JSON thô cho model."""
    events = _flat(_turn("a")) + [_compaction("tóm tắt", covers=2)] + _flat(_turn("b"))
    for message in Session(events=events).to_messages():
        assert message["role"] in {"user", "assistant", "tool"}
        assert "covers" not in message["content"]


def test_nen_lan_hai_thi_ban_sau_thang() -> None:
    """Nén lại không sửa bản cũ — nó append bản mới với `covers` lớn hơn.

    Bản cũ rơi vào đúng vùng bản mới đứng thay, nên nó tự biến mất khỏi phép
    chiếu. Không có bước hoà giải nào, và log vẫn append-only.
    """
    events = (
        _flat(_turn("a"), _turn("b"))
        + [_compaction("TÓM TẮT CŨ", covers=4)]
        + _flat(_turn("c"))
        + [_compaction("TÓM TẮT MỚI", covers=7)]
    )
    messages = Session(events=events).to_messages()
    noi_dung = " ".join(m["content"] for m in messages)
    assert "TÓM TẮT MỚI" in noi_dung
    assert "TÓM TẮT CŨ" not in noi_dung


def test_chua_cham_nguong_thi_khong_nen() -> None:
    """Nén tốn một lần gọi model — không được nổ khi chưa cần."""
    events = _flat(_turn("a", size=100), _turn("b", size=100))
    rong_rai = Session(events=events, max_tokens=100_000)
    assert rong_rai.plan_compaction() is None


def test_khong_co_ngan_sach_thi_khong_bao_gio_nen() -> None:
    assert Session(events=_flat(_turn("a", size=5_000))).plan_compaction() is None


def test_vuot_nguong_thi_nen_va_giu_lai_turn_moi_nhat() -> None:
    events = _flat(*[_turn(tag, size=300) for tag in "abcde"])
    session = Session(events=events, max_tokens=800)
    plan = session.plan_compaction()

    assert plan is not None
    assert 0 < plan.covers < len(events)
    # Điểm cắt phải rơi đúng đầu một turn, nếu không sẽ sinh tool_result mồ côi.
    assert events[plan.covers]["type"] == "user"
    # Turn cuối luôn ở ngoài vùng nén.
    assert plan.covers <= len(events) - len(_turn("e", size=300))


def test_vung_nen_la_tien_to_byte_for_byte_cua_request_vua_gui() -> None:
    """Đây là cái làm prefix cache của provider còn dùng lại được.

    Giữ được là nhờ `_render` ánh xạ từng event độc lập: chiếu-rồi-lấy-tiền-tố
    bằng đúng lấy-tiền-tố-rồi-chiếu. Mất tính chất đó thì mỗi lần nén phải trả
    tiền cho toàn bộ hội thoại ở giá uncached.

    Ngân sách ở đây đủ rộng để `_within_budget` chưa phải bỏ gì — đó là trạng
    thái mà ngưỡng 0.8 sinh ra, và là trạng thái compaction chạy trong thực tế.
    Trường hợp đã phải bỏ turn thì xem test kế tiếp.
    """
    events = _flat(*[_turn(tag, size=300) for tag in "abcde"])
    session = Session(events=events, max_tokens=1_500)
    assert len(session.to_messages()) == len(events), "chưa được bỏ turn nào"

    plan = session.plan_compaction()
    assert plan is not None
    assert plan.messages == session.to_messages()[: len(plan.messages)]


def test_da_phai_bo_turn_thi_van_nen_tu_LOG_chu_khong_tu_request() -> None:
    """Ngân sách quá chật nên bỏ turn nổ trước nén. Vùng nén lấy từ ĐÂU?

    Lấy từ log. Tức là bản tóm tắt nhìn thấy cả những turn mà request vừa rồi
    ĐÃ bỏ — nó kéo lại được phần model vừa mất. Cái giá là mất tính tiền tố,
    nên lần gọi tóm tắt đó không hưởng prefix cache. Đổi một lần trả giá
    uncached lấy phần nội dung lẽ ra mất hẳn: đáng.
    """
    events = _flat(*[_turn(tag, size=300) for tag in "abcde"])
    session = Session(events=events, max_tokens=800)
    assert len(session.to_messages()) < len(events), "ngân sách này phải làm bỏ turn"

    plan = session.plan_compaction()
    assert plan is not None
    noi_dung = " ".join(m["content"] for m in plan.messages)
    assert "hỏi a" in noi_dung, "turn đã bị bỏ khỏi request vẫn phải vào bản tóm tắt"


def test_mot_turn_khong_the_che_doi_thi_khong_nen() -> None:
    """Một turn duy nhất ngốn hết ngân sách: compaction không chẻ được nó.

    Đối chiếu compaction/: "balanced summary compaction cannot split one
    indivisible unit". Trả None còn hơn trả một điểm cắt làm hỏng cặp
    tool_call/tool_result.
    """
    session = Session(events=_flat(_turn("a", size=5_000)), max_tokens=500)
    assert session.plan_compaction() is None


def test_khong_nen_lai_phan_da_nen() -> None:
    """Nén lại cái đã nén là trả tiền model hai lần cho cùng một đoạn text.

    Phải chạy tới lần nén THỨ HAI mới thử được điều này, nên hội thoại phải
    phình tiếp sau lần nén đầu — y như lúc chạy thật.
    """
    session = Session(events=_flat(*[_turn(tag, size=300) for tag in "abcde"]),
                      max_tokens=1_500)
    dau = session.plan_compaction()
    assert dau is not None
    assert session.apply_compaction(dau, "TÓM TẮT ĐẦU") is None

    for event in _flat(*[_turn(tag, size=300) for tag in "fghij"]):
        session.append(event)
    sau = session.plan_compaction()

    assert sau is not None
    assert sau.covers > dau.covers, "lần sau phải nén thêm, không nén lại chỗ cũ"
    noi_dung = " ".join(m["content"] for m in sau.messages)
    assert "TÓM TẮT ĐẦU" in noi_dung, "bản tóm tắt cũ phải có mặt để model gộp vào"
    assert "hỏi a" not in noi_dung, "phần đã nén không được gửi lại nguyên văn"


def test_summary_khong_lam_ngan_lai_thi_bi_tu_choi() -> None:
    """Một bản "tóm tắt" dài hơn bản gốc làm ngữ cảnh PHÌNH ra.

    Đối chiếu compaction/: "rejects a summary that does not shrink its source".
    """
    events = _flat(*[_turn(tag, size=300) for tag in "abcde"])
    session = Session(events=events, max_tokens=800)
    plan = session.plan_compaction()
    assert plan is not None

    assert "did not shrink" in (session.apply_compaction(plan, "y" * 100_000) or "")
    assert "empty summary" in (session.apply_compaction(plan, "") or "")
    assert all(event["type"] != "compaction" for event in session.events)
    assert session.apply_compaction(plan, "tóm tắt ngắn gọn") is None
    assert session.events[-1]["type"] == "compaction"


def test_nen_xong_thi_request_nho_di_that() -> None:
    """Phép thử cuối cùng: nén phải làm giảm con số, không chỉ đổi hình dạng."""
    events = _flat(*[_turn(tag, size=300) for tag in "abcde"])
    session = Session(events=events, max_tokens=800)
    truoc = session._estimate(session._pruned(session._compacted(session.events)))

    plan = session.plan_compaction()
    assert plan is not None
    assert session.apply_compaction(plan, "tóm tắt a..d") is None

    sau = session._estimate(session._pruned(session._compacted(session.events)))
    assert sau < truoc


def test_resume_doc_lai_duoc_log_da_nen(tmp_path) -> None:
    """Event compaction nằm trong log, nên resume phải chiếu ra y hệt."""
    log_path = tmp_path / "nen.jsonl"
    goc = Session(log_path=log_path, max_tokens=800)
    for event in _flat(*[_turn(tag, size=300) for tag in "abcde"]):
        goc.append(event)
    plan = goc.plan_compaction()
    assert plan is not None
    assert goc.apply_compaction(plan, "tóm tắt a..d") is None

    tiep = Session.resume(log_path, max_tokens=800)
    assert tiep.to_messages() == goc.to_messages()


def test_nen_xong_van_qua_nguong_nhung_khong_con_gi_de_nen() -> None:
    """Bản tóm tắt ngắn hơn vùng gốc nhưng vẫn chưa đủ để xuống dưới ngưỡng.

    Không có chốt chặn thì lần sau `plan_compaction` trả về ĐÚNG vùng vừa nén,
    model tóm tắt lại chính bản tóm tắt của nó, rồi lặp vô hạn — mỗi vòng một
    lần gọi model. Trả `None` ở đây là nói thật: đã hết chỗ nén, phần còn lại
    là việc của `_within_budget`.
    """
    session = Session(events=_flat(*[_turn(tag, size=300) for tag in "abcde"]),
                      max_tokens=1_500)
    plan = session.plan_compaction()
    assert plan is not None
    assert session.apply_compaction(plan, "S" * 2_300) is None

    van_qua = session._estimate(session._pruned(session._compacted(session.events)))
    assert van_qua > 1_500 * Session._COMPACTION_THRESHOLD, "test này cần vẫn quá ngưỡng"
    assert session.plan_compaction() is None


# ----------------------------------------------- phần gọi model (core/compaction.py)


def _dong_lon(max_tokens: int = 1_500) -> Session:
    """Session đã vượt ngưỡng nén."""
    return Session(events=_flat(*[_turn(tag, size=300) for tag in "abcde"]),
                   max_tokens=max_tokens)


@pytest.mark.asyncio
async def test_chua_can_nen_thi_khong_goi_model() -> None:
    """Mỗi lần nén là một lần trả tiền. Không được gọi khi Session bảo chưa cần."""
    llm = FakeLLM([])
    session = Session(events=_flat(_turn("a")), max_tokens=100_000)

    assert await maybe_compact(session=session, llm=llm, system="S", tools=[]) is False
    assert llm.requests == []


@pytest.mark.asyncio
async def test_nen_xong_thi_log_co_event_va_wire_ngan_lai() -> None:
    session = _dong_lon()
    truoc = len(session.to_messages())
    llm = FakeLLM([AssistantMessage(text="checkpoint ngắn gọn về a..d")])

    assert await maybe_compact(session=session, llm=llm, system="S", tools=[]) is True
    assert session.events[-1]["type"] == "compaction"
    assert len(session.to_messages()) < truoc


@pytest.mark.asyncio
async def test_request_tom_tat_dung_system_va_tools_y_het() -> None:
    """Bớt một tool schema là làm nguội prefix cache của cả hội thoại."""
    session = _dong_lon()
    llm = FakeLLM([AssistantMessage(text="checkpoint")])
    tools = [{"name": "calculator", "description": "d", "parameters": {}}]

    await maybe_compact(session=session, llm=llm, system="PERSONA", tools=tools)

    request = llm.requests[0]
    assert request["system"] == "PERSONA"
    assert request["tools"] == to_wire_tools(tools)


@pytest.mark.asyncio
async def test_instruction_di_o_message_CUOI_cung() -> None:
    """Đặt ở đầu thì prefix hết ấm ngay từ byte đầu tiên."""
    session = _dong_lon()
    llm = FakeLLM([AssistantMessage(text="checkpoint")])

    await maybe_compact(session=session, llm=llm, system="S", tools=[])

    messages = llm.requests[0]["messages"]
    assert messages[-1] == {"role": "user", "content": _INSTRUCTION}
    assert all(_INSTRUCTION not in m["content"] for m in messages[:-1])


@pytest.mark.asyncio
async def test_model_hong_thi_turn_van_chay_tiep() -> None:
    """Nén hỏng không được làm chết turn — `_within_budget` vẫn đỡ được."""
    class LLMHong:
        async def generate(self, **_: object) -> AssistantMessage:
            raise RuntimeError("502 Bad Gateway")

    session = _dong_lon()
    assert await maybe_compact(session=session, llm=LLMHong(), system="S", tools=[]) is False
    assert all(event["type"] != "compaction" for event in session.events)
    assert session.to_messages(), "vẫn phải chiếu ra được request hợp lệ"


@pytest.mark.asyncio
async def test_nen_hong_thi_ghi_lai_ly_do_vao_log() -> None:
    """Nén hỏng mà im lặng thì mỗi lượt lại rụng một turn, không ai biết tại sao."""
    class LLMHong:
        async def generate(self, **_: object) -> AssistantMessage:
            raise RuntimeError("502 Bad Gateway")

    session = _dong_lon()
    await maybe_compact(session=session, llm=LLMHong(), system="S", tools=[])

    hong = session.events[-1]
    assert hong["type"] == "compaction_failed"
    assert "502 Bad Gateway" in hong["content"]
    # Ghi mà KHÔNG chiếu: model không cần đọc chuyện nội bộ của harness.
    assert all("502" not in m["content"] for m in session.to_messages())


@pytest.mark.asyncio
async def test_model_tra_rong_thi_tu_choi_va_ghi_lai() -> None:
    session = _dong_lon()
    llm = FakeLLM([AssistantMessage(text="   ")])

    assert await maybe_compact(session=session, llm=llm, system="S", tools=[]) is False
    hong = session.events[-1]
    assert hong["type"] == "compaction_failed"
    assert "empty summary" in hong["content"], "lý do phải nói rõ hỏng ở đâu"


@pytest.mark.asyncio
async def test_hong_lien_tiep_du_nhieu_thi_thoi_khong_thu_nua() -> None:
    """Cầu dao: hỏng vì provider thì lượt sau thường hỏng y hệt.

    Không ngắt thì mỗi lượt tốn thêm một lần gọi model cho đúng cái lỗi vừa
    rồi. Đối chiếu Claude Code 2.1.76: "stops retrying after 3 failed attempts".
    """
    class LLMHong:
        def __init__(self) -> None:
            self.lan_goi = 0

        async def generate(self, **_: object) -> AssistantMessage:
            self.lan_goi += 1
            raise RuntimeError("502 Bad Gateway")

    session = _dong_lon()
    llm = LLMHong()
    for _ in range(5):
        assert await maybe_compact(session=session, llm=llm, system="S", tools=[]) is False

    assert llm.lan_goi == Session._COMPACTION_MAX_FAILURES, "ngắt rồi thì không gọi nữa"
    so_lan_hong = sum(e["type"] == "compaction_failed" for e in session.events)
    assert so_lan_hong == Session._COMPACTION_MAX_FAILURES
    # Ngắt phép nén KHÔNG làm chết hội thoại: lưới `_within_budget` vẫn đỡ.
    assert session.to_messages()


@pytest.mark.asyncio
async def test_nen_duoc_mot_lan_thi_cau_dao_dong_lai() -> None:
    """Đếm là đếm LIÊN TIẾP: một lần thành công xoá sạch nợ cũ.

    Hỏng vì mạng chập thì không được tính dồn vào lần hỏng sáu tiếng sau đó.
    """
    class LLMChapChon:
        def __init__(self) -> None:
            self.lan_goi = 0

        async def generate(self, **_: object) -> AssistantMessage:
            self.lan_goi += 1
            if self.lan_goi <= 2:
                raise RuntimeError("502 Bad Gateway")
            return AssistantMessage(text="checkpoint a..d")

    session = _dong_lon()
    llm = LLMChapChon()
    assert await maybe_compact(session=session, llm=llm, system="S", tools=[]) is False
    assert await maybe_compact(session=session, llm=llm, system="S", tools=[]) is False
    assert await maybe_compact(session=session, llm=llm, system="S", tools=[]) is True

    assert session._failures_since_compaction() == 0


def test_force_bo_qua_nguong_va_cau_dao_nhung_khong_bo_qua_gi_khac() -> None:
    """`/compact` gõ tay bỏ qua ĐÚNG hai chốt, và giữ nguyên các chốt còn lại.

    Cả ngưỡng lẫn cầu dao đều chỉ tồn tại để quyết định khi nào TỰ nén — người
    dùng gõ lệnh là đã tự quyết hộ. Nhưng `retain` và chốt "không còn gì mới
    để nén" thì không liên quan gì tới chuyện ai quyết, nên `force` không được
    phá: phá là nén mất chính câu hỏi đang chạy, hoặc trả tiền model để tóm
    tắt lại bản tóm tắt vừa viết.
    """
    session = Session(events=_flat(*[_turn(tag, size=300) for tag in "abcde"]),
                      max_tokens=3_000)
    for _ in range(Session._COMPACTION_MAX_FAILURES):
        session.append({"type": "compaction_failed", "content": "502 Bad Gateway"})

    assert session.plan_compaction() is None, "chưa tới ngưỡng, lại còn đang ngắt"
    plan = session.plan_compaction(force=True)
    assert plan is not None
    assert plan.covers < len(session.events), "vùng giữ nguyên văn vẫn phải còn"

    assert session.apply_compaction(plan, "checkpoint a..e") is None
    assert session.plan_compaction(force=True) is None, (
        "nén xong rồi thì force cũng không có gì mới để nén"
    )


def test_cau_dao_song_sot_qua_resume(tmp_path) -> None:
    """Trạng thái cầu dao đọc ra từ log, nên resume không reset nó.

    Giữ bằng một biến đếm trong bộ nhớ thì resume xong là quên — hội thoại lại
    lao vào đúng chuỗi lỗi vừa thoát ra.
    """
    log_path = tmp_path / "hong.jsonl"
    goc = Session(log_path=log_path, max_tokens=1_500)
    for event in _flat(*[_turn(tag, size=300) for tag in "abcde"]):
        goc.append(event)
    for _ in range(Session._COMPACTION_MAX_FAILURES):
        goc.append({"type": "compaction_failed", "content": "502 Bad Gateway"})

    tiep = Session.resume(log_path, max_tokens=1_500)
    assert tiep._failures_since_compaction() == Session._COMPACTION_MAX_FAILURES
    assert tiep.plan_compaction() is None


@pytest.mark.asyncio
async def test_model_lo_goi_tool_nhung_van_viet_du_thi_van_nhan() -> None:
    """Chỉ phần text đi vào checkpoint — tool call bị bỏ qua, không phải lỗi.

    Đối chiếu compaction-basic: "only returned text enters the checkpoint,
    excluding reasoning and tool calls". Coi là lỗi thì một lần model lỡ tay
    đủ huỷ cả phép nén dù bản tóm tắt vẫn đầy đủ.
    """
    session = _dong_lon()
    llm = FakeLLM([AssistantMessage(
        text="checkpoint đầy đủ",
        tool_calls=(ToolCall(id="call_x", name="calculator", arguments_json="{}"),),
    )])

    assert await maybe_compact(session=session, llm=llm, system="S", tools=[]) is True
    assert session.events[-1]["content"] == "checkpoint đầy đủ"


def test_instruction_khong_bao_model_giau_viec_bi_nen() -> None:
    """Ràng buộc này do content filter của provider áp, không phải do thẩm mỹ.

    Bản upstream có dòng "Do NOT mention this request, or that the conversation
    was condensed". Gửi nguyên văn lên Azure thì cả request bị chặn:
    `jailbreak: {detected: True, filtered: True}`, HTTP 400. Dò từng dòng thì
    chính dòng đó đứng MỘT MÌNH lại qua được — thứ bị bắt là ngữ cảnh: một
    instruction dựng sẵn bố cục rồi dặn model đừng nói là mình được dặn, đọc
    lên đúng dạng prompt injection.

    Nén hỏng thì không chết turn (có `compaction_failed` + `_within_budget` đỡ),
    nhưng hỏng 100% số lần thì tính năng coi như không tồn tại. Khoá lại ở đây
    vì cái bẫy là chép nguyên văn upstream về — chuyện rất dễ xảy ra.

    Việc "đừng nhắc tới checkpoint" vẫn còn, nhưng nằm ở phía model ĐỌC bản tóm
    tắt (`_CHECKPOINT_PREAMBLE`), chứ không phải phía model VIẾT nó.
    """
    assert "do not mention" not in _INSTRUCTION.lower()
    assert "condensed" not in _INSTRUCTION.lower()
    # Thứ thật sự cần vẫn phải nói ra: đây là bản ghi, không phải một lượt trả lời.
    assert "standalone record" in _INSTRUCTION
