"""Thử lại một request hỏng — nhưng chỉ khi thử lại được.

Ranh giới của cả file này nằm ở một câu: **một request chưa phát chữ nào là
một giao dịch nguyên tử.** Chưa có gì in ra màn hình, chưa có gì vào log, nên
gửi lại lần nữa thì không ai ở tầng trên biết — và không cần biết. Ngược lại,
một request đã phát ra ba chữ thì không còn nguyên tử: gửi lại là in chồng lên
cái người dùng vừa đọc, và terminal thì không có lệnh xoá-cái-vừa-in.

Vì vậy điều kiện "thử lại được" KHÔNG phải một cờ truyền tay, mà chính là kiểu
exception: `StreamInterrupted` chỉ được ném ra khi đã có chữ đi qua `on_text`
(xem `llm/stream.py`), nên gặp nó là biết ngay đã quá ranh giới. Mọi lỗi khác
đều xảy ra trước token đầu.

Đối chiếu — và đây là chỗ bản này CỐ Ý khác harness thật. deepseek-harness thử
lại kể cả khi stream đã phát nửa câu (agent.ts:432): adapter của họ biến lỗi
giữa stream thành một chunk kết thúc, rồi hỏi waterfall `agent/request-error`
xem có thử lại không, và UI đánh dấu lần thử chết bằng `attemptId` rồi vẽ lại
(assistant-stream.ts). Bản này không có khả năng đó — màn hình là terminal,
`print()` đã chạy là không rút lại được — nên ranh giới phải lùi về trước token
đầu tiên. Khác nhau ở năng lực của kênh hiển thị, không phải ở triết lý.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from typing import Any

from mini_harness.core.types import StreamInterrupted

# Mỗi lần thử lại là một dict event, không phải một dòng text. Hai lý do.
# Thứ nhất, kênh text đã có chủ: `on_text` chảy thẳng vào `StreamAccumulator`,
# nên một thông báo đi đường đó sẽ trở thành lời của chính model. Thứ hai,
# dict đi được cả hai nơi cần đến: `Session.append` (để log có bằng chứng) và
# từ đó `Session.on_event` lo phần hiển thị. Ai nối hai đầu là việc của app.py.
OnRetry = Callable[[dict[str, Any]], None]

# Thử tối đa ngần này LẦN (tính cả lần đầu).
_MAX_ATTEMPTS = 5
# Chờ bao lâu trước lần thử đầu tiên, rồi gấp đôi dần.
_INITIAL_DELAY = 0.5
# Trần của phép gấp đôi. Chờ lâu hơn ngần này thì người dùng đã bấm Ctrl-C rồi.
_MAX_DELAY = 10.0
# Nhiễu ±10% quanh mốc đã tính. Không có nó, nhiều client cùng bị 429 một lúc
# sẽ cùng tỉnh dậy một lúc và đạp nhau lần nữa.
_JITTER = 0.1
# Lỗi tạm thời: đáng thử lại. 408/409 là tranh chấp phía server, 429 là quá
# tải, 5xx là server hỏng. Mọi mã khác (401 sai key, 400 sai request) thử lại
# bao nhiêu lần cũng ra đúng kết quả đó.
_RETRYABLE_STATUS = frozenset({408, 409, 429})


async def with_retry(
    attempt: Callable[[], Awaitable[Any]],
    *,
    on_retry: OnRetry | None = None,
) -> Any:
    """Gọi `attempt()`, thử lại khi nó hỏng vì lý do tạm thời.

    `attempt` là một callable không tham số chứ không phải một coroutine đã
    dựng sẵn: một coroutine chỉ await được MỘT lần, nên truyền nó vào đây là
    tự chặn hết mọi lần thử thứ hai.
    """
    for exponent in range(_MAX_ATTEMPTS):
        try:
            return await attempt()
        except StreamInterrupted:
            # Đã có chữ trên màn hình người dùng. Xem docstring đầu file.
            raise
        except Exception as error:
            if exponent == _MAX_ATTEMPTS - 1 or not _retryable(error):
                raise
            delay = _delay(exponent, error)
            # Báo TRƯỚC khi ngủ, không phải sau. Phần chờ là phần dài nhất của
            # cả vòng này, nên nó cũng là phần dễ bị cắt ngang nhất — máy chết,
            # người dùng bấm Ctrl-C. Báo sau thì mọi lần chờ bị cắt ngang đều
            # biến mất không dấu vết, và log còn lại chỉ nói "turn hỏng" mà
            # không nói harness đã kiên nhẫn bao lâu.
            if on_retry is not None:
                on_retry({
                    "type": "retry",
                    "attempt": exponent + 1,
                    "delay_ms": round(delay * 1000),
                    "error": f"{type(error).__name__}: {error}",
                })
            await asyncio.sleep(delay)
    raise AssertionError("unreachable")


def _retryable(error: BaseException) -> bool:
    """Lỗi này thử lại thì có cơ hội khác đi không?

    Hỏi `status_code` trước, vì nó phân loại chính xác nhất. Không có
    `status_code` nghĩa là request chưa tới được server: đứt mạng, timeout,
    DNS hỏng — đúng loại đáng thử lại. Nhưng không có nghĩa là mọi exception
    không-status đều đáng thử lại: một `TypeError` do chính bản này viết sai
    cũng không có `status_code`, và thử lại nó 5 lần chỉ làm lỗi khó đọc hơn.
    Nên phải hỏi đúng tên: `APIConnectionError` (và `APITimeoutError` kế thừa
    từ nó).

    Import muộn, giống `azure.py`: test chạy được mà không cần cài openai.
    """
    status = getattr(error, "status_code", None)
    if status is not None:
        return status in _RETRYABLE_STATUS or status >= 500
    try:
        from openai import APIConnectionError
    except ImportError:
        return False
    return isinstance(error, APIConnectionError)


def _delay(exponent: int, error: BaseException) -> float:
    """Chờ bao lâu trước lần thử kế tiếp, tính bằng giây."""
    delay = min(_INITIAL_DELAY * 2**exponent, _MAX_DELAY)
    after = _retry_after(error)
    # Server tự nói nó cần bao lâu thì nghe nó — nó biết hạn mức của chính nó.
    # Nhưng chỉ nghe trong tầm trần: một `Retry-After: 3600` là server bảo
    # "mai quay lại", và ngồi ngủ một tiếng trong một turn chat là treo máy
    # chứ không phải kiên nhẫn. Vượt trần thì thà hỏng ngay cho người dùng
    # quyết định. Đối chiếu: llm-retry/index.ts:230.
    if after is not None and after <= _MAX_DELAY:
        delay = after
    return delay * random.uniform(1 - _JITTER, 1 + _JITTER)


def _retry_after(error: BaseException) -> float | None:
    """Đọc header `Retry-After`, tính bằng giây. `None` nếu không có/không hiểu.

    Chỉ nhận dạng SỐ GIÂY. RFC cho phép cả một mốc HTTP-date, nhưng dạng đó
    phải so với đồng hồ server mới có nghĩa, và lệch giờ máy khách là chuyện
    thường — đọc sai một mốc date còn tệ hơn là quay về mốc gấp đôi.
    """
    response = getattr(error, "response", None)
    headers = getattr(response, "headers", None)
    if headers is None:
        return None
    try:
        return float(headers.get("retry-after"))
    except (TypeError, ValueError):
        return None
