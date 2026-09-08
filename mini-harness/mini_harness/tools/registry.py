"""Tool registry: khai báo tool, compile schema, validate args, chạy tool.

File này là implementation của Protocol `Tools` trong agent_loop.py. Nó import
`ToolResult` từ đó (loop sở hữu các type đi qua biên) và KHÔNG bị import ngược lại.

Hai bất biến của file:
  1. Schema compile một lần lúc `define_tool` (dev-time), không compile lại mỗi call.
  2. `ToolRegistry.execute` không bao giờ raise — mọi lỗi thành `ToolResult(is_error=True)`
     để model đọc được và tự sửa ở lượt sau.

Đối chiếu harness thật: core/tools/src/schema.ts và core/tools/src/index.ts.
V1 CỐ TÌNH bỏ `output.schema` + `render()`: tool ở đây trả thẳng str.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError

from mini_harness.core.types import ToolResult

__all__ = ["ToolDefinition", "ToolRegistry", "define_tool"]


# --------------------------------------------------------------- định nghĩa tool


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    """Một tool đã sẵn sàng đăng ký. Chỉ tạo qua `define_tool`.

    `parameters` là JSON Schema THẬT (đã compile từ DSL), gửi trực tiếp lên wire.
    `validator` là metadata nội bộ — không bao giờ lọt tới model, xem `schemas()`.
    """

    name: str
    description: str
    parameters: dict[str, Any]
    execute: Callable[[dict[str, Any]], Awaitable[str]]
    validator: Draft202012Validator
    # Cũng là metadata nội bộ: model KHÔNG được biết tool này cần xin phép.
    # Nó chỉ biết khi bị deny, qua error result.
    requires_approval: bool = False


def define_tool(
    *,
    name: str,
    description: str,
    parameters: dict[str, Any],
    execute: Callable[[dict[str, Any]], Awaitable[str]],
    requires_approval: bool = False,
) -> ToolDefinition:
    """Khai báo một tool, compile schema ngay và fail loud nếu khai báo sai.

    `parameters` dùng DSL của harness: dict `property_name -> spec`, mỗi spec là
    một JSON Schema node cộng annotation tuỳ chọn `required: True`.

    Sai khai báo là bug của dev, không phải của model — nên raise ngay lúc define
    thay vì để nó thành lỗi runtime mờ mịt.

    Raises:
        ValueError: name rỗng, hoặc schema compile ra không hợp lệ.
        TypeError: `parameters` hoặc một property spec không phải dict.
    """
    if not name:
        raise ValueError("define_tool: name không được rỗng")
    schema = _compile_parameters(name, parameters)
    return ToolDefinition(
        name=name,
        description=description,
        parameters=schema,
        execute=execute,
        validator=Draft202012Validator(schema),
        requires_approval=requires_approval,
    )


def _compile_parameters(name: str, parameters: dict[str, Any]) -> dict[str, Any]:
    """Compile DSL property-map thành JSON Schema object-rooted.

    Đối chiếu: parameterSchemaSpecToJsonSchema() ở schema.ts:449.
    """
    if not isinstance(parameters, dict):
        raise TypeError(f"define_tool({name}): parameters phải là dict, nhận {type(parameters).__name__}")

    properties: dict[str, Any] = {}
    required: list[str] = []
    for prop_name, spec in parameters.items():
        if not isinstance(spec, dict):
            raise TypeError(
                f"define_tool({name}): spec của property {prop_name!r} phải là dict, "
                f"nhận {type(spec).__name__}"
            )
        node = dict(spec)
        # `required` là annotation của DSL, không phải keyword JSON Schema hợp lệ
        # ở cấp property — gỡ ra và gom lên root.
        if node.pop("required", False):
            required.append(prop_name)
        properties[prop_name] = node

    schema: dict[str, Any] = {"type": "object", "properties": properties}
    # Không property nào required thì bỏ hẳn key: `required: []` là schema hợp lệ
    # nhưng gây nhiễu cho model.
    if required:
        schema["required"] = required

    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as error:
        raise ValueError(f"define_tool({name}): schema không hợp lệ: {error.message}") from error
    return schema


# ------------------------------------------------------------------- registry


# Kênh xin phép user. `None` trả về = cho phép, string = lý do từ chối.
# Harness thật là một service riêng, lấy qua `ctx.get('approval')`
# (core/tools/src/index.ts:1683) — ở đây thu gọn thành một callable.
Approver = Callable[[str, dict[str, Any]], Awaitable[str | None]]


class ToolRegistry:
    """Tập tool visible của một agent. Khớp Protocol `Tools` trong agent_loop.py."""

    def __init__(self, *, approver: Approver | None = None) -> None:
        self._tools: dict[str, ToolDefinition] = {}
        self._approver = approver

    def register(self, definition: ToolDefinition) -> None:
        """Thêm một tool. Trùng tên là bug dev-time nên raise ngay.

        Raises:
            ValueError: đã có tool cùng tên.
        """
        if definition.name in self._tools:
            raise ValueError(f"tool {definition.name!r} đã được đăng ký")
        self._tools[definition.name] = definition

    def schemas(self) -> list[dict[str, Any]]:
        """Projection model-facing của các tool: ĐÚNG name/description/parameters.

        Whitelist tường minh, không phải blacklist: `ToolDefinition` có thể mọc
        thêm field nội bộ (validator hôm nay, timeout/permission mai sau) và
        không field nào trong số đó được tự động lọt lên wire. Mỗi field muốn
        model thấy phải được thêm vào đây một cách có ý thức.

        Đối chiếu: schemaOf() ở core/tools/src/index.ts:1246.
        """
        return [
            {
                "name": definition.name,
                "description": definition.description,
                "parameters": definition.parameters,
            }
            for definition in self._tools.values()
        ]

    async def execute(self, name: str, arguments_json: str) -> ToolResult:
        """Chạy một tool call. LUÔN trả ToolResult, không bao giờ raise.

        Đối chiếu: toolErrorResult() ở core/tools/src/index.ts:1860.
        """
        definition = self._tools.get(name)
        if definition is None:
            # Model-facing text luôn viết bằng tiếng Anh: nó đi vào request kế tiếp.
            available = ", ".join(sorted(self._tools)) or "(none registered)"
            return ToolResult(
                content=f'Error: no tool named "{name}". Available tools: {available}',
                is_error=True,
            )

        args = _parse_arguments(arguments_json)
        # Sort để thứ tự violation ổn định qua các lần chạy (iter_errors không
        # bảo đảm thứ tự), tránh test flaky và tránh làm bẩn prompt cache.
        # set() vì `required` yield một error cho mỗi field thiếu, mà
        # _format_violation lại mô tả cả nhóm -> các error đó cho cùng một dòng.
        violations = sorted({_format_violation(error) for error in definition.validator.iter_errors(args)})
        if violations:
            return ToolResult(content=f"Error: invalid arguments: {'; '.join(violations)}", is_error=True)

        # Approval gate đặt ở ĐÂY, trong executor, không ở agent loop: execute()
        # là con đường duy nhất tool chạy được, nên caller nào cũng bị chặn.
        # Đặt sau validate là có chủ ý — đừng bắt user duyệt một call chắc chắn
        # fail. Đối chiếu: core/tools/src/index.ts:1366.
        if definition.requires_approval:
            if self._approver is None:
                # Fail closed: không có kênh xin phép thì từ chối, không phải
                # cho qua. Đối chiếu: core/tools/src/index.ts:1684.
                return ToolResult(
                    content=f'Error: tool "{name}" requires approval, '
                            "but no approval channel is available",
                    is_error=True,
                )
            reason = await self._approver(name, args)
            if reason is not None:
                return ToolResult(
                    content=f'Error: tool "{name}" was denied: {reason}. '
                            "Do not call it again unless the user asks.",
                    is_error=True,
                )

        try:
            output = await definition.execute(args)
        except Exception as error:  # noqa: BLE001 — tool nào cũng có thể throw; đó là message cho model.
            return ToolResult(content=f"Error: {error}", is_error=True)

        if not isinstance(output, str):
            return ToolResult(
                content=f'Error: tool "{name}" returned {type(output).__name__}, expected a string',
                is_error=True,
            )
        return ToolResult(content=output)


def _parse_arguments(arguments_json: str) -> Any:
    """Parse args thô từ model, không bao giờ throw.

    JSON hỏng thì giữ nguyên raw string làm args: nó sẽ fail validate ở bước sau
    và cho model một thông báo lỗi có ích thay vì một exception.
    Đối chiếu: parseArguments() ở tool-calls.ts:105.
    """
    if not arguments_json.strip():
        return {}
    try:
        return json.loads(arguments_json)
    except json.JSONDecodeError:
        return arguments_json


def _format_violation(error: ValidationError) -> str:
    """Đổi một ValidationError thành một dòng CÓ PATH cho model đọc.

    Path là phần quan trọng: `"expression" must be a string` nói cho model biết
    sửa field nào, `must be a string` thì không.
    """
    path = _quote_path(error.absolute_path)
    if error.validator == "required" and isinstance(error.instance, dict):
        # jsonschema gắn lỗi `required` vào object cha, không vào field thiếu.
        # Tên field thiếu chỉ đọc được từ error.message (chi tiết nội bộ của
        # jsonschema) hoặc tính lại từ validator_value + instance — chọn cách sau.
        missing = sorted(name for name in error.validator_value if name not in error.instance)
        if missing:
            return "; ".join(
                f"{_quote_path([*error.absolute_path, name])} is required" for name in missing
            )
    if error.validator == "type":
        expected = error.validator_value
        names = [expected] if isinstance(expected, str) else list(expected)
        return f"{path} must be {' or '.join(_article(n) for n in names)}"
    return f"{path}: {error.message}"


def _quote_path(tokens: Iterable[str | int]) -> str:
    """Render path thành dạng `"todos[0].status"`; root rỗng thành `"arguments"`."""
    parts: list[str] = []
    for token in tokens:
        if isinstance(token, int):
            parts.append(f"[{token}]")
        else:
            parts.append(f".{token}" if parts else token)
    return f'"{"".join(parts) or "arguments"}"'


def _article(type_name: str) -> str:
    return f"{'an' if type_name[:1] in 'aeiou' else 'a'} {type_name}"
