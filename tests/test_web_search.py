"""Test cho web_search — phần quyết định "gửi gì cho model", không gọi mạng.

Chia thẳng theo đường biên của file tool: `_format` là hàm thuần chứa toàn bộ
quyết định về output nên test trực tiếp; phần gọi HTTP mỏng và chỉ chứng minh
được bằng một lần chạy thật, không bằng mock.
"""

from __future__ import annotations

import pytest

from mini_harness.profiles import RESEARCH
from mini_harness.tools.registry import ToolRegistry
from mini_harness.tools.web_search import _MAX_RAW_CHARS, _format, web_search_tool


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(web_search_tool("key-gia"))
    return registry


# ---------------------------------------------------------------- khai báo tool


def test_query_la_bat_buoc():
    schema = web_search_tool("key-gia").parameters
    assert schema["required"] == ["query"]
    assert set(schema["properties"]) == {"query", "max_results", "fetch_content"}


def test_fetch_content_noi_ro_cai_gia_cua_no():
    """Cờ ngân sách mà mô tả suông thì model sẽ bật nó mọi lúc."""
    description = web_search_tool("key-gia").parameters["properties"]["fetch_content"]["description"]
    assert "context" in description


@pytest.mark.asyncio
async def test_thieu_query_bao_loi_truoc_khi_cham_mang():
    """Validate chặn trước, nên test này chạy được không cần API key lẫn Internet."""
    result = await _registry().execute("web_search", "{}")
    assert result.is_error is True
    assert "query" in result.content


# ----------------------------------------------------------------- _format


def test_khong_co_ket_qua_khong_phai_la_loi():
    """Rỗng là kết quả hợp lệ — và phải gợi ý model đổi từ khoá, không thử lại."""
    out = _format("abcxyz", [], fetch_content=False)
    assert "abcxyz" in out
    assert "different" in out or "broader" in out


def test_snippet_mode_khong_dung_raw_content():
    out = _format("q", [{"title": "T", "url": "U", "content": "ngắn", "raw_content": "DÀI" * 100}], False)
    assert "ngắn" in out
    assert "DÀI" not in out


def test_fetch_content_mode_dung_raw_content():
    out = _format("q", [{"title": "T", "url": "U", "content": "ngắn", "raw_content": "đầy đủ"}], True)
    assert "đầy đủ" in out


def test_raw_content_dai_bi_cat_va_noi_ra_la_da_cat():
    """Model phải phân biệt "trang nói có thế" với "trang bị cắt"."""
    out = _format("q", [{"title": "T", "url": "U", "raw_content": "x" * (_MAX_RAW_CHARS + 500)}], True)
    assert "truncated" in out
    assert len(out) < _MAX_RAW_CHARS + 300


def test_raw_content_null_roi_ve_snippet():
    """Tavily trả raw_content=null khi không tải được trang — đừng trả khối rỗng."""
    out = _format("q", [{"title": "T", "url": "U", "content": "snippet", "raw_content": None}], True)
    assert "snippet" in out


def test_moi_ket_qua_deu_co_url():
    """Persona bắt trích nguồn, nên URL phải luôn có mặt để trích."""
    out = _format("q", [{"title": "A", "url": "http://a", "content": "x"},
                        {"title": "B", "url": "http://b", "content": "y"}], False)
    assert "http://a" in out and "http://b" in out


# ------------------------------------------------------------------- wiring


def test_research_cam_web_search_va_write_file(monkeypatch):
    from mini_harness.app import build_tools

    monkeypatch.setenv("TAVILY_API_KEY", "key-gia")
    names = {schema["name"] for schema in build_tools(RESEARCH.tools).schemas()}
    assert names == {"web_search", "write_file"}


def test_thieu_api_key_thi_dung_ngay_chu_khong_bo_tool(monkeypatch):
    """Im lặng bỏ tool thì agent vẫn trả lời — bằng trí nhớ, và không ai nhìn ra."""
    from mini_harness.app import build_tools

    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    with pytest.raises(ValueError, match="TAVILY_API_KEY"):
        build_tools(RESEARCH.tools)


def test_agent_khong_can_search_thi_khong_doi_key(monkeypatch):
    from mini_harness.app import build_tools
    from mini_harness.profiles import GENERAL

    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    assert {s["name"] for s in build_tools(GENERAL.tools).schemas()} == set(GENERAL.tools)
