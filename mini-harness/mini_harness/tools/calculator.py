"""Tool calculator: tính biểu thức số học bằng AST whitelist.

Mỗi tool là một module tự sở hữu MODEL-FACING TEXT của nó — description và mọi
message lỗi. Đó không phải chuyện gọn gàng: bug thật của tool này là `^`
(model dùng với ý luỹ thừa, Python parse thành XOR) và nó được sửa bằng cách
viết lại đúng hai câu tiếng Anh ở đây, không phải bằng cách sửa logic.
"""

from __future__ import annotations

import ast
import operator
from typing import Any

from mini_harness.tools.registry import ToolDefinition, define_tool

_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.Mod: operator.mod,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}

# Toán tử KHÔNG được whitelist, chỉ dùng để đặt tên ký hiệu trong error message.
# Không được thêm vào _OPERATORS: `^` trông giống lũy thừa nhưng là XOR, cho
# chạy sẽ trả số sai một cách âm thầm (695700^3 = 695703, không phải luỹ thừa).
_UNSUPPORTED_OPERATOR_SYMBOLS = {
    ast.BitXor: "^",
    ast.BitAnd: "&",
    ast.BitOr: "|",
    ast.LShift: "<<",
    ast.RShift: ">>",
    ast.FloorDiv: "//",
}


def _evaluate(node: ast.AST) -> float:
    """Tính biểu thức số học bằng AST whitelist — KHÔNG dùng eval().

    `expression` là string do model sinh ra, tức là untrusted input. eval() ở đây
    là remote code execution với extra step.
    """
    if isinstance(node, ast.Expression):
        return _evaluate(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, int | float):
        return float(node.value)
    if isinstance(node, ast.BinOp) and type(node.op) in _OPERATORS:
        return _OPERATORS[type(node.op)](_evaluate(node.left), _evaluate(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPERATORS:
        return _OPERATORS[type(node.op)](_evaluate(node.operand))
    # `^` là bẫy phổ biến nhất: model dùng nó với ý lũy thừa, nhưng Python (và
    # whitelist này) parse `^` thành XOR. Cho message riêng, đủ cụ thể để model
    # tự sửa ngay lượt sau thay vì đoán từ trí nhớ.
    if isinstance(node, ast.BinOp) and type(node.op) is ast.BitXor:
        raise ValueError('"^" means XOR here, not exponentiation — use "**" for power (e.g. "2**3")')
    if isinstance(node, ast.BinOp) and type(node.op) in _UNSUPPORTED_OPERATOR_SYMBOLS:
        symbol = _UNSUPPORTED_OPERATOR_SYMBOLS[type(node.op)]
        raise ValueError(f'"{symbol}" is not a supported operator; only numbers and + - * / ** % are supported')
    # Node loại khác (Name, Call, Compare, ...) không có ký hiệu cụ thể để nêu.
    # Message này model đọc được, nên nói rõ cái gì được phép.
    raise ValueError("only numbers and + - * / ** % are supported")


async def _calculator(args: dict[str, Any]) -> str:
    try:
        tree = ast.parse(args["expression"], mode="eval")
    except SyntaxError as error:
        raise ValueError(f"not a valid expression: {error.msg}") from error
    precision = args.get("precision", 2)
    value = round(_evaluate(tree), precision)
    # precision=0 nghĩa là số nguyên; round() vẫn trả float nên "7006652.0"
    # sẽ mâu thuẫn với chính cái model vừa yêu cầu.
    return str(int(value) if precision == 0 else value)



def calculator_tool() -> ToolDefinition:
    """Khai báo tool. Không có tham số vì tool này không phụ thuộc gì bên ngoài."""
    return define_tool(
        name="calculator",
        description="Evaluate an arithmetic expression and return the result.",
        parameters={
            "expression": {
                "type": "string",
                "required": True,
                "description": 'An arithmetic expression, e.g. "100 * 1.1". '
                                'Use "**" for exponentiation (e.g. "2**3"), not "^".',
            },
            "precision": {
                "type": "integer",
                "description": "Decimal places to round to. Defaults to 2.",
            },
        },
        execute=_calculator,
    )
