"""Test cho chỗ NỐI DÂY (`app.py`) — không phải cho logic nào cả.

`app.py` là composition root: file duy nhất biết đủ mọi implementation. Logic
của nó gần như bằng không, nên lâu nay nó không có test. Nhưng một lỗi có thật
đã nằm đúng ở đây và chỉ lộ ra khi chạy live: cả hội thoại lẫn phép nén dùng
CHUNG một provider, mà provider đó mang theo kênh hiển thị — nên bản tóm tắt
nội bộ bị in thẳng ra terminal, ở đường tự động thì còn dính liền vào câu trả
lời đang stream.

Không test nào ở tầng dưới bắt được: `core/` và `cli/` đều nhận provider từ
ngoài, nên với chúng việc hai vai trùng một object là hợp lệ. Sai ở lắp ráp
thì phải test ở chỗ lắp ráp.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from mini_harness import app
from mini_harness.core.agent import AgentProfile
from mini_harness.core.session import Session
from mini_harness.core.types import AssistantMessage
from fakes import FakeLLM


@pytest.mark.asyncio
async def test_phep_nen_dung_provider_khong_gan_man_hinh(
    monkeypatch: Any, capsys: Any
) -> None:
    """Provider của phép nén phải được dựng KHÔNG có `on_text`."""
    dung: list[dict[str, Any]] = []

    def ghi_lai(**kwargs: Any) -> FakeLLM:
        dung.append(kwargs)
        return FakeLLM([AssistantMessage(text="xong")])

    monkeypatch.setattr(app, "PROVIDERS", {"--azure": ghi_lai})
    monkeypatch.setattr(app.sys, "argv", ["mini_harness", "--azure", "2+2 là mấy"])
    assert await app.main() == 0

    assert len(dung) == 2, "phải dựng hai provider: một cho hội thoại, một để nén"
    hoi_thoai, tom_tat = dung
    assert hoi_thoai.get("on_text") is not None, "hội thoại thì PHẢI stream ra màn hình"
    assert "on_text" not in tom_tat, "phép nén thì KHÔNG được có kênh hiển thị"


# ------------------------------------------------------------------- sub-agent


def test_log_cua_con_nam_canh_log_cua_cha() -> None:
    """`run.jsonl` -> `run.task-1.jsonl`. File RIÊNG, không trộn vào log cha."""
    assert app._sub_log(Path("run.jsonl"), 1) == Path("run.task-1.jsonl")
    assert app._sub_log(Path("/tmp/a/b.jsonl"), 3) == Path("/tmp/a/b.task-3.jsonl")
    assert app._sub_log(None, 1) is None, "không có --session thì con cũng không ghi đĩa"


def test_con_co_ngan_sach_rieng_khong_chia_phan_voi_cha() -> None:
    """Đây là NỘI DUNG của cả tính năng: ngân sách của con là một ngân sách mới.

    Cửa sổ ngữ cảnh là tài nguyên theo từng request, nên con khởi đầu từ 0 với
    trọn `MAX_TOKENS`, không phải phần còn thừa của cha. Con số bằng đúng của
    cha chứ không nhỏ hơn — chia đôi ngân sách mới là hiểu sai bài toán.
    """
    runs: list[Session] = []
    session, _tools, _system = app.build_spawn(runs=runs)("research")

    assert session.max_tokens == app.MAX_TOKENS
    assert session.max_tool_result_chars == app.MAX_TOOL_RESULT_CHARS
    assert session.events == [{"type": "agent", "name": "research"}], (
        "con bắt đầu từ log TRỐNG — không có một chữ nào của hội thoại cha"
    )


@pytest.mark.asyncio
async def test_tool_can_duyet_o_cha_thi_o_con_van_can_duyet() -> None:
    """Uỷ quyền KHÔNG được là đường leo thang quyền.

    Kiểm bằng HÀNH VI chứ không bằng cách ngó vào `registry._approver`: cái
    đáng bảo đảm là tool bị từ chối thật, không phải là một field được gán đúng.
    """
    hoi: list[str] = []

    async def tu_choi(name: str, _args: dict[str, Any]) -> str:
        hoi.append(name)
        return "user nói không"

    _session, tools, _system = app.build_spawn(runs=[], approver=tu_choi)("research")
    ket_qua = await tools.execute("write_file", '{"path": "x.txt", "content": "y"}')

    assert hoi == ["write_file"], "con phải đi qua đúng kênh xin phép của cha"
    assert ket_qua.is_error and "was denied" in ket_qua.content


def test_profile_duoc_uy_quyen_khong_duoc_cam_task(monkeypatch: Any) -> None:
    """Chặn độ sâu, và chặn NGAY LÚC KHỞI ĐỘNG.

    Để nó chỉ lộ ra lúc chạy nghĩa là phát hiện một cây agent đệ quy sau khi đã
    đốt vài lượt API — mà cây sâu bao nhiêu thì đọc `profiles.py` là biết, nên
    không có lý do gì phải đợi tới đó.
    """
    monkeypatch.setitem(
        app.PROFILES, "research",
        AgentProfile(name="research", persona="x", tools=("task",)),
    )
    with pytest.raises(ValueError, match="không được cầm task"):
        app.build_tools(
            ("task",), spawn=lambda _name: (None, None, ""),  # type: ignore[arg-type,return-value]
            sub_llm=object(), sub_runs=[],
        )


def test_replay_mot_lan_uy_quyen_thi_dung_han_chu_khong_chay_that() -> None:
    """`--replay --agent lead` phải DỪNG, vì uỷ quyền không replay được.

    Log đang phát lại chỉ chứa câu trả lời cuối của con; cả hội thoại của nó
    nằm ở file khác. Cho chạy tiếp thì `task` sẽ gọi model THẬT giữa một phiên
    replay vốn hứa là không chạm API.
    """
    with pytest.raises(ValueError, match="không replay được"):
        app.build_tools(("task",))
