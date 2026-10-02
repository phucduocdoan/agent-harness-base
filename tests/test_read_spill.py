"""Test cho read_spill — nửa còn lại của việc cắt tool result.

Phép chiếu cắt rồi để lại locator; tool này cầm locator đọc ngược vào log.
Test ở đây đi qua `ToolRegistry.execute` chứ không gọi thẳng hàm: validate
schema là một phần của hợp đồng với model, và model thì gửi JSON.
"""

from __future__ import annotations

import pytest

from mini_harness.core.session import Session
from mini_harness.tools.read_spill import read_spill_tool
from mini_harness.tools.registry import ToolRegistry

BODY = "ĐẦU" + "".join(f"<{i:05d}>" for i in range(5_000)) + "ĐUÔI"

# Nội dung riêng cho test search: hai chỗ khớp cách xa nhau, bọc giữa đệm đủ
# dài để chỗ khớp không nằm ở offset 0 hay cuối chuỗi — một test search mà
# chỗ khớp lại trùng biên chuỗi thì không bắt được lỗi off-by-one.
NOI_DUNG_SEARCH = (
    "mở đầu, " + "đệm " * 50 + "NEEDLE-FIRST ở đây" + " giữa " * 50
    + "NEEDLE-SECOND ở đây" + " đệm " * 50 + "kết thúc"
)


def _session(content: str = BODY) -> Session:
    return Session(
        events=[
            {"type": "user", "content": "hỏi"},
            {"type": "assistant", "content": "", "tool_calls": [
                {"id": "call_1", "name": "web_search", "arguments": "{}"}]},
            {"type": "tool_result", "call_id": "call_1", "name": "web_search",
             "content": content, "is_error": False},
        ],
        max_tool_result_chars=1_000,
    )


def _registry(session: Session) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(read_spill_tool(session))
    return registry


# --------------------------------------------------------------- đọc được gì


@pytest.mark.asyncio
async def test_doc_duoc_dung_khuc_phep_chieu_da_giau():
    """Phép thử của cả tính năng: khúc KHÔNG lên wire vẫn lấy lại được."""
    session = _session()
    len_gui = len([m for m in session.to_messages() if m["role"] == "tool"][0]["content"])
    assert len_gui < len(BODY), "dựng test sai: phải có cắt thì mới có gì để đọc lại"

    result = await _registry(session).execute(
        "read_spill", '{"call_id": "call_1", "offset": 5000, "length": 100}')
    assert result.is_error is False
    assert BODY[5_000:5_100] in result.content


@pytest.mark.asyncio
async def test_mac_dinh_doc_tu_dau():
    result = await _registry(_session()).execute("read_spill", '{"call_id": "call_1"}')
    assert BODY[:4_000] in result.content


@pytest.mark.asyncio
async def test_noi_ro_con_lai_bao_nhieu_va_offset_ke_tiep():
    """Thiếu nó thì model phải tự cộng, và cộng sai là bỏ qua một khúc."""
    result = await _registry(_session()).execute(
        "read_spill", '{"call_id": "call_1", "offset": 0, "length": 100}')
    assert "offset=100" in result.content
    assert str(len(BODY) - 100) in result.content


@pytest.mark.asyncio
async def test_doc_toi_cuoi_thi_noi_la_het():
    """"Còn 0 ký tự" và "hết rồi" là hai câu khác nhau với model."""
    result = await _registry(_session("ngắn")).execute(
        "read_spill", '{"call_id": "call_1"}')
    assert "End of this result" in result.content


# ------------------------------------------------------------------ trần, lỗi


@pytest.mark.asyncio
async def test_xin_qua_tran_thi_cat_xuong_chu_khong_bao_loi():
    """Vừa cắt context xong lại để model kéo cả trang về là tự huỷ việc vừa làm.

    Nhưng xin quá KHÔNG phải lỗi: cắt xuống rồi nói ra còn hơn bắt model đoán
    lại con số đúng.
    """
    result = await _registry(_session()).execute(
        "read_spill", '{"call_id": "call_1", "length": 999999}')
    assert result.is_error is False
    assert "0-16000" in result.content


@pytest.mark.asyncio
async def test_call_id_khong_co_thi_bao_loi_chi_duong():
    """Model bịa call_id là chuyện thường — message phải nói lấy nó ở đâu."""
    result = await _registry(_session()).execute(
        "read_spill", '{"call_id": "bia-ra"}')
    assert result.is_error is True
    assert "call_id" in result.content


@pytest.mark.asyncio
async def test_offset_qua_cuoi_noi_ro_cuc_dai_la_bao_nhieu():
    result = await _registry(_session("ngắn")).execute(
        "read_spill", '{"call_id": "call_1", "offset": 9999}')
    assert result.is_error is True
    assert "3" in result.content, "'ngắn' dài 4 ký tự -> offset lớn nhất là 3"


@pytest.mark.asyncio
async def test_thieu_call_id_bi_chan_boi_schema():
    result = await _registry(_session()).execute("read_spill", "{}")
    assert result.is_error is True
    assert "call_id" in result.content


# ------------------------------------------------------------------ bất biến


@pytest.mark.asyncio
async def test_tool_khong_bao_gio_ghi_vao_log():
    """Một tool ghi được vào log sẽ làm hỏng đúng cái khiến replay tin được."""
    session = _session()
    truoc = list(session.events)
    await _registry(session).execute("read_spill", '{"call_id": "call_1"}')
    assert session.events == truoc


# --------------------------------------------------------------- search (grep)


@pytest.mark.asyncio
async def test_search_tim_duoc_nhieu_cho_khop():
    """`query` là `grep`: trả vị trí, không trả nội dung."""
    session = _session(NOI_DUNG_SEARCH)
    result = await _registry(session).execute(
        "read_spill", '{"call_id": "call_1", "query": "NEEDLE"}')
    assert result.is_error is False
    assert "2 match" in result.content
    assert f"offset {NOI_DUNG_SEARCH.find('NEEDLE-FIRST')}" in result.content
    assert f"offset {NOI_DUNG_SEARCH.find('NEEDLE-SECOND')}" in result.content
    # VỊ TRÍ, không phải nội dung đầy đủ: search không được thay thế read.
    assert "kết thúc" not in result.content


@pytest.mark.asyncio
async def test_search_khong_phan_biet_hoa_thuong():
    session = _session(NOI_DUNG_SEARCH)
    result = await _registry(session).execute(
        "read_spill", '{"call_id": "call_1", "query": "needle-first"}')
    assert result.is_error is False
    assert f"offset {NOI_DUNG_SEARCH.find('NEEDLE-FIRST')}" in result.content


@pytest.mark.asyncio
async def test_search_khong_khop_khong_phai_loi():
    """Không tìm thấy gì là một câu trả lời hợp lệ, không phải lỗi."""
    session = _session(NOI_DUNG_SEARCH)
    result = await _registry(session).execute(
        "read_spill", '{"call_id": "call_1", "query": "khong-ton-tai-o-dau-ca"}')
    assert result.is_error is False
    assert "No match" in result.content


@pytest.mark.asyncio
async def test_search_qua_20_ket_qua_thi_noi_ro_con_bao_nhieu():
    noi_dung = "NEEDLE".join(["đệm"] * 26)  # 25 chỗ khớp
    session = _session(noi_dung)
    result = await _registry(session).execute(
        "read_spill", '{"call_id": "call_1", "query": "NEEDLE"}')
    assert result.is_error is False
    assert "25 match" in result.content
    assert result.content.count("offset ") == 20
    assert "5 more match" in result.content
    assert "narrow your query" in result.content


@pytest.mark.asyncio
async def test_search_call_id_sai_van_bao_loi_chi_duong():
    result = await _registry(_session(NOI_DUNG_SEARCH)).execute(
        "read_spill", '{"call_id": "bia-ra", "query": "NEEDLE"}')
    assert result.is_error is True
    assert "call_id" in result.content


@pytest.mark.asyncio
async def test_offset_tu_search_doc_duoc_dung_bang_read():
    """Nghiệm thu chính: offset search trả ra phải dùng thẳng được cho đọc."""
    session = _session(NOI_DUNG_SEARCH)
    tim = await _registry(session).execute(
        "read_spill", '{"call_id": "call_1", "query": "NEEDLE-SECOND"}')
    offset = int(tim.content.split("offset ")[1].split(":")[0])
    assert offset == NOI_DUNG_SEARCH.find("NEEDLE-SECOND")

    doc = await _registry(session).execute(
        "read_spill",
        f'{{"call_id": "call_1", "offset": {offset}, "length": 13}}')
    assert doc.is_error is False
    assert "NEEDLE-SECOND" in doc.content
