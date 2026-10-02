"""Test cho tool `task` (`mini_harness/tools/task.py`) — uỷ quyền việc cho sub-agent.

Bốn bất biến cần khoá, cộng một đường hỏng và một đường định danh:
  1. Câu giao việc tới được con, và không có gì của cha đi cùng nó.
  2. Cha chỉ nhận lại đúng một kết quả, dù con đi bao nhiêu step tool.
  3. Tên agent sai bị `enum` trong schema chặn TRƯỚC KHI `execute` chạy.
  4. Huỷ giữa chừng không bị tool này nuốt, và log của con vẫn được vá xong.
  5. Con kẹt vòng lặp (MaxStepsExceeded) thành error result, không làm chết cha.
  6. Mỗi lần uỷ quyền là một session riêng, và cả hai đều vào `runs`.

Không import `app.py`: `spawn` giả dựng ngay trong từng test, như SPEC yêu cầu.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from mini_harness.core.session import Session
from mini_harness.core.types import AssistantMessage, ToolCall
from mini_harness.tools.registry import ToolRegistry, define_tool
from mini_harness.tools.task import task_tool
from fakes import FakeLLM


def _registry_voi_mot_tool(name: str, execute: Any) -> ToolRegistry:
    """Registry con tối giản, dùng lại ở nhiều test bên dưới."""
    registry = ToolRegistry()
    registry.register(
        define_tool(name=name, description="do something", parameters={}, execute=execute)
    )
    return registry


# ------------------------------------------------------- cách ly hội thoại


@pytest.mark.asyncio
async def test_con_khong_nhin_thay_hoi_thoai_cua_cha() -> None:
    """Câu giao việc tới được con, và không có gì của cha đi cùng nó.

    Nói thẳng test này khoá được gì: vế SAU (`"làm việc X"` thật sự thành
    `user_input` của con) là thứ `task.py` kiểm soát, đổi nó là test đỏ ngay.
    Vế TRƯỚC (không có dấu hiệu của cha) hôm nay không thể đỏ, vì `task.py`
    không hề cầm session cha nên không có đường nào để rò. Giữ nó không phải
    để chứng minh hôm nay, mà làm chốt cho mai: ai đó tiêm session cha vào
    `task_tool` để "truyền chút ngữ cảnh" thì đúng dòng này sẽ đỏ.
    """
    DAU_HIEU_CHA = "ký-hiệu-đặc-trưng-của-cha-39f2"
    session_cha = Session()
    session_cha.append({"type": "user", "content": DAU_HIEU_CHA})
    session_cha.append({"type": "assistant", "content": "đã rõ, để tôi xử lý", "tool_calls": []})

    llm_con = FakeLLM([AssistantMessage(text="xong việc")])

    def spawn(agent: str) -> tuple[Session, ToolRegistry, str]:
        return Session(), ToolRegistry(), "system con"

    task = task_tool(spawn=spawn, llm=llm_con, delegatable=("general",), runs=[])
    await task.execute({"agent": "general", "task": "làm việc X"})

    sent = llm_con.requests[0]["messages"]
    assert all(DAU_HIEU_CHA not in str(message) for message in sent)
    assert any("làm việc X" in str(message) for message in sent)


# ------------------------------------------------------- một kết quả duy nhất


@pytest.mark.asyncio
async def test_cha_chi_nhan_dung_mot_ket_qua() -> None:
    """Con chạy 3 step có tool nhưng cha chỉ nhận đúng một `ToolResult`.

    Đó là cả nội dung của tính năng: ba step của con gộp lại thành một dòng
    duy nhất ở phía cha. Mọi thứ ở giữa — hai tool call và hai kết quả — nằm
    trọn trong session riêng mà `spawn` dựng ra và không bao giờ tới cha.
    """
    async def lam(_args: dict[str, Any]) -> str:
        return "đã làm"

    llm_con = FakeLLM([
        AssistantMessage(tool_calls=(ToolCall("c1", "lam", "{}"),)),
        AssistantMessage(tool_calls=(ToolCall("c2", "lam", "{}"),)),
        AssistantMessage(text="kết quả cuối cùng của con"),
    ])

    def spawn(agent: str) -> tuple[Session, ToolRegistry, str]:
        return Session(), _registry_voi_mot_tool("lam", lam), "system con"

    registry = ToolRegistry()
    registry.register(task_tool(spawn=spawn, llm=llm_con, delegatable=("general",), runs=[]))

    result = await registry.execute("task", '{"agent":"general","task":"làm hộ tôi 3 bước"}')

    assert result.content == "kết quả cuối cùng của con"


# ------------------------------------------------------- chặn tên trước khi chạy


@pytest.mark.asyncio
async def test_ten_agent_khong_duoc_phep_bi_chan_truoc_khi_chay() -> None:
    """Tên profile không nằm trong `delegatable` phải bị chặn bởi `enum` của
    schema, TRƯỚC KHI `_task` kịp gọi `spawn` — nên `spawn` không được gọi lần nào.
    """
    so_lan_goi = 0

    def spawn(agent: str) -> tuple[Session, ToolRegistry, str]:
        nonlocal so_lan_goi
        so_lan_goi += 1
        return Session(), ToolRegistry(), "system con"

    registry = ToolRegistry()
    registry.register(
        task_tool(spawn=spawn, llm=FakeLLM([]), delegatable=("general",), runs=[])
    )

    result = await registry.execute("task", '{"agent":"khong-ton-tai","task":"x"}')

    assert result.is_error is True
    assert so_lan_goi == 0


# ------------------------------------------------------------------- huỷ giữa chừng


@pytest.mark.asyncio
async def test_huy_giua_chung_thi_log_con_van_hop_le() -> None:
    """Con bị huỷ trong lúc tool đang chạy.

    `CancelledError` phải bay thẳng ra khỏi `execute` — nuốt nó là biến Ctrl-C
    của người dùng thành một turn chạy tiếp âm thầm. Và session con phải đã
    được `run_turn` tự vá xong trước khi exception thoát ra, nên sau đó
    `abort_pending_tool_calls()` không còn gì để vá nữa (trả về 0).
    """
    started = asyncio.Event()

    async def hang(_args: dict[str, Any]) -> str:
        started.set()
        await asyncio.sleep(60)  # bị huỷ trong lúc này
        return "không bao giờ tới đây"

    session_con = Session()

    def spawn(agent: str) -> tuple[Session, ToolRegistry, str]:
        return session_con, _registry_voi_mot_tool("hang", hang), "system con"

    llm_con = FakeLLM([AssistantMessage(tool_calls=(ToolCall("c1", "hang", "{}"),))])
    registry = ToolRegistry()
    registry.register(task_tool(spawn=spawn, llm=llm_con, delegatable=("general",), runs=[]))

    task_dang_chay = asyncio.create_task(
        registry.execute("task", '{"agent":"general","task":"chạy đi"}')
    )
    await started.wait()
    task_dang_chay.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task_dang_chay

    assert session_con.abort_pending_tool_calls() == 0


# --------------------------------------------------------------------- hết step


@pytest.mark.asyncio
async def test_con_het_step_thanh_error_result_chu_khong_lam_chet_cha() -> None:
    """Con kẹt vòng lặp tool mãi không trả lời (`MaxStepsExceeded`) phải thành
    một `ToolResult(is_error=True)` cho cha, không phải exception bay lên làm
    chết cả turn đang chạy của cha.
    """
    async def lam(_args: dict[str, Any]) -> str:
        return "đã làm"

    # Nhiều hơn _MAX_STEPS (10) để FakeLLM không bao giờ hết kịch bản trước
    # khi run_turn tự dừng vì vượt step.
    script = [
        AssistantMessage(tool_calls=(ToolCall(f"c{i}", "lam", "{}"),)) for i in range(20)
    ]
    llm_con = FakeLLM(script)

    def spawn(agent: str) -> tuple[Session, ToolRegistry, str]:
        return Session(), _registry_voi_mot_tool("lam", lam), "system con"

    registry = ToolRegistry()
    registry.register(task_tool(spawn=spawn, llm=llm_con, delegatable=("general",), runs=[]))

    result = await registry.execute("task", '{"agent":"general","task":"loop đi"}')

    assert result.is_error is True
    assert "step" in result.content


# --------------------------------------------------------------- một session mỗi lần


@pytest.mark.asyncio
async def test_moi_lan_uy_quyen_la_mot_session_rieng() -> None:
    """Gọi `task` hai lần phải tạo ra hai session riêng biệt, cả hai đều vào
    `runs` — đó là cách `/task` xem lại được từng lần uỷ quyền.
    """
    def spawn(agent: str) -> tuple[Session, ToolRegistry, str]:
        return Session(), ToolRegistry(), "system con"

    runs: list[Session] = []
    llm_con = FakeLLM([AssistantMessage(text="xong 1"), AssistantMessage(text="xong 2")])
    registry = ToolRegistry()
    registry.register(task_tool(spawn=spawn, llm=llm_con, delegatable=("general",), runs=runs))

    await registry.execute("task", '{"agent":"general","task":"việc 1"}')
    await registry.execute("task", '{"agent":"general","task":"việc 2"}')

    assert len(runs) == 2
    assert runs[0] is not runs[1]
