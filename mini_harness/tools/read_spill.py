"""Tool read_spill: đọc lại khúc giữa mà phép chiếu đã giấu đi.

Đây là nửa còn lại của việc cắt tool result. `Session.to_messages()` cắt ruột
một kết quả quá khổ và để lại locator (xem `_prune_text`); tool này cầm locator
đó đọc ngược vào log. Có nó thì cắt là CẤT ĐI; không có nó thì cắt là HUỶ, và
model không có cách nào biết mình vừa mất gì.

Chỗ lệch có chủ ý so với upstream: `spill/` bên đó phải dựng hẳn một
`SpillStore` cộng backend `spill-local/` ghi ra file riêng, vì transcript của
nó KHÔNG giữ bản đầy đủ — policy thay luôn kết quả model-facing trước khi nó
vào transcript. Ở đây thì ngược: log là append-only và đã giữ nguyên bản đầy
đủ, cắt chỉ xảy ra ở phép chiếu. Nên kho spill ĐÃ CÓ SẴN, chính là
`session.events`. Thêm một kho thứ hai là chép dữ liệu ra hai chỗ rồi tự chuốc
lấy retention, cleanup và chuyện hai bản lệch nhau — tất cả để giải một bài
toán mà event log vốn đã giải rồi.

Cái giá của lựa chọn đó: spill sống đúng bằng đời của session. Upstream giữ
file qua nhiều phiên và grep được; ở đây hết phiên là hết, trừ khi chạy
`--session` (khi đó log trên đĩa chính là bản lưu). Đổi lại là 0 dòng storage.

Vế "grep được" ở trên không phải phép ẩn dụ suông: `read` của upstream đi kèm
`grep` để tìm trước khi đọc, vì một kết quả bị giấu có thể dài hàng chục nghìn
ký tự và model không có cách nào biết cần đọc ở offset nào. Đây KHÔNG phải một
tính năng mới tự nghĩ ra — nó là nửa còn lại của cùng cặp công cụ đã có sẵn ở
chỗ khác, dựng lại trên nền event log thay vì file. Tham số `query` dưới đây
đóng đúng vai trò của `grep`: tìm VỊ TRÍ, không trả nội dung; model thấy vị trí
rồi tự gọi lại đúng tool này với `offset` để đọc, như `read` vẫn luôn làm.
"""

from __future__ import annotations

from typing import Any

from mini_harness.core.session import Session
from mini_harness.tools.registry import ToolDefinition, define_tool

# Cửa sổ mặc định cho một lần đọc. Nhỏ hơn hẳn trần chiếu của `app.py`: mục
# đích của tool này là lấy đúng khúc cần, không phải kéo ngược cả trang vào
# context — làm thế thì vừa cắt xong lại tự nhét vào.
_MAC_DINH = 4_000

# Trần cứng, kể cả khi model xin nhiều hơn. Xin quá trần không phải lỗi: cắt
# xuống rồi nói ra còn hơn bắt model đoán lại con số đúng.
#
# Cửa sổ vượt trần chiếu thì chính kết quả này cũng bị cắt ruột — vô hại, và
# cố ý không chống: nó chỉ là luật cũ áp lại một cách nhất quán.
_TRAN = 16_000

# Nửa cửa sổ trích đoạn quanh một chỗ khớp. Đủ để model thấy chỗ khớp nằm
# trong câu nào mà không kéo cả đoạn văn về — đọc trọn vẫn là việc của
# `read_spill` qua offset, trích đoạn ở đây chỉ để NHẬN RA chỗ khớp đúng ý.
_NUA_TRICH_DOAN = 100

# Trần số kết quả search. Không phải để tiết kiệm token cho MỘT câu trả lời —
# một query mơ hồ kiểu "the" ra hàng nghìn chỗ khớp trong một kết quả search
# dài, và liệt kê hết thì search tự nó lại thành một tool result cần bị cắt.
_TRAN_KET_QUA = 20


def _tim_vi_tri(text: str, query: str, call_id: str) -> str:
    """Tìm chuỗi con `query` trong `text`, trả về VỊ TRÍ chứ không trả nội dung.

    Đây là `grep`, không phải `read`: model tìm được offset rồi tự quay lại
    gọi `read_spill` với offset đó để đọc. Khớp chuỗi con thường, không phân
    biệt hoa thường, không regex — đủ cho việc "chỗ nào nhắc tới X", không
    nhằm thay một công cụ tìm kiếm thật.

    Không khớp KHÔNG phải lỗi: nó là một câu trả lời hợp lệ, y như
    `web_search` không tìm thấy kết quả.
    """
    nguon = text.lower()
    can_tim = query.lower()
    vi_tri: list[int] = []
    tu = 0
    while True:
        idx = nguon.find(can_tim, tu)
        if idx == -1:
            break
        vi_tri.append(idx)
        # Bước qua hết độ dài query, không phải +1: khớp chồng lấn của cùng
        # một chuỗi lặp (vd query "aa" trong "aaaa") chỉ làm nhiễu danh sách,
        # không thêm thông tin gì cho model.
        tu = idx + len(can_tim)

    if not vi_tri:
        return f'No match for "{query}" in tool result {call_id} ({len(text)} characters).'

    tong = len(vi_tri)
    hien = vi_tri[:_TRAN_KET_QUA]
    dong: list[str] = []
    for idx in hien:
        dau = max(0, idx - _NUA_TRICH_DOAN)
        cuoi = min(len(text), idx + len(query) + _NUA_TRICH_DOAN)
        trich = text[dau:cuoi]
        if dau > 0:
            trich = "…" + trich
        if cuoi < len(text):
            trich = trich + "…"
        dong.append(f"offset {idx}: {trich}")

    con_lai = tong - len(hien)
    phan_duoi = (
        f"\n\n({con_lai} more match(es) not shown; narrow your query to see them.)"
        if con_lai
        else ""
    )
    return (
        f'{tong} match(es) for "{query}" in tool result {call_id}. '
        "Call read_spill again with one of these offsets to read it in full:\n\n"
        + "\n\n".join(dong)
        + phan_duoi
    )


def read_spill_tool(session: Session) -> ToolDefinition:
    """Khai báo tool, với session nhận TỪ NGOÀI.

    Cùng một luật đã áp cho `sandbox` của write_file và `api_key` của
    web_search: tool nhận môi trường qua tham số. Ở đây "môi trường" tình cờ
    lại là chính session — nhưng tool vẫn chỉ ĐỌC, không bao giờ append. Ghi
    vào log là việc của `core/loop.py`, và một tool ghi được vào log sẽ làm
    hỏng đúng cái tính chất khiến replay tin được.
    """

    async def read(args: dict[str, Any]) -> str:
        call_id = args["call_id"]
        text = next(
            (
                event["content"]
                for event in session.events
                if event["type"] == "tool_result" and event["call_id"] == call_id
            ),
            None,
        )
        if text is None:
            raise ValueError(
                f"no tool result with call_id {call_id!r} in this conversation; "
                "use the call_id printed in the result you want to read"
            )

        # `query` có mặt thì đây là một lượt TÌM, không phải ĐỌC: hai việc
        # dùng chung một kho (`text` ở trên) nên gộp vào một tool thay vì
        # tách tool thứ hai phải tự lặp lại đúng đoạn tra cứu call_id này.
        query = args.get("query")
        if query:
            return _tim_vi_tri(text, query, call_id)

        offset = max(0, int(args.get("offset", 0)))
        length = min(max(1, int(args.get("length", _MAC_DINH))), _TRAN)
        if offset >= len(text):
            raise ValueError(
                f"offset {offset} is past the end of this {len(text)}-character "
                "result; the largest usable offset is "
                f"{max(0, len(text) - 1)}"
            )

        end = min(offset + length, len(text))
        con_lai = len(text) - end
        # Nói ra phần CÒN LẠI và offset kế tiếp: thiếu nó thì model phải tự
        # cộng, và một lần cộng sai là một khúc bị bỏ qua mà không ai biết.
        duoi = (
            f"[{con_lai} characters remain after this window; "
            f"call read_spill again with offset={end} to continue.]"
            if con_lai
            else "[End of this result.]"
        )
        return (
            f"Characters {offset}-{end} of {len(text)} "
            f"from tool result {call_id}:\n\n{text[offset:end]}\n\n{duoi}"
        )

    return define_tool(
        name="read_spill",
        description=(
            "Read any part of an earlier tool result that was shown to you "
            "truncated. A truncated result states its call_id and which "
            "character offsets are hidden; pass that call_id here with the "
            "offset you want. The full text is always available this way, so "
            "do not re-run the original tool just to see the hidden part. "
            "If you don't know which offset to read, pass `query` instead of "
            "`offset`/`length` to search the stored result for a substring "
            "first; it returns match locations, not content, so read the "
            "offset it gives you with a second call."
        ),
        parameters={
            "call_id": {
                "type": "string",
                "required": True,
                "description": "The call_id named in the truncated tool result.",
            },
            "offset": {
                "type": "integer",
                "description": (
                    "Character offset to start reading from. Defaults to 0, "
                    "which is the start of the result."
                ),
            },
            "length": {
                "type": "integer",
                "description": (
                    f"How many characters to return. Defaults to {_MAC_DINH}, "
                    f"capped at {_TRAN}. Ask for a small window and continue "
                    "with a second call rather than pulling the whole text back "
                    "into context."
                ),
            },
            "query": {
                "type": "string",
                "description": (
                    "A literal substring to search for in the stored result "
                    "(case-insensitive, no regex). When set, this call ignores "
                    "`offset`/`length` and instead returns up to "
                    f"{_TRAN_KET_QUA} match locations with a short excerpt "
                    "around each one, so you can see where the text you want "
                    "lives before reading it in full."
                ),
            },
        },
        execute=read,
    )
