"""Test cho `read_line` — chỗ harness chạm fd 0 thật.

Dùng fd THẬT (/dev/null, file, pipe) chứ không mock: cái đang test chính là
"asyncio làm gì với loại fd này", nên thay fd bằng đồ giả thì test còn lại
chẳng chứng minh gì.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from mini_harness.cli import terminal


@pytest.fixture(autouse=True)
def _stdin_sach(monkeypatch):
    """`read_line` nhớ fd trong biến module — mỗi test phải bắt đầu từ chưa xét."""
    monkeypatch.setattr(terminal, "_STDIN", None)
    monkeypatch.setattr(terminal, "_STDIN_FLAGS", None)
    monkeypatch.setattr(terminal, "_STDIN_FD", None)
    monkeypatch.setattr(terminal, "_STDIN_POLL_DUOC", None)


# ------------------------------------------------------------------ _poll_duoc


def test_dev_null_va_file_thuong_khong_vao_duoc_epoll(tmp_path: Path) -> None:
    """Hai loại fd này mà đưa vào `connect_read_pipe` thì process treo vĩnh viễn."""
    with open(os.devnull) as f:
        assert terminal._poll_duoc(f.fileno()) is False
    path = tmp_path / "in.txt"
    path.write_text("x\n", encoding="utf-8")
    with path.open() as f:
        assert terminal._poll_duoc(f.fileno()) is False


def test_pipe_thi_vao_duoc() -> None:
    """Phải phân biệt được, không phải lúc nào cũng trả False."""
    doc, ghi = os.pipe()
    try:
        assert terminal._poll_duoc(doc) is True
    finally:
        os.close(doc)
        os.close(ghi)


# -------------------------------------------------------------------- read_line


async def _doc(n: int) -> list[str]:
    """Đọc n dòng. Timeout là MỘT PHẦN của test: lỗi cũ biểu hiện bằng treo."""
    return [await asyncio.wait_for(terminal.read_line(), timeout=5) for _ in range(n)]


@pytest.mark.asyncio
async def test_stdin_la_file_thuong_van_doc_duoc(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "in.txt"
    path.write_text("xin chào\n/quit\n", encoding="utf-8")
    with path.open() as f:
        monkeypatch.setattr("sys.stdin", f)
        assert await _doc(2) == ["xin chào\n", "/quit\n"]


@pytest.mark.asyncio
async def test_stdin_la_dev_null_bao_eof_chu_khong_treo(monkeypatch) -> None:
    """Lỗi cũ: epoll từ chối /dev/null, lỗi ném trong callback nên bị nuốt, và
    `readline()` không bao giờ về — `--replay log < /dev/null` treo tới khi bị
    kill, không in ra lý do nào."""
    with open(os.devnull) as f:
        monkeypatch.setattr("sys.stdin", f)
        with pytest.raises(EOFError):
            await _doc(1)
