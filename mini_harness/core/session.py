"""Session — event log append-only, và phép chiếu (projection) sang message list.

Đây là chỗ dễ làm sai nhất khi tự viết harness: người ta hay lưu luôn
`list[dict]` đúng format OpenAI rồi append vào đó. Harness thật KHÔNG làm vậy:
`session.append('tool/result', ...)` ghi một EVENT vào log, còn message list gửi
lên model là thứ được CHIẾU RA từ log (packages/core/session/src/surface.ts).

Tách hai thứ này ra mới có: resume, replay, checkpoint, và đổi cách render
history mà không mất dữ liệu gốc.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mini_harness.core.types import CompactionPlan

# Result giả ghi cho tool_call chưa kịp chạy. Text lấy đúng harness thật
# (tool-calls.ts:253) vì nó cũng là model-facing text.
ABORTED_BEFORE_DISPATCH = "Error: tool call aborted before dispatch"


@dataclass(slots=True)
class Session:
    """Log append-only các event của một hội thoại.

    Hiện thực Session Protocol trong core/loop.py. Không validate event: agent
    loop là caller duy nhất và nó tự biết mình ghi gì.
    """

    events: list[dict[str, Any]] = field(default_factory=list)
    # Có path thì mỗi event được ghi ngay thành một dòng JSONL. Append-only
    # thật: crash giữa turn vẫn còn nguyên phần đã ghi. Ghi cả cục lúc kết thúc
    # thì mất sạch — mà crash giữa turn là chuyện thường của agent.
    log_path: Path | None = None
    # Nơi UI nghe để hiển thị. Session là nguồn sự thật duy nhất của mọi thứ
    # model thấy, nên echo lên màn hình phải PHÁI SINH từ đây — không phải từ
    # agent loop (loop không sở hữu việc hiển thị) và cũng không phải từ tool
    # registry (nó không biết mình đang chạy trong phiên nào).
    on_event: Callable[[dict[str, Any]], None] | None = None
    # Ngân sách token cho message list. None = không cắt gì (mặc định, vì
    # `core/` không có quyền đoán cửa sổ của model nào). Chủ sở hữu con số này
    # là app.py — nó mới biết đang chạy provider nào.
    max_tokens: int | None = None
    # Trần cho MỘT tool result khi chiếu. None = không cắt. Cũng do app.py sở
    # hữu, cùng lý do với `max_tokens`. Xem `_prune_text` về việc cắt thế nào.
    max_tool_result_chars: int | None = None
    # Lượng hiệu chỉnh cho bộ đoán, học từ `usage` mà API trả về.
    # 0 cho tới khi đo được lần đầu. Xem `_calibrate`.
    _calibration: int = 0

    def append(self, event: dict[str, Any]) -> None:
        self.events.append(event)
        if self.log_path is not None:
            with self.log_path.open("a", encoding="utf-8") as log:
                log.write(json.dumps(event, ensure_ascii=False) + "\n")
        # Phát SAU khi event đã vào list và đã xuống đĩa: chỉ echo cái đã
        # commit, không bao giờ echo một thứ rồi mới ghi (hoặc ghi hỏng).
        # Đối chiếu luật "publish state only at its commit point".
        if self.on_event is not None:
            self.on_event(event)
        if event["type"] == "assistant" and event.get("prompt_tokens"):
            # Cái đã gửi đi ở step này là log TRỪ chính event vừa append.
            self._calibrate(event["prompt_tokens"], self.events[:-1])

    @classmethod
    def resume(
        cls,
        log_path: Path,
        *,
        max_tokens: int | None = None,
        max_tool_result_chars: int | None = None,
    ) -> Session:
        """Đọc lại log và trả về session ghi tiếp vào chính file đó.

        Ngân sách nhận NGAY ở đây chứ không gán sau, vì `_recalibrate` phải
        chiếu log qua đúng chính sách đang chạy. Dựng xong mới gán thì phép neo
        đã chạy trên một phép chiếu không cắt gì — đo thật trên một log có
        tool result 81k ký tự: hiệu chỉnh ra -27194, và ước lượng request kế
        tiếp ra số ÂM. Một session nửa cấu hình là một session sai.

        Log có thể kết thúc giữa turn: một `assistant` có tool_calls mà thiếu
        `tool_result` (bị kill, crash, cancel). Gửi nguyên trạng đó lên API là
        request KHÔNG HỢP LỆ — mọi tool_call bắt buộc phải có result.

        Nên ở đây phải REPAIR. Và repair bằng cách append event thật vào log,
        không phải bịa ra lúc chiếu: nhờ vậy log luôn là bản ghi trung thực của
        cái model đã thực sự nhìn thấy. Đối chiếu tool-calls.ts:8 —
        "Abort records synthetic error results for skipped calls so replay
        stays valid."
        """
        events = [
            json.loads(line)
            for line in log_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        session = cls(
            events=events,
            log_path=log_path,
            max_tokens=max_tokens,
            max_tool_result_chars=max_tool_result_chars,
        )
        session._recalibrate()
        session.abort_pending_tool_calls()
        return session

    def abort_pending_tool_calls(self) -> int:
        """Ghi result giả cho mọi tool_call còn treo. Trả về số call đã vá.

        Hai chỗ gọi, cùng một lý do: (1) resume một log bị cắt giữa turn,
        (2) user huỷ giữa lúc tool đang chạy. Cả hai đều để lại log thiếu
        result, và cả hai đều làm request kế tiếp không hợp lệ nếu không vá.
        Đối chiếu: appendSkippedToolCall() ở tool-calls.ts:250.
        """
        pending = self._orphan_tool_calls()
        for call_id, name in pending:
            self.append({
                "type": "tool_result", "call_id": call_id, "name": name,
                "content": ABORTED_BEFORE_DISPATCH, "is_error": True,
            })
        return len(pending)

    def _orphan_tool_calls(self) -> list[tuple[str, str]]:
        """tool_call nào chưa có result, theo đúng thứ tự model đã gọi."""
        answered = {
            event["call_id"] for event in self.events if event["type"] == "tool_result"
        }
        return [
            (call["id"], call["name"])
            for event in self.events
            if event["type"] == "assistant"
            for call in event.get("tool_calls") or ()
            if call["id"] not in answered
        ]

    # ------------------------------------------------------- ngân sách token

    def _recalibrate(self) -> None:
        """Chạy lại phép neo trên một log vừa đọc từ đĩa.

        `append()` là chỗ phép neo tiến từng bước, nhưng `resume` dựng thẳng
        session từ list nên không đi qua đó. Không chạy lại thì con số API đã
        đo nằm NGAY TRONG LOG bị bỏ phí — đo thật trên một log có sẵn: session
        ước lượng 9662 token cho một request mà API đã báo 6697, thừa 44%, và
        hệ quả là bỏ turn cũ sớm hơn mức cần thiết.

        Cuộn tiến từ đầu chứ không nhảy thẳng tới event cuối, vì `_for_model`
        của mỗi bước phụ thuộc phép neo của bước trước (qua `_within_budget`).
        Cuộn tiến tái hiện đúng thứ tự đã xảy ra lúc chạy thật. Đối chiếu
        llm/token-meter: "advances one isolated fold per session from the
        durable event log".
        """
        self._calibration = 0
        for index, event in enumerate(self.events):
            if event["type"] == "assistant" and event.get("prompt_tokens"):
                self._calibrate(event["prompt_tokens"], self.events[:index])

    def _calibrate(self, prompt_tokens: int, sent: list[dict[str, Any]]) -> None:
        """Neo bộ đoán vào số API thật, bằng một phép trừ.

            hiệu chỉnh = số API báo  -  bộ đoán tự tính cho CÙNG tập event đó

        `sent` là log tại thời điểm request đó đi — caller biết, session thì
        không. Tính lại ở đây thay vì nhờ `to_messages()` ghi lại: phép chiếu
        giữ nguyên tính thuần, và không ai phải nhớ thứ tự gọi giữa hai hàm.

        Hệ quả: `_estimate` của đúng tập event vừa đo sẽ ra lại đúng
        `prompt_tokens`. Mọi sai số đã bị hấp thụ, và chỉ phần event MỚI phát
        sinh sau đó mới còn là số đoán — mà phần đó thì nhỏ, và lại được neo
        lại ở step kế tiếp.

        Con số này GỘP hai thứ ngược dấu nhau, và không tách ra được:
          + phần request message list không nhìn thấy (system prompt, tool
            schema) — luôn dương;
          − sai số của chính bộ đoán — thường âm, vì `_CHARS_PER_TOKEN = 2.5`
            cố ý đoán thừa, mà một tool result tiếng Anh thật thì ~4.9 ký tự
            mỗi token.

        Nên nó PHẢI được phép âm. Đo thật trên một phiên `research`: step đầu
        +272 (đúng là system prompt + 3 tool schema), step sau −2556 sau khi
        một tool result 15.8k ký tự vào log. Chặn ở 0 là vứt bỏ phép hiệu
        chỉnh đúng lúc nó cần nhất.
        """
        self._calibration = prompt_tokens - self._guess(self._for_model(sent))

    # Ước lượng THÔ, cố ý nghiêng về phía đoán thừa: chừng nào chưa có số đo
    # nào thì đoán thiếu nghĩa là tưởng còn chỗ trong khi đã tràn. Đo bằng
    # tiktoken o200k_base: tiếng Anh 4.87 ký tự/token, tiếng Việt 3.35, JSON
    # 2.53. Lấy theo trường hợp tệ nhất.
    #
    # Không import tiktoken ở đây dù nó chính xác hơn: `core/` mà biết tokenizer
    # của một provider cụ thể là phá seam. Và sau request đầu tiên thì không cần
    # nữa — `_calibrate` neo cả bộ đoán này vào số API thật.
    _CHARS_PER_TOKEN = 2.5
    # Mỗi message còn tốn phần bao: role, delimiter của wire format.
    _TOKENS_PER_MESSAGE = 4

    def _guess(self, events: list[dict[str, Any]]) -> int:
        """Đoán số token của riêng phần event. Không cộng overhead."""
        count = 0
        for event in events:
            text = json.dumps(event, ensure_ascii=False)
            count += int(len(text) / self._CHARS_PER_TOKEN) + self._TOKENS_PER_MESSAGE
        return count

    def _estimate(self, events: list[dict[str, Any]]) -> int:
        """Số token của CẢ request: số đoán đã hiệu chỉnh theo lần đo gần nhất."""
        return self._calibration + self._guess(events)

    def _for_model(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Chuỗi chính sách áp lên log trước khi chiếu. KHÔNG sửa `self.events`.

        Ba bước, xếp theo mức độ mất mát TĂNG DẦN:

          1. `_compacted`  — thay khúc đầu bằng bản tóm tắt model đã viết.
             Mất chi tiết, nhưng giữ lại ý; và tool result trong vùng đó vẫn
             đọc lại được qua `call_id`.
          2. `_pruned`     — cắt ruột tool result quá khổ, để lại locator.
             Gần như không mất gì: `read_spill` lấy lại được nguyên văn.
          3. `_within_budget` — bỏ TRỌN turn cũ. Mất hẳn, không có đường về.

        Nên bước 3 là lưới an toàn chứ không phải cơ chế chính: nó chỉ chạy
        khi hai bước trên đã làm hết sức mà vẫn chưa vừa. Đảo thứ tự là bỏ đi
        những turn mà sau khi nén/cắt vốn vẫn vừa chỗ. Đối chiếu
        compaction-tool-result-pruner: "Trimming makes no model call and can
        clear token pressure on its own, so compaction may skip the summary
        entirely."

        Nhưng thứ tự này KHÔNG phải luật chung. Nó là hệ quả của việc cả ba
        phép cắt cùng xảy ra Ở ĐÂY, trên một log còn nguyên vẹn. Codex cắt
        ruột tool result ngay lúc ghi vào history
        (`context_manager/history.rs:566-572`), mất vĩnh viễn, xong trước khi
        phép nén kịp được cân nhắc; và bước 3 của nó (`remove_first_item`)
        chỉ tồn tại bên trong vòng retry của chính phép nén
        (`compact.rs:313-325`). Ba phép cắt không cùng một chỗ thì không có
        thứ tự nào để mà xếp. Xem `docs/codex_study_notes.md`.
        """
        return self._within_budget(self._pruned(self._compacted(events)))

    def _compacted(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Thay khúc đầu bằng bản tóm tắt, nếu log có event `compaction`.

        Chỉ đọc event compaction CUỐI CÙNG. Nén lần hai không sửa gì của lần
        một — nó append một event mới với `covers` lớn hơn, và bản cũ rơi vào
        đúng vùng mà bản mới đứng thay. Không có mâu thuẫn để hoà giải: bản
        mới được model viết ra TỪ một phép chiếu đã chứa bản cũ, nên nội dung
        cũ đã nằm trong đó rồi. Đối chiếu compaction-basic: "If the
        conversation already contains a <compacted-summary> block, it is a
        PRIOR checkpoint... merge newer information into a single consolidated
        summary."

        Nhận `events` qua tham số chứ không đọc `self.events`: `_calibrate`
        chiếu lại từng tiền tố của log để cuộn phép neo, và mỗi tiền tố có thể
        đang ở một trạng thái nén khác nhau.
        """
        marker = next(
            (event for event in reversed(events) if event["type"] == "compaction"), None
        )
        if marker is None:
            return events
        return [_summary_event(marker["content"])] + [
            event for event in events[marker["covers"]:] if event["type"] != "compaction"
        ]

    def _pruned(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Cắt ruột những tool result quá khổ."""
        limit = self.max_tool_result_chars
        if limit is None:
            return events
        return [
            {**event, "content": _prune_text(event["content"], limit, event["call_id"])}
            if event["type"] == "tool_result" and len(event["content"]) > limit
            else event
            for event in events
        ]

    def _within_budget(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Bỏ bớt turn cũ cho vừa `max_tokens`."""
        if self.max_tokens is None:
            return events
        turns = _split_turns(events)
        kept = events
        # Bỏ TRỌN từng turn từ cũ nhất. Cắt lẻ từng event sẽ để lại
        # `tool_result` mồ côi không có `tool_call` đi kèm, và API từ chối
        # nguyên request — hỏng nặng hơn hẳn so với việc tràn cửa sổ.
        while len(turns) > 1 and self._estimate(kept) > self.max_tokens:
            turns.pop(0)
            kept = [event for turn in turns for event in turn]
        # Luôn giữ turn cuối cùng kể cả khi một mình nó đã vượt ngân sách: bỏ
        # nốt thì chẳng còn gì để hỏi model. Quá cỡ ở đây là việc của tầng
        # khác (cắt nhỏ chính câu hỏi), không phải của compaction.
        return kept

    # Nén khi phép chiếu (đã nén + đã cắt ruột, CHƯA bỏ turn) vượt ngần này
    # phần ngân sách. Phải nổ TRƯỚC lúc tràn, vì hai lý do: còn chỗ cho chính
    # request tóm tắt chạy, và vùng đem đi tóm tắt vẫn còn là tiền tố nguyên vẹn
    # của request vừa gửi — tức prefix cache còn dùng lại được.
    _COMPACTION_THRESHOLD = 0.8
    # Phần đuôi giữ NGUYÊN VĂN, tính theo ngân sách. Tóm tắt là mất chi tiết,
    # mà chi tiết của mấy turn gần nhất là thứ model đang thật sự làm việc trên.
    _COMPACTION_RETAIN = 0.16
    # Hỏng liên tiếp bao nhiêu lần thì thôi, không thử nữa. Không có con số này
    # thì mỗi lượt lại tốn một lần gọi model cho đúng cái lỗi vừa rồi — hỏng vì
    # model trả về rỗng hay vì provider từ chối thì lượt sau thường hỏng y hệt.
    # Đối chiếu Claude Code 2.1.76: "stops retrying after 3 failed attempts".
    _COMPACTION_MAX_FAILURES = 3

    def plan_compaction(self) -> CompactionPlan | None:
        """Có cần nén không, và nếu có thì nén tới đâu — trả `None` nếu chưa cần.

        Session trả lời câu này chứ không phải `core/compaction.py`, vì trả lời
        được nó cần đo: ngân sách, phép chiếu, phép neo token. Còn gọi model thì
        Session không làm. Hai việc, hai file.

        Ngưỡng đo trên `_pruned(_compacted(...))` — tức là mọi thứ TRỪ phép bỏ
        turn. Đo sau khi bỏ turn thì con số luôn nằm dưới ngân sách và compaction
        không bao giờ nổ; đo trước khi cắt ruột thì nó nổ cả những lần mà cắt
        ruột (miễn phí, không gọi model) vốn đã đủ. Đối chiếu
        compaction-tool-result-pruner: "Trimming makes no model call and can
        clear token pressure on its own".

        Hỏng quá `_COMPACTION_MAX_FAILURES` lần liên tiếp thì ngắt hẳn, không
        thử nữa. Mất phép nén không làm chết hội thoại — `_within_budget` vẫn
        đỡ, chỉ là từ đó trở đi mỗi lượt rụng một turn cũ thay vì được tóm tắt
        lại. Đó là xuống cấp có kiểm soát, và nó hiện ra trong log qua chuỗi
        event `compaction_failed`.
        """
        if self.max_tokens is None:
            return None
        if self._failures_since_compaction() >= self._COMPACTION_MAX_FAILURES:
            return None
        if self._estimate(self._pruned(self._compacted(self.events))) <= (
            self.max_tokens * self._COMPACTION_THRESHOLD
        ):
            return None

        # Chỉ xét phần log CHƯA bị nén. Nén lại cái đã nén là trả tiền model
        # hai lần cho cùng một đoạn text.
        start = self._compaction_start()
        turns = _split_turns(self.events[start:])
        retain = self.max_tokens * self._COMPACTION_RETAIN
        kept = 0
        total = 0
        # Đi từ turn MỚI nhất lùi lại: cái gần hiện tại là cái phải giữ nguyên văn.
        for turn in reversed(turns):
            cost = self._guess(self._pruned(turn))
            # `kept` ở điều kiện đầu: luôn giữ ít nhất một turn, kể cả khi một
            # mình nó đã vượt `retain`. Nén luôn turn đang chạy là nén mất chính
            # câu hỏi model đang trả lời.
            if kept and total + cost > retain:
                break
            kept += len(turn)
            total += cost

        covers = len(self.events) - kept
        if covers <= start:
            # Không có turn nào mới để nén. Xảy ra khi một turn duy nhất đã
            # chiếm hết ngân sách — compaction không chẻ được một turn, và
            # không giả vờ là chẻ được. Đối chiếu compaction/: "balanced summary
            # compaction cannot split one indivisible unit".
            return None

        # Vùng đem tóm tắt chiếu qua _compacted + _pruned nhưng KHÔNG qua
        # _within_budget: nguồn của bản tóm tắt là LOG, không phải request vừa
        # gửi. Khi bỏ-turn đã phải nổ, bản tóm tắt nhờ vậy còn kéo lại được
        # những turn model vừa mất.
        #
        # Và vì `_render` ánh xạ từng event độc lập, danh sách này là TIỀN TỐ
        # BYTE-FOR-BYTE của `to_messages()` — CHỪNG NÀO `_within_budget` chưa
        # bỏ gì. Ngưỡng 0.8 làm cho đó là trường hợp thường, không phải là bảo
        # đảm: một turn đủ lớn vẫn nhảy thẳng từ dưới ngưỡng lên quá ngân sách
        # trong một bước. Mất tiền tố thì chỉ mất prefix cache cho đúng lần gọi
        # đó, không sai kết quả. Đối chiếu compaction-basic: "the replayed
        # system prompt, tools, and shadowed-region messages match the
        # conversation's last routed request byte-for-byte".
        region = self._render(self._pruned(self._compacted(self.events[:covers])))
        return CompactionPlan(covers=covers, messages=region)

    def apply_compaction(self, plan: CompactionPlan, summary: str) -> str | None:
        """Ghi bản tóm tắt vào log. Trả LÝ DO nếu từ chối, `None` nếu nhận.

        Event mang text THÔ, chưa đóng khung. Khung `<compacted-summary>` và
        lời dẫn checkpoint được dựng ở phép chiếu (`_summary_event`), không
        nằm trong log. Cùng một luật đã áp cho `is_error`: log giữ sự thật,
        wire format là thứ phái sinh. Upstream đạt cùng kết quả bằng hai event
        (`compaction/summary` thô + một `user/message` đã đóng khung); ở đây
        ranh giới log/projection đã làm sẵn việc đó nên một event là đủ.

        Từ chối bản tóm tắt KHÔNG nhỏ hơn vùng nó thay thế — nếu không, "nén"
        có thể làm phình ngữ cảnh ra, và lần sau lại nổ ngưỡng ngay. Đối chiếu
        compaction/: "rejects a summary that does not shrink its source".

        Trả LÝ DO chứ không trả `False`, vì lý do là thứ duy nhất đi được tới
        log: `core/compaction.py` ghi nó vào event `compaction_failed`, và từ
        đó `--dump` đọc được tại sao. Một chữ `False` thì buộc bên gọi phải
        đoán — mà nó không có số liệu để đoán đúng, số liệu nằm ở đây.
        """
        if not summary.strip():
            return "model returned an empty summary"
        truoc = self._guess(self._pruned(self._compacted(self.events[: plan.covers])))
        sau = self._guess([_summary_event(summary)])
        if sau >= truoc:
            return (
                f"summary did not shrink its source: {sau} tokens replacing "
                f"{truoc} tokens over {plan.covers} events"
            )
        self.append({"type": "compaction", "content": summary, "covers": plan.covers})
        return None

    def _failures_since_compaction(self) -> int:
        """Số lần nén hỏng kể từ lần nén THÀNH CÔNG gần nhất.

        Đọc ngược từ log chứ không giữ một biến đếm: log là nguồn sự thật duy
        nhất ở đây, nên resume từ file cũng khôi phục luôn trạng thái cầu dao —
        không phải nhớ thêm thứ gì ngoài log.
        """
        count = 0
        for event in reversed(self.events):
            if event["type"] == "compaction":
                break
            if event["type"] == "compaction_failed":
                count += 1
        return count

    def _compaction_start(self) -> int:
        """Log đã được nén tới event thứ mấy. 0 nếu chưa nén lần nào."""
        return next(
            (event["covers"] for event in reversed(self.events)
             if event["type"] == "compaction"),
            0,
        )

    def to_messages(self) -> list[dict[str, Any]]:
        """Chiếu log thành message list đúng wire format của OpenAI/DeepSeek.

        Một chiều: event -> message. Không có đường ngược lại, và không sửa
        `self.events`. Gọi bao nhiêu lần cũng ra cùng kết quả.

        Có ngân sách thì phép chiếu này LOSSY theo hai cách: turn cũ bị bỏ
        hẳn, và tool result quá khổ bị cắt mất ruột. Cả hai đều chỉ xảy ra ở
        ĐÂY — log vẫn giữ nguyên bản đầy đủ. Đó chính là lý do tách log khỏi
        projection ngay từ đầu: cắt ngữ cảnh mà không mất dữ liệu.
        """
        return self._render(self._for_model(self.events))

    def _render(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Đổi dạng event -> message, KHÔNG áp chính sách nào.

        Tách khỏi `to_messages` vì `plan_compaction` cần chiếu một tiền tố của
        log qua một chuỗi chính sách KHÁC (không bỏ turn). Và vì hàm này ánh xạ
        từng event một cách độc lập — không có trạng thái vắt qua giữa các
        event — nên "chiếu rồi lấy tiền tố" và "lấy tiền tố rồi chiếu" ra cùng
        một thứ. Đó chính là tính chất `plan_compaction` dựa vào để giữ prefix
        cache còn ấm.
        """
        messages: list[dict[str, Any]] = []
        for event in events:
            kind = event["type"]
            if kind == "user":
                messages.append({"role": "user", "content": event["content"]})
            elif kind == "assistant":
                message: dict[str, Any] = {"role": "assistant", "content": event["content"]}
                if event["tool_calls"]:
                    message["tool_calls"] = [
                        {
                            "id": call["id"],
                            "type": "function",
                            "function": {"name": call["name"], "arguments": call["arguments"]},
                        }
                        for call in event["tool_calls"]
                    ]
                messages.append(message)
            elif kind == "tool_result":
                # Wire format KHÔNG có field `is_error`. Đó là lý do tools.py
                # nhét "Error: " vào chính content: model chỉ đọc được content,
                # nên thông tin "cái này lỗi" phải nằm trong text, không phải
                # metadata. `is_error` trong event chỉ để UI/replay dùng.
                messages.append({
                    "role": "tool",
                    "tool_call_id": event["call_id"],
                    "content": event["content"],
                })
            elif kind in ("agent", "compaction_failed"):
                # Event METADATA: ghi vào log nhưng KHÔNG gửi cho model. Nó trả
                # lời câu "log này chạy bằng persona nào" — câu mà resume và
                # replay cần, còn model thì không (persona đã nằm sẵn trong
                # system prompt rồi, chiếu thêm lần nữa là nói hai lần).
                #
                # Nhóm "ghi mà không chiếu". `compaction_failed` cũng vào đây:
                # một lần nén hỏng là chuyện nội bộ của harness, kể cho model
                # nghe là mời nó bình luận về chính cơ chế đang giấu bớt đi.
                # Harness thật có cả một họ như vậy: turn/start, step/start,
                # compaction/*. Nhóm này là lý do `to_messages()` phải là phép
                # CHIẾU có chọn lọc chứ không phải phép đổi dạng 1-1.
                continue
            else:
                raise ValueError(f"event type không biết: {kind!r}")
        return messages


# Lời dẫn đứng trước bản tóm tắt. Model-facing nên viết tiếng Anh. Lấy gần
# nguyên văn compaction-basic, vì mỗi câu ở đó chữa một lỗi hành vi cụ thể:
# "established background" chặn model đi xác minh lại những gì đã chốt;
# "without restating it" chặn nó mở đầu bằng cách kể lại cả bản tóm tắt;
# "without acknowledging this checkpoint" chặn nó nói với user về một cơ chế
# nội bộ mà user không hỏi.
#
# Câu cuối là của riêng bản này: ở đây log là append-only và cũng chính là kho
# spill, nên tool result trong vùng đã nén VẪN đọc lại được. Upstream không nói
# được câu đó vì bên đó surface bị thay thật.
#
# Và cũng như `_prune_text`: nêu sự thật, KHÔNG nhắc tên tool nào. Session
# không biết agent đang chạy cầm tool gì.
_CHECKPOINT_PREAMBLE = (
    "This is an automatically generated checkpoint condensing an earlier span "
    "of the conversation to free up context. Treat the captured context as "
    "established background and build on it without restating it. Continue the "
    "task directly from the messages that follow, without acknowledging this "
    "checkpoint. Tool results from the condensed span are still stored and "
    "retrievable by their call_id."
)


def _summary_event(summary: str) -> dict[str, Any]:
    """Đóng khung bản tóm tắt thành một event `user` để chiếu đi.

    Vai `user` chứ không phải `system`: system message chèn giữa hội thoại là
    thứ mỗi provider xử một kiểu, có chỗ còn từ chối. `user` thì provider nào
    cũng nhận. Upstream cũng thay bằng đúng một `user/message`.

    Thẻ `<compacted-summary>` là ranh giới để model phân biệt "đây là ghi chép
    về hội thoại" với "đây là lời user nói". Thiếu thẻ, model dễ đọc bản tóm
    tắt như một yêu cầu mới.
    """
    return {
        "type": "user",
        "content": (
            f"{_CHECKPOINT_PREAMBLE}\n\n"
            f"<compacted-summary>\n{summary}\n</compacted-summary>"
        ),
    }


def _split_turns(events: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Chẻ log thành từng turn. Một turn bắt đầu ở mỗi event `user`.

    Định nghĩa này làm cho "bỏ trọn một turn" không bao giờ tạo ra mồ côi:
    `assistant` và `tool_result` luôn nằm cùng nhóm với `user` đã sinh ra chúng.
    """
    turns: list[list[dict[str, Any]]] = []
    for event in events:
        if event["type"] == "user" or not turns:
            turns.append([])
        turns[-1].append(event)
    return turns


def _prune_text(text: str, limit: int, call_id: str) -> str:
    """Giữ đầu và ĐUÔI của một tool result, nói rõ phần giữa đi đâu.

    Giữ cả đuôi chứ không cắt cụt: kết luận của một bài viết nằm ở cuối, và
    thông báo lỗi của một lệnh dài cũng nằm ở cuối. Cắt cụt đuôi là bỏ đúng
    phần hay mang câu trả lời.

    Marker là model-facing text nên viết tiếng Anh, và phải nói ra con số: một
    tool result bị cắt mà im lặng sẽ được model đọc như một kết quả đầy đủ.

    Marker mang theo LOCATOR — `call_id` và dải offset bị giấu — chứ không chỉ
    nói "đã cắt". Phần giữa không mất: nó vẫn nằm nguyên trong `self.events`,
    và `tools/read_spill.py` đọc ngược vào đó. Cắt ở phép chiếu là CẤT ĐI,
    không phải HUỶ.

    Nhưng marker KHÔNG nhắc tên tool nào. Session không biết agent đang chạy
    cầm những tool gì, nên nói "dùng read_spill" là nói một câu có thể sai.
    Nó chỉ nêu sự thật và cái locator; dạy cách dùng locator là việc của
    description bên tool. Cùng một đường biên upstream vạch cho `spill/`:
    "The service owns storage only: ... no retrieval or search API."

    `limit` tính trên nội dung GỐC giữ lại; marker nằm ngoài con số đó.

    Đối chiếu: compaction/compaction-tool-result-pruner — "trims each
    over-budget tool result to a bounded head, a short 'middle pruned' marker,
    and a bounded tail"; spill/spill-policy — "replaces the model-facing result
    with a bounded head/tail preview plus the backend's locator".
    """
    # Đầu nhiều hơn đuôi: phần mở đầu thường đã nói chủ đề là gì, còn đuôi chỉ
    # cần đủ để thấy kết luận.
    head = limit * 2 // 3
    tail = limit - head
    return (
        text[:head]
        + f"\n\n[... {len(text) - limit} characters hidden here: offsets "
        f"{head}-{len(text) - tail} of this {len(text)}-character result, "
        f'call_id "{call_id}". The full text is still stored and retrievable. ...]\n\n'
        + text[len(text) - tail:]
    )
