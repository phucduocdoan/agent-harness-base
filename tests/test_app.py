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

from typing import Any

import pytest

from mini_harness import app
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
