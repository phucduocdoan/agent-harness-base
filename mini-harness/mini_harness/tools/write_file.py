"""Tool write_file: tool đầu tiên cần user duyệt trước khi chạy."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mini_harness.tools.registry import ToolDefinition, define_tool


def write_file_tool(sandbox: Path) -> ToolDefinition:
    """Khai báo tool, với thư mục sandbox nhận TỪ NGOÀI.

    `sandbox` là tham số bắt buộc chứ không phải hằng số trong file này: tool
    không được quyền tự quyết định nó được ghi ở đâu. Chỗ biết điều đó là wiring
    (`app.py`), vì đó là chỗ duy nhất biết harness này đang chạy trong hoàn cảnh
    nào. Đối chiếu luật: "defaulting is an explicit resolve step in the owning
    implementation, never a hidden `?? default`".
    """
    async def write(args: dict[str, Any]) -> str:
        target = (sandbox / args["path"]).resolve()
        # Chặn `../` ngay trong executor, không tin `path` từ model.
        if not target.is_relative_to(sandbox):
            raise ValueError("path must stay inside the sandbox directory")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(args["content"])
        return f"wrote {len(args['content'])} bytes to {target.relative_to(sandbox)}"

    return define_tool(
        name="write_file",
        description="Write a text file. Requires user approval.",
        parameters={
            "path": {"type": "string", "required": True, "description": "Relative path"},
            "content": {"type": "string", "required": True, "description": "File contents"},
        },
        execute=write,
        requires_approval=True,
    )
