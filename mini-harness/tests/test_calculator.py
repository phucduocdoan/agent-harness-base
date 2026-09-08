"""Test cho calculator tool của mini_harness/tools/calculator.py — bất biến của `_evaluate` whitelist.

Gọi qua `ToolRegistry.execute(...)` thật (validate + approval gate +
exception-to-ToolResult), không gọi `_evaluate` trực tiếp — bug thật lộ ra ở
tầng model-facing text, không phải ở nội bộ hàm.
"""

from __future__ import annotations

import pytest

from mini_harness.tools.calculator import calculator_tool
from mini_harness.tools.registry import ToolRegistry


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(calculator_tool())
    return registry


@pytest.mark.asyncio
async def test_caret_names_xor_and_suggests_double_star() -> None:
    """`^` là bẫy thật: model dùng nó với ý lũy thừa. Message phải tự sửa được nó."""
    result = await _registry().execute("calculator", '{"expression": "2^3"}')
    assert result.is_error is True
    assert "^" in result.content
    assert "**" in result.content


@pytest.mark.asyncio
async def test_caret_is_not_silently_computed_as_xor() -> None:
    """BẤT BIẾN khoá cứng: `^` không bao giờ được chạy như XOR.

    (695700^3) XOR ra 695703 -> biểu thức trả 109.25, trông hợp lý nhưng sai
    hoàn toàn so với đáp án đúng 1302096.57. Chặn thì model biết fail; cho qua
    thì không ai biết.
    """
    result = await _registry().execute(
        "calculator",
        '{"expression": "(4/3) * 3.14 * (695700^3) / ((4/3) * 3.14 * (6371^3))"}',
    )
    assert result.is_error is True
    assert result.content != "109.25"


@pytest.mark.asyncio
async def test_double_star_still_computes_correctly() -> None:
    result = await _registry().execute(
        "calculator", '{"expression": "(695700**3) / (6371**3)"}'
    )
    assert result.is_error is False
    assert result.content == "1302096.57"


@pytest.mark.asyncio
async def test_other_bit_operator_is_rejected_and_named() -> None:
    result = await _registry().execute("calculator", '{"expression": "6 & 3"}')
    assert result.is_error is True
    assert "&" in result.content


@pytest.mark.asyncio
async def test_function_call_still_gives_a_clear_message() -> None:
    """`log(100)` đã có message ổn từ trước — nhánh chung không được làm nó tệ đi."""
    result = await _registry().execute("calculator", '{"expression": "log(100)"}')
    assert result.is_error is True
    assert "only numbers and + - * / ** % are supported" in result.content


def test_expression_description_mentions_double_star() -> None:
    (schema,) = [s for s in _registry().schemas() if s["name"] == "calculator"]
    assert "**" in schema["parameters"]["properties"]["expression"]["description"]
