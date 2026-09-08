"""End-to-end: 4 file ghép lại, chạy đủ vòng tool call.

Đây là test kiểm 2 trong 3 success criterion của V1:
  #1 model gọi tool -> execute -> trả lời cuối.
  #2 args sai schema -> không crash -> model đọc error -> tự sửa -> xong.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from mini_harness.core.loop import MaxStepsExceeded, run_turn
from mini_harness.core.session import ABORTED_BEFORE_DISPATCH, Session
from mini_harness.core.types import AssistantMessage, ToolCall
from mini_harness.tools.registry import ToolRegistry, define_tool
from fakes import FakeLLM

SYSTEM = "Bạn là trợ lý có tool."


async def _calc(args: dict[str, Any]) -> str:
    return str(round(eval(args["expression"], {"__builtins__": {}}), args.get("precision", 2)))  # noqa: S307


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        define_tool(
            name="calculator",
            description="Evaluate an arithmetic expression.",
            parameters={
                "expression": {"type": "string", "required": True, "description": "e.g. 100*1.1"},
                "precision": {"type": "integer", "description": "decimal places"},
            },
            execute=_calc,
        )
    )
    return registry


async def _run(script: list[AssistantMessage]) -> tuple[str, FakeLLM, Session]:
    model, session = FakeLLM(script), Session()
    answer = await run_turn(
        llm=model, tools=_registry(), session=session,
        system=SYSTEM, user_input="100*1.1 bằng mấy?",
    )
    return answer, model, session


# --------------------------------------------------------------- criterion #1


@pytest.mark.asyncio
async def test_tool_call_then_final_answer() -> None:
    answer, model, session = await _run([
        AssistantMessage(tool_calls=(ToolCall("c1", "calculator", '{"expression": "100*1.1"}'),)),
        AssistantMessage(text="Bằng 110.0."),
    ])
    assert answer == "Bằng 110.0."
    assert len(model.requests) == 2
    # Request thứ 2 phải chứa kết quả tool -> model mới trả lời được.
    assert {"role": "tool", "tool_call_id": "c1", "content": "110.0"} in model.requests[1]["messages"]
    assert [event["type"] for event in session.events] == [
        "user", "assistant", "tool_result", "assistant",
    ]


@pytest.mark.asyncio
async def test_tools_reach_the_model_in_wire_format() -> None:
    _, model, _ = await _run([AssistantMessage(text="ok")])
    (tool,) = model.requests[0]["tools"]
    assert tool["type"] == "function"
    assert set(tool["function"].keys()) == {"name", "description", "parameters"}
    assert model.requests[0]["system"] == SYSTEM


# --------------------------------------------------------------- criterion #2


@pytest.mark.asyncio
async def test_invalid_arguments_do_not_crash_and_model_self_corrects() -> None:
    answer, model, session = await _run([
        # 1) JSON hỏng.
        AssistantMessage(tool_calls=(ToolCall("c1", "calculator", '{"expression": '),)),
        # 2) JSON hợp lệ nhưng thiếu field required.
        AssistantMessage(tool_calls=(ToolCall("c2", "calculator", '{"precision": 1}'),)),
        # 3) Đọc error, sửa đúng.
        AssistantMessage(tool_calls=(ToolCall("c3", "calculator", '{"expression": "100*1.1"}'),)),
        AssistantMessage(text="Bằng 110.0."),
    ])
    assert answer == "Bằng 110.0."

    errors = [event for event in session.events if event.get("is_error")]
    assert len(errors) == 2
    # Cả hai lần model đều nhận được error CÓ NỘI DUNG, không phải exception.
    assert all(event["content"].startswith("Error: ") for event in errors)
    assert '"expression" is required' in errors[1]["content"]
    # Và error đó thực sự tới tay model ở request kế tiếp.
    assert any(
        message.get("role") == "tool" and '"expression" is required' in message["content"]
        for message in model.requests[2]["messages"]
    )


@pytest.mark.asyncio
async def test_unknown_tool_is_an_error_result_not_a_crash() -> None:
    answer, _, session = await _run([
        AssistantMessage(tool_calls=(ToolCall("c1", "calulator", "{}"),)),
        AssistantMessage(text="Xin lỗi, tôi gọi sai tên tool."),
    ])
    assert answer == "Xin lỗi, tôi gọi sai tên tool."
    (error,) = [event for event in session.events if event.get("is_error")]
    assert "calculator" in error["content"]


# ------------------------------------------------------------------- bounds


@pytest.mark.asyncio
async def test_max_steps_stops_an_endless_tool_loop() -> None:
    # Model kẹt vòng lặp: cứ gọi tool mãi, không bao giờ trả lời.
    script = [
        AssistantMessage(tool_calls=(ToolCall(f"c{i}", "calculator", '{"expression": "1+1"}'),))
        for i in range(5)
    ]
    with pytest.raises(MaxStepsExceeded):
        await run_turn(
            llm=FakeLLM(script), tools=_registry(), session=Session(),
            system=SYSTEM, user_input="loop đi", max_steps=3,
        )


# --------------------------------------------------------------------- cancel


def _slow_registry(started: asyncio.Event) -> ToolRegistry:
    async def hang(_args: dict[str, Any]) -> str:
        started.set()
        await asyncio.sleep(60)  # user Ctrl-C trong lúc này
        return "không bao giờ tới đây"

    registry = ToolRegistry()
    registry.register(
        define_tool(name="hang", description="Hangs.", parameters={}, execute=hang)
    )
    return registry


@pytest.mark.asyncio
async def test_cancel_mid_tool_leaves_a_resumable_log() -> None:
    started, session = asyncio.Event(), Session()
    model = FakeLLM([AssistantMessage(tool_calls=(
        ToolCall("c1", "hang", "{}"),
        ToolCall("c2", "hang", "{}"),
    ))])
    task = asyncio.create_task(run_turn(
        llm=model, tools=_slow_registry(started), session=session,
        system=SYSTEM, user_input="chạy đi",
    ))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # Cả hai call đều được vá — kể cả c1 đang chạy dở.
    results = [event for event in session.events if event["type"] == "tool_result"]
    assert [event["call_id"] for event in results] == ["c1", "c2"]
    assert all(event["content"] == ABORTED_BEFORE_DISPATCH for event in results)
    # Và invariant thật: mọi tool_call có tool message.
    messages = session.to_messages()
    called = {call["id"] for message in messages for call in message.get("tool_calls") or ()}
    answered = {m["tool_call_id"] for m in messages if m["role"] == "tool"}
    assert called == answered


@pytest.mark.asyncio
async def test_registry_does_not_swallow_cancellation() -> None:
    """`except Exception` trong execute() KHÔNG được bắt CancelledError.

    Nếu bắt, huỷ sẽ âm thầm biến thành một error result và turn chạy tiếp —
    user bấm Ctrl-C mà agent vẫn làm việc. CancelledError là BaseException nên
    thoát được, nhưng đây là hành vi phải khoá lại bằng test.
    """
    started = asyncio.Event()
    registry = _slow_registry(started)
    task = asyncio.create_task(registry.execute("hang", "{}"))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_cancel_during_generate_needs_no_repair() -> None:
    # Chưa có assistant event nào -> log đã hợp lệ, không có gì phải vá.
    class Hanging:
        async def generate(self, **_kwargs: Any) -> AssistantMessage:
            started.set()
            await asyncio.sleep(60)
            raise AssertionError("unreachable")

    started, session = asyncio.Event(), Session()
    task = asyncio.create_task(run_turn(
        llm=Hanging(), tools=_registry(), session=session,
        system=SYSTEM, user_input="chờ đi",
    ))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert [event["type"] for event in session.events] == ["user"]
