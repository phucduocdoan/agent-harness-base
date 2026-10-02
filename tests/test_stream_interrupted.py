"""Một bất biến: chữ nào đã lên màn hình thì phải có trong log.

Lỗi gốc nằm ở khoảng cách giữa hai dòng. `on_text` đẩy text ra terminal NGAY
lúc nó tới (stream.py:66), còn `session.append({"type": "assistant", ...})`
chỉ chạy SAU KHI `generate()` trả về trọn vẹn (loop.py). Giữa hai mốc đó mà
stream vỡ — provider chết, hoặc người dùng bấm Ctrl-C — thì người dùng đã đọc
một đoạn text không tồn tại ở bất cứ đâu. `--replay` và `--session` sau đó
phát lại một cuộc hội thoại KHÁC với cuộc hội thoại họ vừa xem.

Hai lối vỡ, hai cách xử, và sự khác nhau nằm ở NGUYÊN NHÂN chứ không ở nội
dung text:

  * provider chết  -> vào log, KHÔNG chiếu cho model.
  * người dùng huỷ -> vào log, CÓ chiếu cho model.

Vì sao xem `docstring` của `StreamInterrupted` / `StreamCancelled`.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from mini_harness.core.loop import run_turn
from mini_harness.core.session import Session
from mini_harness.core.types import StreamCancelled, StreamInterrupted
from mini_harness.tools.registry import ToolRegistry

SYSTEM = "Bạn là trợ lý."
CAU_HOI = "100 * 1.1 bằng mấy?"
DA_IN = "Để tôi tính: 100 * 1.1 "


class StreamVo:
    """Model giả đẩy text ra `on_text` rồi chết giữa chừng.

    `FakeLLM` không dùng được ở đây: nó trả về một `AssistantMessage` nguyên
    vẹn, nên không có cách nào dựng lại tình huống "đã in ra màn hình nhưng
    chưa về tới caller" — mà đó đúng là tình huống duy nhất gây lỗi này.

    Hai chế độ vì hàm thật cũng có đúng hai lối thoát bất thường: `loi` là
    provider chết, không `loi` là treo chờ bị huỷ.
    """

    def __init__(self, text: str, *, loi: BaseException | None = None,
                 on_text: Any = None) -> None:
        self._text = text
        self._loi = loi
        self._on_text = on_text
        self.da_phat = asyncio.Event()

    async def generate(self, **_kwargs: Any) -> Any:
        self._on_text(self._text)
        self.da_phat.set()
        if self._loi is not None:
            raise StreamInterrupted(self._text, self._loi)
        try:
            await asyncio.sleep(60)  # người dùng Ctrl-C trong lúc này
        except asyncio.CancelledError:
            raise StreamCancelled(self._text) from None
        raise AssertionError("unreachable")


# --------------------------------------------------------------- provider chết


@pytest.mark.asyncio
async def test_provider_chet_giua_stream_thi_text_da_in_van_vao_log() -> None:
    """Nhưng model không được thấy lại nó.

    Hai assert cuối là hai mặt của cùng một quyết định. Log giữ, vì log phải
    trung thực với cái người dùng đã nhìn. Phép chiếu bỏ, vì gửi lại cho model
    một câu cụt của chính nó là mời nó viết tiếp từ chỗ đứt — trong khi việc
    đúng là trả lời lại từ đầu.
    """
    man_hinh: list[str] = []
    llm = StreamVo(DA_IN, loi=ConnectionError("connection reset"), on_text=man_hinh.append)
    session = Session()

    with pytest.raises(StreamInterrupted):
        await run_turn(llm=llm, tools=ToolRegistry(), session=session,
                       system=SYSTEM, user_input=CAU_HOI)

    assert man_hinh == [DA_IN]  # người dùng ĐÃ đọc chừng này
    assert [event["type"] for event in session.events] == ["user", "assistant_attempt"]
    assert session.events[-1]["content"] == DA_IN
    assert session.to_messages() == [{"role": "user", "content": CAU_HOI}]


# ------------------------------------------------------------- người dùng huỷ


@pytest.mark.asyncio
async def test_huy_giua_stream_thi_text_da_in_vao_log_va_den_duoc_model() -> None:
    """Ctrl-C giữa lúc model đang nói.

    Khác hẳn trường hợp trên dù text y hệt: ở đây không có gì hỏng. Người dùng
    chủ động cắt, vẫn đọc được đoạn dở, và câu kế tiếp của họ rất có thể nói về
    đúng đoạn đó ("thôi, cách khác đi"). Model không thấy đoạn đó thì không
    hiểu "cách khác" là khác với cái gì.

    Và huỷ vẫn phải là huỷ: `CancelledError` vẫn bay ra ngoài, không bị nuốt.
    """
    man_hinh: list[str] = []
    llm = StreamVo(DA_IN, on_text=man_hinh.append)
    session = Session()

    turn = asyncio.create_task(run_turn(llm=llm, tools=ToolRegistry(), session=session,
                                        system=SYSTEM, user_input=CAU_HOI))
    await llm.da_phat.wait()
    turn.cancel()
    with pytest.raises(asyncio.CancelledError):
        await turn

    assert man_hinh == [DA_IN]
    assert [event["type"] for event in session.events] == ["user", "assistant"]
    assert session.events[-1]["content"] == DA_IN
    assert session.events[-1]["interrupted"] is True
    assert session.to_messages()[-1] == {"role": "assistant", "content": DA_IN}
