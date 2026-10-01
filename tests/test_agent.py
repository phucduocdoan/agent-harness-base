"""Một loại agent là dữ liệu: đổi profile phải đổi hành vi mà không đổi code."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from mini_harness.app import build_system_prompt, build_tools
from mini_harness.context import time_context, workspace_context
from mini_harness.core.agent import AgentProfile
from mini_harness.core.prompt import assemble
from mini_harness.core.session import Session
from mini_harness.profiles import GENERAL, TUTOR


# ------------------------------------------------------------------ assemble


def test_assemble_giu_thu_tu_va_bo_section_rong():
    assert assemble("A", None, "", "   ", "B") == "A\n\nB"


def test_assemble_khong_section_nao_thi_ra_chuoi_rong():
    assert assemble(None, "") == ""


# ------------------------------------------------------------------- context


def test_time_context_noi_la_luc_bat_dau_phien_chu_khong_phai_bay_gio():
    # Prompt được ráp một lần rồi giữ nguyên cả phiên, nên câu chữ phải là câu
    # còn đúng sau hai tiếng nữa.
    text = time_context(datetime(2026, 1, 2, 3, 4, tzinfo=timezone.utc))
    assert text.startswith("Session started at 2026-01-02 03:04")
    assert "current time" not in text.lower()


def test_workspace_context_chan_so_file_liet_ke(tmp_path: Path):
    for index in range(5):
        (tmp_path / f"f{index}.txt").write_text("x", encoding="utf-8")
    text = workspace_context(tmp_path, limit=2)
    assert "f0.txt, f1.txt" in text
    assert "f4.txt" not in text
    assert "+3 more" in text


def test_workspace_context_khi_thu_muc_chua_ton_tai(tmp_path: Path):
    assert "does not exist yet" in workspace_context(tmp_path / "chua-co")


# -------------------------------------------------------------- tool per agent


def test_tutor_khong_cam_tool_nao():
    # Tutor không tool là nội dung thiết kế, không phải thiếu sót: gia sư cầm
    # calculator sẽ tự bấm ra đáp án thay cho học trò.
    assert build_tools(TUTOR.tools).schemas() == []


def test_general_cam_dung_tool_minh_khai_bao():
    names = {schema["name"] for schema in build_tools(GENERAL.tools).schemas()}
    assert names == set(GENERAL.tools)


def test_profile_goi_tool_khong_co_thi_fail_ngay_luc_dung():
    # Im lặng ở đây thì triệu chứng về sau là "model tự dưng ngu đi".
    with pytest.raises(ValueError, match="khong-ton-tai|không có"):
        build_tools(("calculator", "khong-ton-tai"))


# ------------------------------------------------------------ system prompt


def test_prompt_cua_hai_agent_khac_nhau_o_persona():
    assert TUTOR.persona in build_system_prompt(TUTOR)
    assert GENERAL.persona in build_system_prompt(GENERAL)


def test_chi_agent_ghi_duoc_file_moi_nghe_ve_workspace():
    # Kể cho agent không có write_file về thư mục nó không đụng được là mời nó thử.
    assert "Workspace:" in build_system_prompt(GENERAL)
    assert "Workspace:" not in build_system_prompt(TUTOR)


def test_them_agent_moi_khong_can_sua_code_nao():
    # Phép thử của cả thiết kế: một profile dựng tại chỗ trong test này cũng
    # chạy qua đúng các hàm mà agent "thật" đi qua.
    adhoc = AgentProfile(name="adhoc", persona="You are terse.", tools=("calculator",))
    assert "You are terse." in build_system_prompt(adhoc)
    assert [s["name"] for s in build_tools(adhoc.tools).schemas()] == ["calculator"]


# ------------------------------------------------------------------- session


def test_event_agent_nam_trong_log_nhung_khong_len_wire():
    session = Session(events=[
        {"type": "agent", "name": "tutor"},
        {"type": "user", "content": "chào"},
    ])
    assert len(session.events) == 2
    assert session.to_messages() == [{"role": "user", "content": "chào"}]


def test_event_la_khong_biet_van_con_bao_loi():
    # Bỏ qua `agent` là một ngoại lệ CÓ TÊN, không phải nới lỏng kiểm tra.
    session = Session(events=[{"type": "khong-biet"}])
    with pytest.raises(ValueError, match="không biết"):
        session.to_messages()
