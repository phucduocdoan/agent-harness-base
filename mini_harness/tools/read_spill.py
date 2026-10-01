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
            "do not re-run the original tool just to see the hidden part."
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
        },
        execute=read,
    )
