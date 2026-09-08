"""Test cho mini_harness/tools/registry.py — mỗi bất biến một test.

pytest-asyncio có sẵn nên dùng `@pytest.mark.asyncio`.
"""

from __future__ import annotations

from typing import Any

import pytest

from mini_harness.tools.registry import ToolRegistry, define_tool


# ------------------------------------------------------------------- fixtures


async def _calc(args: dict[str, Any]) -> str:
    # Không tính thật: test quan tâm biên của registry, không quan tâm số học.
    return f"{args['expression']} @ {args.get('precision', 2)}"


def _calc_tool():
    return define_tool(
        name="calculator",
        description="Tính một biểu thức số học.",
        parameters={
            "expression": {"type": "string", "required": True, "description": "Ví dụ 100*1.1"},
            "precision": {"type": "integer", "description": "Số chữ số thập phân"},
        },
        execute=_calc,
    )


def _registry_with_calc() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(_calc_tool())
    return registry


# --------------------------------------------------------------------- schemas


def test_schemas_expose_exactly_three_fields() -> None:
    schemas = _registry_with_calc().schemas()
    assert len(schemas) == 1
    assert set(schemas[0].keys()) == {"name", "description", "parameters"}


def test_compile_dsl_lifts_required_to_root_array() -> None:
    parameters = _calc_tool().parameters
    assert parameters["type"] == "object"
    assert parameters["required"] == ["expression"]
    # Annotation `required` bị gỡ khỏi từng property spec.
    assert "required" not in parameters["properties"]["expression"]
    assert parameters["properties"]["expression"] == {"type": "string", "description": "Ví dụ 100*1.1"}


def test_compile_dsl_omits_required_key_when_nothing_required() -> None:
    tool = define_tool(
        name="noop",
        description="Không cần tham số.",
        parameters={"note": {"type": "string"}},
        execute=lambda _args: _echo("ok"),
    )
    assert "required" not in tool.parameters


async def _echo(text: str) -> str:
    return text


# --------------------------------------------------------------------- execute


@pytest.mark.asyncio
async def test_happy_path_returns_tool_output() -> None:
    result = await _registry_with_calc().execute("calculator", '{"expression": "100*1.1", "precision": 1}')
    assert result.is_error is False
    assert result.content == "100*1.1 @ 1"


@pytest.mark.asyncio
async def test_malformed_json_becomes_error_result() -> None:
    result = await _registry_with_calc().execute("calculator", '{"expression": ')
    assert result.is_error is True
    assert "invalid arguments" in result.content
    # Raw string được giữ làm args nên nó fail ở type check của root.
    assert result.content == 'Error: invalid arguments: "arguments" must be an object'


@pytest.mark.asyncio
async def test_wrong_type_violation_carries_path() -> None:
    result = await _registry_with_calc().execute("calculator", '{"expression": 42}')
    assert result.is_error is True
    assert result.content == 'Error: invalid arguments: "expression" must be a string'


@pytest.mark.asyncio
async def test_missing_required_field_names_the_field() -> None:
    result = await _registry_with_calc().execute("calculator", '{"precision": 1}')
    assert result.is_error is True
    assert result.content == 'Error: invalid arguments: "expression" is required'


@pytest.mark.asyncio
async def test_several_missing_required_fields_are_listed_once_each() -> None:
    """`required` yield một error mỗi field thiếu -> phải dedupe, không nhân bản."""
    registry = ToolRegistry()
    registry.register(
        define_tool(
            name="pair",
            description="Cần cả hai field.",
            parameters={
                "left": {"type": "string", "required": True},
                "right": {"type": "string", "required": True},
            },
            execute=_calc,
        )
    )
    result = await registry.execute("pair", "{}")
    assert result.is_error is True
    assert result.content == 'Error: invalid arguments: "left" is required; "right" is required'


@pytest.mark.asyncio
async def test_unknown_tool_lists_available_tools() -> None:
    result = await _registry_with_calc().execute("calulator", "{}")
    assert result.is_error is True
    assert "calulator" in result.content
    assert "calculator" in result.content


@pytest.mark.asyncio
async def test_tool_raising_becomes_error_result() -> None:
    async def boom(_args: dict[str, Any]) -> str:
        raise RuntimeError("ổ cứng bốc khói")

    registry = ToolRegistry()
    registry.register(
        define_tool(name="boom", description="Luôn fail.", parameters={}, execute=boom)
    )
    result = await registry.execute("boom", "{}")
    assert result.is_error is True
    assert result.content == "Error: ổ cứng bốc khói"


@pytest.mark.asyncio
async def test_empty_arguments_json_is_treated_as_empty_object() -> None:
    registry = ToolRegistry()
    registry.register(
        define_tool(
            name="ping",
            description="Không có field required nào.",
            parameters={"note": {"type": "string"}},
            execute=lambda _args: _echo("pong"),
        )
    )
    result = await registry.execute("ping", "")
    assert result.is_error is False
    assert result.content == "pong"


@pytest.mark.asyncio
async def test_multiple_violations_have_stable_order() -> None:
    registry = _registry_with_calc()
    arguments = '{"precision": "hai"}'
    first = await registry.execute("calculator", arguments)
    second = await registry.execute("calculator", arguments)
    assert first.is_error is True
    assert first.content == second.content
    assert first.content == (
        'Error: invalid arguments: "expression" is required; "precision" must be an integer'
    )


# ----------------------------------------------------------------- dev-time fail


def test_define_tool_rejects_bad_parameters() -> None:
    with pytest.raises(TypeError):
        define_tool(
            name="bad",
            description="spec không phải dict.",
            parameters={"x": "string"},
            execute=lambda _args: _echo("ok"),
        )


def test_register_rejects_duplicate_name() -> None:
    registry = _registry_with_calc()
    with pytest.raises(ValueError):
        registry.register(_calc_tool())


# ------------------------------------------------------------------- approval
# Luật của harness thật: "test denial through the executor" — mọi test dưới đây
# gọi thẳng registry.execute(), KHÔNG đi qua agent loop. Nếu gate chỉ chặn được
# khi chạy cả loop thì gate đặt sai chỗ.


def _dangerous_registry(approver=None) -> ToolRegistry:
    async def wipe(args: dict[str, Any]) -> str:
        return f"wiped {args['path']}"

    registry = ToolRegistry(approver=approver) if approver else ToolRegistry()
    registry.register(
        define_tool(
            name="wipe",
            description="Delete a path.",
            parameters={"path": {"type": "string", "required": True}},
            execute=wipe,
            requires_approval=True,
        )
    )
    return registry


@pytest.mark.asyncio
async def test_denial_becomes_an_error_result_not_an_exception() -> None:
    async def deny(_name: str, _args: dict[str, Any]) -> str | None:
        return "user said no"

    result = await _dangerous_registry(deny).execute("wipe", '{"path": "/"}')
    assert result.is_error is True
    assert result.content == (
        'Error: tool "wipe" was denied: user said no. '
        "Do not call it again unless the user asks."
    )


@pytest.mark.asyncio
async def test_approval_lets_the_tool_run() -> None:
    async def allow(_name: str, _args: dict[str, Any]) -> str | None:
        return None

    result = await _dangerous_registry(allow).execute("wipe", '{"path": "/tmp/x"}')
    assert result.is_error is False
    assert result.content == "wiped /tmp/x"


@pytest.mark.asyncio
async def test_missing_approval_channel_fails_closed() -> None:
    # Không cấu hình approver -> DENY, không phải cho qua.
    result = await _dangerous_registry().execute("wipe", '{"path": "/"}')
    assert result.is_error is True
    assert "no approval channel is available" in result.content


@pytest.mark.asyncio
async def test_invalid_arguments_never_reach_the_approver() -> None:
    """Đừng bắt user duyệt một call chắc chắn fail (tools/index.ts:1366)."""
    seen: list[dict[str, Any]] = []

    async def record(_name: str, args: dict[str, Any]) -> str | None:
        seen.append(args)
        return None

    result = await _dangerous_registry(record).execute("wipe", "{}")
    assert result.is_error is True
    assert result.content == 'Error: invalid arguments: "path" is required'
    assert seen == []


@pytest.mark.asyncio
async def test_approver_sees_the_validated_arguments() -> None:
    seen: list[tuple[str, dict[str, Any]]] = []

    async def record(name: str, args: dict[str, Any]) -> str | None:
        seen.append((name, args))
        return "nope"

    await _dangerous_registry(record).execute("wipe", '{"path": "/etc"}')
    assert seen == [("wipe", {"path": "/etc"})]


def test_requires_approval_never_reaches_the_model() -> None:
    # Whitelist trong schemas() bảo vệ sẵn: thêm field nội bộ không lọt ra wire.
    (schema,) = _dangerous_registry().schemas()
    assert set(schema.keys()) == {"name", "description", "parameters"}
