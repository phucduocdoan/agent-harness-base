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
