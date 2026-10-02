"""Nén ngữ cảnh bằng một bản tóm tắt do chính model viết.

Đây là phép cắt ÍT MẤT MÁT NHẤT trong ba phép mà `Session._for_model` áp lên
log, và là phép duy nhất phải trả tiền: nó tốn một lần gọi model. Hai phép kia
(cắt ruột tool result, bỏ trọn turn cũ) đều miễn phí và thuần tính toán.

Chia việc với `core/session.py` theo đúng một đường: Session ĐO, file này GỌI.
Trả lời được "có cần nén không, nén tới đâu" thì phải biết ngân sách, phép
chiếu và phép neo token — Session có cả ba. Còn gọi model thì Session không
làm, và không nên làm: nó là nguồn sự thật của một hội thoại, không phải một
client. `CompactionPlan` là cái duy nhất đi qua đường đó.

File này KHÔNG import implementation nào, cùng luật với `core/loop.py`.

Đối chiếu harness thật: packages/compaction/compaction-basic.
"""

from __future__ import annotations

from typing import Any, Protocol

from mini_harness.core.types import AssistantMessage, CompactionPlan

# Instruction đi ở message CUỐI CÙNG, sau toàn bộ vùng cần tóm tắt. Đặt ở đầu
# (như một system prompt riêng) thì mất prefix cache, vì prefix hết ấm ngay từ
# byte đầu tiên.
#
# Model-facing nên viết tiếng Anh. Mục chia sẵn chứ không để model tự nghĩ bố
# cục: bản tóm tắt này sẽ bị tóm tắt tiếp ở lần nén sau, và một cấu trúc cố
# định thì gộp được, còn văn xuôi tự do thì mỗi lần gộp lại rụng một ít.
#
# Khác upstream ở phần mục: bên đó là coding agent nên có "Files and Code";
# ở đây agent có thể là `tutor` hay `research`, không đụng tới file nào. Mục
# phải nói được cho cả ba persona.
#
# Và khác upstream ở một dòng Rules, vì ĐO ĐƯỢC: bản upstream viết "Do NOT
# mention this request, or that the conversation was condensed", và Azure
# content filter từ chối nguyên request với `jailbreak: detected: True`, HTTP
# 400. Dò từng dòng thì chính dòng đó đứng một mình lại qua được — cái bị bắt
# là NGỮ CẢNH: một instruction dựng sẵn bố cục rồi dặn model đừng nói ra là
# mình được dặn, đọc lên đúng dạng prompt injection.
#
# Nên diễn đạt lại theo thứ thật sự cần: bản tóm tắt phải là một BẢN GHI, không
# phải một lượt trả lời. Hiệu quả y hệt mà không phải nhờ model giấu gì cả.
# Còn việc "đừng nhắc tới checkpoint" thì thuộc về phía model ĐỌC, và nó đã
# nằm sẵn trong `_CHECKPOINT_PREAMBLE` của session.py — đúng chỗ của nó.
_INSTRUCTION = """\
Write a checkpoint that condenses everything above, so the conversation can \
continue after the original messages are dropped. Use exactly these sections:

## Request and Intent
- [what the user asked for; quote verbatim where exact wording matters]

## Established Facts
- [what has been settled: findings, decisions, numbers, names, and why]

## Tool Work
- [which tools ran, with what arguments, and what came back. For a tool result \
that still matters, name its call_id: the full result is still stored and can \
be read back by call_id, so a pointer is worth more here than a paraphrase]

## Pending Work
- [requested work not finished yet, and anything still unresolved]

## Current State
- [precisely where the conversation stands right now]

Rules:
- Concise engineering prose. Preserve exact identifiers, numbers, commands, \
error strings and quoted wording.
- Capture the user's corrections and explicit instructions faithfully.
- Write a standalone record, not a reply: no greeting, no closing, and no reference to these instructions.
- Output only the checkpoint text. Do not call any tool.
- If a <compacted-summary> block already appears above, it is an EARLIER \
checkpoint: merge it into this one under the same sections, keeping what is \
still true and dropping what is not. Do not copy it forward verbatim.
"""


class Summarizer(Protocol):
    """Phần duy nhất của LLM mà việc nén cần tới."""

    async def generate(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> AssistantMessage: ...


class CompactionSession(Protocol):
    """Phần duy nhất của Session mà việc nén cần tới.

    Khai báo ở đây chứ không dùng lại `Session` của `core/loop.py`: mỗi
    consumer tự nói ra contract của riêng mình. Loop thì ngược lại — nó kế
    thừa Protocol này, vì nó là chỗ gọi `maybe_compact`.
    """

    def append(self, event: dict[str, Any]) -> None: ...

    def plan_compaction(self, *, force: bool = False) -> CompactionPlan | None: ...

    def apply_compaction(self, plan: CompactionPlan, summary: str) -> str | None: ...


async def maybe_compact(
    *,
    session: CompactionSession,
    llm: Summarizer,
    system: str,
    tools: list[dict[str, Any]],
    force: bool = False,
) -> bool:
    """Nén nếu Session bảo là cần. Trả về có nén được hay không.

    `force=True` là `/compact` gõ tay: cùng một đường, chỉ khác ở chỗ Session
    không hỏi ngưỡng nữa. Không tách thành hàm riêng, vì mọi thứ sau lúc lập
    kế hoạch — gọi model, bỏ qua tool call, ghi lý do khi hỏng — đều y hệt;
    tách ra là nhân đôi phần dễ lệch nhau nhất.

    `system` và `tools` truyền vào Y HỆT request vừa gửi, không phải một bộ rút
    gọn. Nghe thì thừa — model chỉ cần viết văn, đâu cần tool schema — nhưng
    prefix cache của provider so sánh từ byte đầu tiên, nên bớt đi một tool
    schema là làm nguội toàn bộ phần phía trước. Upstream nói thẳng cái giá:
    "Routing the summarizer to a different provider/model ... forgoes this
    reuse."

    Model có gọi tool trong lúc tóm tắt thì KỆ: chỉ phần text đi vào checkpoint.
    Đối chiếu compaction-basic: "only returned text enters the checkpoint,
    excluding reasoning and tool calls". Coi đó là thất bại thì một lần model
    lỡ tay sẽ làm hỏng cả phép nén dù nó vẫn viết đủ bản tóm tắt.

    Hỏng thì KHÔNG làm hỏng turn: ghi lại một event rồi để vòng lặp chạy tiếp
    với `_within_budget`. Đây là chỗ cố ý lệch khỏi upstream, và lệch vì kiến
    trúc chứ không vì tiện: bên đó tóm tắt hỏng thì "proceeds with full
    over-budget history", vì nó còn một listener riêng bắt lỗi
    CONTEXT_WINDOW_EXCEEDED thật từ provider rồi mới cắt. Bản này không có cái
    listener đó — `_within_budget` là tấm lưới duy nhất, nên lưới phải luôn
    căng. Bỏ lưới ở đây nghĩa là tóm tắt hỏng -> request vượt cửa sổ -> API
    trả 400 -> chết cả turn.

    Nhưng lưới không phải là lý do để thử mãi: hỏng liên tiếp đủ nhiều thì
    `plan_compaction` tự ngắt (xem `_COMPACTION_MAX_FAILURES`). Cầu dao nằm
    bên đó chứ không nằm đây, vì đếm được là phải đọc log — mà log là của
    Session. File này chỉ ghi vào, không đếm.
    """
    plan = session.plan_compaction(force=force)
    if plan is None:
        return False

    try:
        reply = await llm.generate(
            system=system,
            messages=[*plan.messages, {"role": "user", "content": _INSTRUCTION}],
            tools=tools,
        )
    except Exception as error:  # noqa: BLE001 - lỗi nào cũng phải rơi về lưới
        _ghi_that_bai(session, f"{type(error).__name__}: {error}")
        return False

    tu_choi = session.apply_compaction(plan, reply.text)
    if tu_choi is None:
        return True
    # Lý do do Session viết, không phải file này đoán: chỉ bên đó mới có con số.
    _ghi_that_bai(session, tu_choi)
    return False


def _ghi_that_bai(session: CompactionSession, reason: str) -> None:
    """Ghi lần nén hỏng vào log — ghi mà KHÔNG chiếu.

    Nén hỏng rồi im lặng là thứ tệ nhất ở đây: hội thoại vẫn chạy, chỉ là mỗi
    lượt lại rụng một turn cũ, và không chỗ nào nói ra tại sao. Event này làm
    nó hiện ra trong log và trong `--dump`.

    Cùng họ với event `agent`: nằm trong log, không bao giờ lên wire. Nó là
    chuyện nội bộ của harness, không phải chuyện model cần đọc — mà kể cho
    model nghe thì còn mời nó bình luận về chính cơ chế đang giấu đi.
    Đối chiếu compaction/: "the attempt is recorded in the session log".
    """
    session.append({"type": "compaction_failed", "content": reason})
