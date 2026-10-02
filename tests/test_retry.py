"""Thử lại một request hỏng — và quan trọng hơn: KHÔNG thử lại khi không được.

Ranh giới cần khoá lại là ranh giới nguyên tử: chừng nào chưa có chữ nào đi
qua `on_text`, gửi lại lần nữa không ai biết; sau đó thì gửi lại là in chồng
lên cái người dùng vừa đọc. Vì vậy test nặng nhất ở đây không phải "thử lại
thành công" mà là `test_vo_sau_token_dau_thi_khong_thu_lai`.

Không có network: `_FakeClient` dựng lại đúng hình dạng `chat.completions.create`
của openai SDK, chỉ phần `generate_streamed` thật sự chạm tới.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from mini_harness.core.types import StreamCancelled, StreamInterrupted
from mini_harness.llm import retry as retry_module
from mini_harness.llm.stream import generate_streamed


# ------------------------------------------------------------------- fixtures


class LoiHttp(Exception):
    """Lỗi có `status_code`, đúng hình dạng `APIStatusError` của openai SDK."""

    def __init__(self, status: int, *, retry_after: str | None = None) -> None:
        super().__init__(f"HTTP {status}")
        self.status_code = status
        if retry_after is not None:
            self.response = SimpleNamespace(headers={"retry-after": retry_after})


def _chunk(text: str) -> SimpleNamespace:
    delta = SimpleNamespace(content=text, tool_calls=None)
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta)])


class _Stream:
    """Stream giả: phát hết `chunks` rồi vỡ (hoặc kết thúc bình thường)."""

    def __init__(self, chunks: list[Any], loi: BaseException | None) -> None:
        self._chunks = chunks
        self._loi = loi

    async def __aenter__(self) -> _Stream:
        return self

    async def __aexit__(self, *_exc: Any) -> bool:
        return False

    def __aiter__(self) -> Any:
        return self._phat()

    async def _phat(self) -> Any:
        for chunk in self._chunks:
            yield chunk
        if self._loi is not None:
            raise self._loi


class _FakeClient:
    """Mỗi phần tử của `kich_ban` là một LẦN gọi `create`.

    Một exception = hỏng ngay lúc gửi (chưa phát chữ nào). Một tuple
    `(chunks, loi)` = stream mở được rồi mới hỏng — tức có thể đã phát chữ.
    """

    def __init__(self, kich_ban: list[Any]) -> None:
        self._kich_ban = list(kich_ban)
        self.so_lan_goi = 0
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, **_kwargs: Any) -> _Stream:
        self.so_lan_goi += 1
        if not self._kich_ban:
            raise AssertionError(f"create() gọi lần {self.so_lan_goi}, hết kịch bản")
        buoc = self._kich_ban.pop(0)
        if isinstance(buoc, BaseException):
            raise buoc
        chunks, loi = buoc
        return _Stream(chunks, loi)


@pytest.fixture
def dong_ho(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Nuốt mọi lần ngủ, ghi lại độ dài. Bỏ jitter cho test tất định."""
    da_ngu: list[float] = []

    async def khong_ngu(delay: float) -> None:
        da_ngu.append(delay)

    monkeypatch.setattr(retry_module.asyncio, "sleep", khong_ngu)
    monkeypatch.setattr(retry_module.random, "uniform", lambda _lo, _hi: 1.0)
    return da_ngu


async def _chay(client: _FakeClient, **kwargs: Any) -> Any:
    """Gọi `generate_streamed` với phần wire format tối thiểu."""
    kwargs.setdefault("on_text", None)
    return await generate_streamed(
        client, "m", system="s", messages=[], tools=[], **kwargs
    )


# ----------------------------------------------------------- thử lại được


@pytest.mark.asyncio
async def test_429_hai_lan_roi_xong_tra_ve_dung_mot_ket_qua(dong_ho: list[float]) -> None:
    client = _FakeClient([LoiHttp(429), LoiHttp(429), ([_chunk("110.0")], None)])

    message = await _chay(client)

    assert message.text == "110.0"
    assert client.so_lan_goi == 3
    assert dong_ho == [0.5, 1.0]  # gấp đôi dần


@pytest.mark.asyncio
async def test_het_luot_thu_thi_nem_lai_loi_cuoi(dong_ho: list[float]) -> None:
    client = _FakeClient([LoiHttp(503)] * 5)

    with pytest.raises(LoiHttp):
        await _chay(client)

    assert client.so_lan_goi == 5
    assert dong_ho == [0.5, 1.0, 2.0, 4.0]  # ngủ ít hơn số lần thử đúng 1


def test_tran_chan_phep_gap_doi(monkeypatch: pytest.MonkeyPatch) -> None:
    """Gấp đôi mãi sẽ ra 16s, 32s, 64s — người dùng bấm Ctrl-C từ lâu rồi.

    Gọi thẳng `_delay` chứ không chạy qua `with_retry`: với 5 lượt thử, mốc
    cao nhất mới chỉ là 4s, nên một test chạy vòng thật sẽ xanh kể cả khi cái
    trần bị xoá mất.
    """
    monkeypatch.setattr(retry_module.random, "uniform", lambda _lo, _hi: 1.0)
    assert retry_module._delay(20, LoiHttp(500)) == retry_module._MAX_DELAY


# --------------------------------------------------------- không thử lại được


@pytest.mark.asyncio
async def test_vo_sau_token_dau_thi_khong_thu_lai(dong_ho: list[float]) -> None:
    """Lỗi y hệt (429), chỉ khác là đã có chữ ra màn hình. Và thế là đủ.

    Đây là ranh giới của cả tính năng. Thử lại ở đây sẽ in "Kết quả là" hai
    lần, và terminal không có cách nào rút lại dòng đã in.
    """
    man_hinh: list[str] = []
    client = _FakeClient([([_chunk("Kết quả là ")], LoiHttp(429))])

    with pytest.raises(StreamInterrupted) as vo:
        await _chay(client, on_text=man_hinh.append)

    assert man_hinh == ["Kết quả là "]
    assert vo.value.text == "Kết quả là "  # mang theo đúng cái đã in
    assert client.so_lan_goi == 1
    assert dong_ho == []


@pytest.mark.asyncio
async def test_loi_khong_tam_thoi_hong_ngay_khong_cho(dong_ho: list[float]) -> None:
    """401 là sai API key. Thử lại 5 lần chỉ làm người dùng đợi 7 giây rồi vẫn sai."""
    client = _FakeClient([LoiHttp(401)])

    with pytest.raises(LoiHttp):
        await _chay(client)

    assert client.so_lan_goi == 1
    assert dong_ho == []


@pytest.mark.asyncio
async def test_chua_phat_chu_nao_thi_khong_boc_lai_exception(dong_ho: list[float]) -> None:
    """Không có gì mồ côi để mang theo, nên để nguyên tên lỗi gốc cho dễ đọc."""
    client = _FakeClient([LoiHttp(401)])

    with pytest.raises(LoiHttp) as loi:
        await _chay(client)

    assert not isinstance(loi.value, StreamInterrupted)


# ------------------------------------------------------------------ Retry-After


@pytest.mark.asyncio
async def test_nghe_retry_after_cua_server(dong_ho: list[float]) -> None:
    client = _FakeClient([LoiHttp(429, retry_after="3"), ([_chunk("ok")], None)])

    await _chay(client)

    assert dong_ho == [3.0]  # không phải 0.5 của mốc gấp đôi


@pytest.mark.asyncio
async def test_retry_after_qua_tran_thi_bo_qua(dong_ho: list[float]) -> None:
    """`Retry-After: 3600` là server bảo "mai quay lại" — ngồi đợi là treo máy."""
    client = _FakeClient([LoiHttp(429, retry_after="3600"), ([_chunk("ok")], None)])

    await _chay(client)

    assert dong_ho == [0.5]


# ------------------------------------------------------------- báo trước khi ngủ


@pytest.mark.asyncio
async def test_bao_truoc_khi_ngu_chu_khong_phai_sau(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Phần chờ là phần dễ bị cắt ngang nhất, nên bằng chứng phải bền trước đó."""
    moc: list[str] = []

    async def ngu(_delay: float) -> None:
        moc.append("ngủ")

    monkeypatch.setattr(retry_module.asyncio, "sleep", ngu)
    client = _FakeClient([LoiHttp(429), ([_chunk("ok")], None)])

    await _chay(client, on_retry=lambda _event: moc.append("báo"))

    assert moc == ["báo", "ngủ"]


@pytest.mark.asyncio
async def test_thong_bao_khong_di_qua_kenh_text(dong_ho: list[float]) -> None:
    """`on_text` chảy thẳng vào `StreamAccumulator` — một chữ lọt vào đó là một
    chữ model sẽ coi là lời của chính nó.
    """
    man_hinh: list[str] = []
    bao: list[dict[str, Any]] = []
    client = _FakeClient([LoiHttp(429), ([_chunk("110.0")], None)])

    message = await _chay(client, on_text=man_hinh.append, on_retry=bao.append)

    assert man_hinh == ["110.0"]
    assert message.text == "110.0"
    assert [event["type"] for event in bao] == ["retry"]
    assert bao[0]["attempt"] == 1
    assert "429" in bao[0]["error"]


# -------------------------------------------------------------------- huỷ


@pytest.mark.asyncio
async def test_huy_giua_stream_mang_theo_text_da_in() -> None:
    """Ctrl-C không bao giờ được thử lại, và vẫn phải là huỷ.

    `StreamCancelled` kế thừa `CancelledError` đúng vì vậy: mọi `except
    asyncio.CancelledError` sẵn có ở chat/task/registry vẫn bắt được nó.
    """
    man_hinh: list[str] = []
    bat_dau = asyncio.Event()

    class _StreamTreo(_Stream):
        async def _phat(self) -> Any:
            yield _chunk("Để tôi tính ")
            bat_dau.set()
            await asyncio.sleep(60)

    client = _FakeClient([([], None)])

    async def create(**_kwargs: Any) -> Any:
        return _StreamTreo([], None)

    client.chat.completions.create = create

    chay = asyncio.create_task(_chay(client, on_text=man_hinh.append))
    await bat_dau.wait()
    chay.cancel()
    with pytest.raises(StreamCancelled) as huy:
        await chay

    assert isinstance(huy.value, asyncio.CancelledError)
    assert huy.value.text == "Để tôi tính "
    assert man_hinh == ["Để tôi tính "]
