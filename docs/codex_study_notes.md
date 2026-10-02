# Codex (codex-rs) — Study Notes: cơ chế nén ngữ cảnh

Đối chiếu với `mini_harness/` (chép theo deepseek-harness) để trả lời: Codex cắt
ngữ cảnh cho vừa cửa sổ model bằng gì, theo thứ tự nào, và thứ tự đó có xác
nhận luật "mất mát tăng dần" mà `mini_harness/core/session.py:217-234` đang
phát biểu hay không.

Nguồn: `/home/ding/agent-harness-base/codex/codex-rs/`, chủ yếu
`core/src/compact.rs`, `core/src/tasks/compact.rs`, `core/src/session/turn.rs`,
`core/src/session/context_window.rs`, `core/src/context_manager/history.rs`,
`utils/output-truncation/src/lib.rs`, `protocol/src/openai_models.rs`,
`prompts/templates/compact/`.

---

## Trả lời ngắn

1. **Q1 — KHÁC.** Codex không áp ba phép như ba projection độc lập theo thứ
   tự cố định trên mỗi request. Cắt ruột tool result (b) chạy MỘT LẦN, vĩnh
   viễn, ngay lúc ghi item vào history (`context_manager/history.rs:566-572`),
   không phải một phép chiếu lặp lại mỗi lần gửi. Nén-bằng-tóm-tắt (a) là cơ
   chế tự động chính, bắn ở ~90% cửa sổ (`openai_models.rs:526-537`). Bỏ-item-
   đầu (c, `remove_first_item`) KHÔNG phải lưới an toàn độc lập chạy sau (a) và
   (b) — nó chỉ là một retry NẰM BÊN TRONG (a), chạy khi chính request tóm tắt
   bị API từ chối vì tràn cửa sổ (`core/src/compact.rs:313-325`).
2. **Q2.** Ngưỡng là phần trăm cửa sổ, không đợi tràn thật: mặc định
   `auto_compact_token_limit` = 90% cửa sổ đã resolve
   (`protocol/src/openai_models.rs:526-537`), trần cứng là
   `effective_context_window_percent` = 95% (`openai_models.rs:390-392`). Phần
   đuôi giữ nguyên văn KHÔNG phải "N turn cuối" mà là các **user message**
   gần nhất, gộp tối đa `COMPACT_USER_MESSAGE_MAX_TOKENS` = 20 000 token
   (`core/src/compact.rs:62`, `669-710`) — assistant/tool content cũ không có
   bản nguyên văn nào sống sót ngoài bản tóm tắt.
3. **Q3.** Có, template cố định tại
   `prompts/templates/compact/prompt.md` (nạp qua
   `prompts/src/compact.rs:1-2`). **Không có dòng nào dặn model đừng nhắc tới
   việc hội thoại bị nén** — khác hẳn dòng "Do NOT mention this request, or
   that the conversation was condensed" bên deepseek. Xem mục 3.
4. **Q4 — có spill, nhưng CHƯA nối vào tool result.** Cắt ruột tool result là
   MẤT THẬT: không giữ bản đầy đủ ở đâu để đọc lại bằng locator/call_id (đã tìm
   trong `utils/output-truncation/`, `context_manager/history.rs`,
   `tools/src/tool_output.rs`, `core/src/tools/handlers/`). NHƯNG cơ chế spill
   đầy đủ — ghi ra đĩa, để lại đường về trong context — thì Codex CÓ, chỉ là
   đang áp cho **hook output** chứ không phải tool result:
   `hooks/src/output_spill.rs:126` dựng footer `"Full hook output saved to:
   {path}"`. Xem mục 4.1.
5. **Q5.** Có `/compact` (`tui/src/slash_command.rs:96`). Khác tự động ở chỗ:
   `handlers.rs::compact()` spawn `CompactTask` **không kiểm tra ngưỡng token
   nào cả** (`core/src/session/handlers.rs:245-252`), còn tự động phải qua
   `context_window_token_status(...).token_limit_reached`
   (`core/src/session/turn.rs:567`, `1316`). Manual còn abort turn hiện tại
   trước khi chạy; tự động thì chạy lồng vào trong turn (pre/mid/post).
6. **Q6.** Không có lưới an toàn "gửi nguyên lịch sử quá khổ". Lỗi nén được
   propagate bằng `?` lên tận `run_pre_sampling_compact` /
   `run_auto_compact` (`core/src/session/turn.rs:1316-1330`, `1484-1543`), rồi
   `run_compact_task` phát `EventMsg::Error` cho người dùng
   (`core/src/compact.rs:231-240`) — turn lỗi/dừng, không âm thầm gửi tiếp.

---

## 1. Ba cơ chế, không phải ba bước của một pipeline

Trước khi so khớp với luật "mất mát tăng dần" của `mini_harness`, cần nói rõ:
Codex không có một hàm kiểu `_for_model()` áp liên tiếp ba phép lên TOÀN BỘ
history mỗi lần build request, như
`mini_harness/core/session.py:217-234` làm. Thay vào đó Codex có ba cơ chế
sống ở ba thời điểm khác nhau trong vòng đời một item/turn:

| Cơ chế | Khi nào chạy | Có lặp lại mỗi request không | File:line |
|---|---|---|---|
| (b) cắt ruột tool result | Một lần, lúc ghi item vào history | Không — baked-in vĩnh viễn | `core/src/context_manager/history.rs:566-572` |
| (a) nén bằng tóm tắt | Khi `token_limit_reached` (~90% cửa sổ), hoặc `/compact` thủ công | Có, nhưng chỉ khi ngưỡng/đã yêu cầu | `core/src/compact.rs:114-` (auto), `core/src/tasks/compact.rs:19-` (manual) |
| (c) bỏ item đầu | Chỉ khi request CỦA (a) tự nó tràn cửa sổ | Là retry nội bộ của (a), không phải bước độc lập | `core/src/compact.rs:313-325` |

Bằng chứng (b) chạy một lần tại thời điểm ghi, không phải một phép chiếu lặp
lại: `record_item_with_metadata` nhận TỪNG item mới, và nếu là
`FunctionCallOutput`/`CustomToolCallOutput` thì gọi
`truncate_function_output_payload` ngay tại chỗ rồi lưu kết quả đã cắt vào
`self.items` — không có bước nào sau đó đọc lại nguyên bản để cắt lần nữa:

```rust
// core/src/context_manager/history.rs:566-572
if let ResponseItem::FunctionCallOutput { output, .. }
| ResponseItem::CustomToolCallOutput { output, .. } = &mut processed.item
{
    let policy = metadata
        .and_then(|metadata| metadata.history_truncation_token_limit)
        .map(TruncationPolicy::Tokens)
        .unwrap_or_else(|| with_serialization_allowance(policy));
    truncate_function_output_payload(output, policy, estimate_audio_token_count);
}
```

Bằng chứng (c) không phải lưới an toàn cấp session mà là retry cục bộ bên
trong vòng lặp gọi model của chính (a): nó nằm trong `match attempt_result`
của `run_compact_task_inner_impl`, chỉ kích hoạt khi chính request tóm tắt
(`turn_input`, đã bao gồm toàn bộ history) bị API trả lỗi
`ContextWindowExceeded`:

```rust
// core/src/compact.rs:313-325
Err(e) if matches!(e.details(), CodexErrorDetails::ContextWindowExceeded) => {
    if turn_input_len > 1 {
        // Trim from the beginning to preserve cache (prefix-based) and keep recent messages intact.
        error!(
            "Context window exceeded while compacting; removing oldest history item. Error: {e}"
        );
        history.remove_first_item();
        retries = 0;
        continue;
    }
    sess.set_total_tokens_full(turn_context.as_ref()).await;
    return Err(e);
}
```

`remove_first_item` chỉ được gọi từ đúng một chỗ trong toàn bộ
`core/` (xác nhận bằng grep: `core/src/compact.rs:323` là nơi gọi duy nhất,
còn lại là định nghĩa và test ở `core/src/context_manager/history.rs:667` và
`history_tests.rs`). Không có một bước "bỏ turn cũ nhất khỏi context đang gửi"
chạy SAU khi (a) và (b) đã làm hết sức — khác hẳn
`mini_harness/core/session.py:_within_budget` (`session.py:267-283`), vốn là
bước thứ ba độc lập, luôn chạy sau `_compacted` và `_pruned`, trên MỌI request.

**Kết luận Q1: khác.** Luật "mất mát tăng dần, (a)→(b)→(c) trên mỗi request"
là một quyết định thiết kế của `mini_harness`/deepseek, không phải điều Codex
xác nhận. Codex: (b) là ghi-một-lần tại nguồn; (a) là cơ chế nén chính, tự
chọn lúc nào chạy theo ngưỡng token; (c) là retry hẹp, chỉ tồn tại để cứu
chính request tóm tắt của (a), không áp lên context cuối cùng gửi cho model
trong luồng bình thường.

---

## 2. Ngưỡng kích nén và phần giữ nguyên văn

### 2.1. Ngưỡng — phần trăm, không đợi tràn thật

`ModelInfo::auto_compact_token_limit()` tính giới hạn mặc định bằng 90% cửa sổ
đã resolve, rồi lấy min với giới hạn cấu hình nếu có:

```rust
// protocol/src/openai_models.rs:526-537
pub fn auto_compact_token_limit(&self) -> Option<i64> {
    let context_limit = self
        .resolved_context_window()
        .map(|context_window| (context_window * 9) / 10);
    let config_limit = self.auto_compact_token_limit;
    if let Some(context_limit) = context_limit {
        return Some(
            config_limit.map_or(context_limit, |limit| std::cmp::min(limit, context_limit)),
        );
    }
    config_limit
}
```

Trần cứng riêng là `effective_context_window_percent`, mặc định 95%
(`protocol/src/openai_models.rs:390-392`, field dùng tại
`core/src/session/context_window.rs:82-85` để tính
`full_context_window_limit`). `context_window_token_status_with_config`
(`core/src/session/context_window.rs:52-120`) gộp hai con số này:
`token_limit_reached` = true khi đạt 90%-đã-cấu-hình **hoặc** đạt trần 95%
cứng — tức nén được kích HOÀN TOÀN TRƯỚC khi có lỗi tràn cửa sổ thật từ API,
y hệt tinh thần "nổ trước lúc tràn" của `mini_harness` (`session.py:290-294`),
chỉ khác về cách đo (phần trăm cửa sổ model, không phải `max_tokens` tự cấu
hình).

Ngoài ra còn một ngưỡng thứ hai, tắt theo mặc định:
`model_post_turn_compact_threshold_percent`
(`core/src/config/mod.rs:646-648`) — nén ngay cuối turn nếu usage vượt X% cửa
sổ, 0 = tắt (`unwrap_or_default()` tại `core/src/config/mod.rs:4296-4298`
cho ra 0).

### 2.2. Phần giữ nguyên văn — không phải "N turn cuối"

Khác biệt kiến trúc đáng chú ý nhất với `mini_harness`: sau khi nén, Codex
KHÔNG giữ một dải turn gần nhất (user+assistant+tool) nguyên văn như
`_COMPACTION_RETAIN = 0.16` của `mini_harness` (`session.py:218-219`). Lịch sử
mới chỉ gồm: `initial_context` (tùy chọn) + một số **user message** gần nhất
(chỉ phần text, lấy từ cuối ngược lên, tối đa 20 000 token) + đúng MỘT message
tóm tắt:

```rust
// core/src/compact.rs:62
const COMPACT_USER_MESSAGE_MAX_TOKENS: usize = 20_000;
```

```rust
// core/src/compact.rs:676-682 (trong build_compacted_history_with_limit)
let mut remaining = max_tokens;
for message in user_messages.iter().rev() {
    if remaining == 0 { break; }
    let tokens = approx_token_count(&message.message);
    ...
```

Nghĩa là assistant message và tool call/tool result của các turn gần nhất
KHÔNG có bản nguyên văn nào sống sót sau nén — chúng chỉ còn trong bản tóm
tắt do model viết. Đây là một đánh đổi rõ ràng khác với `mini_harness`, không
phải lỗi đọc nhầm: `mini_harness` ưu tiên giữ nguyên NGỮ CẢNH LÀM VIỆC gần
nhất (vì model "đang thật sự làm việc trên" nó — comment tại
`session.py:218-219`); Codex ưu tiên giữ Ý ĐỊNH CỦA NGƯỜI DÙNG (user message
gốc) và giao phần còn lại cho bản tóm tắt.

---

## 3. Prompt tóm tắt — không có dòng "đừng nhắc tới việc bị nén"

Template nạp tĩnh qua `include_str!`:

```rust
// prompts/src/compact.rs:1-2
pub const SUMMARIZATION_PROMPT: &str = include_str!("../templates/compact/prompt.md");
pub const SUMMARY_PREFIX: &str = include_str!("../templates/compact/summary_prefix.md");
```

Nguyên văn `prompts/templates/compact/prompt.md`:

```text
You are performing a CONTEXT CHECKPOINT COMPACTION. Create a handoff summary for another LLM that will resume the task.

Include:
- Current progress and key decisions made
- Important context, constraints, or user preferences
- What remains to be done (clear next steps)
- Any critical data, examples, or references needed to continue

Be concise, structured, and focused on helping the next LLM seamlessly continue the work.
```

Và `prompts/templates/compact/summary_prefix.md` (chuỗi đặt trước bản tóm tắt
khi nó được chèn lại vào history của lượt kế tiếp):

```text
Another language model started to solve this problem and produced a summary of its thinking process. You also have access to the state of the tools that were used by that language model. Use this to build on the work that has already been done and avoid duplicating work. Here is the summary produced by the other language model, use the information in this summary to assist with your own analysis:
```

Không có cấu trúc mục cố định kiểu `## Request and Intent` / `## Tool Work`
như bản `mini_harness` tự viết lại (`mini_harness/core/compaction.py:48-93`),
và quan trọng nhất cho câu hỏi gốc: **không có dòng nào dặn model che giấu
việc hội thoại bị tóm tắt/nén**. Đã đọc toàn bộ hai file template (8 dòng),
không có biến thể nào khác trong `prompts/templates/compact/` (thư mục chỉ có
hai file này). Vậy dòng gây `jailbreak: detected` bên Azure (ghi trong
`mini_harness/core/compaction.py:37-42`) là đặc thù của prompt deepseek, không
phải thứ Codex thừa hưởng hay phải né.

---

## 4. Tool result bị cắt là mất thật — nhưng spill thì Codex có, ở chỗ khác

Đã tìm ở ba chỗ hợp lý nhất:

- `utils/output-truncation/src/lib.rs` (toàn bộ 215 dòng đã đọc): hàm
  `truncate_function_output_items_with_policy` (dòng 115-196) cắt rồi chèn
  `"[omitted {n} text items ...]"` — không có trường nào giữ lại vị trí/offset
  của phần bị cắt để đọc lại.
- `core/src/context_manager/history.rs` quanh `record_item_with_metadata`
  (dòng 549-575): cắt rồi ghi thẳng vào `self.items`, bản gốc không được giữ ở
  đâu khác trong struct `History`.
- `tools/src/tool_output.rs:13-16`: `log_output()` tự khai rõ nó là
  **"a deliberately lossy diagnostic representation"** và **"not the
  authoritative tool result; an untruncated log does not imply a complete
  result"** — tức ngay cả log cũng không phải kênh phục hồi, và không có tool
  nào trong `core/src/tools/handlers/` (đã liệt kê toàn bộ thư mục) tên dạng
  `read_tool_result`/`expand_output`/tương tự để model gọi lại bằng
  `call_id`.

Có hai thứ GẦN giống spill nhưng không phải: `core/src/unified_exec/head_tail_buffer.rs`
giữ head+tail của output một tiến trình đang chạy (cho tool `unified_exec` khi
process còn sống, có thể poll tiếp) — đây là buffer của MỘT tool cụ thể, không
phải cơ chế nén-ngữ-cảnh có thể áp cho bất kỳ tool result nào; và
`history::RetainedContext`/`RetainedSource` (`history/src/retained_context.rs`,
`history/src/retained_source.rs`) — đọc qua thấy đây là bộ nhớ cho **user
message gốc** và các câu trả lời `request_user_input` đã xác thực
(`VerifiedQuestionAnswer`, `VerifiedAnswer` tại
`history/src/retained_context.rs:27-38`), phục vụ Guardian/review, không liên
quan tới việc phục hồi tool result bị cắt.

**Kết luận cho TOOL RESULT: không có đường về.** Model không có cách nào đọc
lại nguyên văn một tool result đã bị `truncate_function_output_payload` cắt —
không SEARCH, không offset, không locator nào cả.

### 4.1. Nhưng spill thì có — áp cho hook output

Ba vị trí tìm ở trên đều là đường đi của *tool result*, nên bỏ sót `hooks/`.
Ở đó Codex có đúng cơ chế spill, đủ cả hai nửa:

```rust
// codex-rs/hooks/src/output_spill.rs:11-12
const HOOK_OUTPUTS_DIR: &str = "hook_outputs";
pub(crate) const DEFAULT_HOOK_OUTPUT_TOKEN_LIMIT: usize = 2_500;

// :122-130
/// Builds the model-visible replacement for a spilled hook output.
///
/// The path footer is budgeted before truncation so adding the recovery path
/// does not consume the configured preview budget.
fn spilled_hook_output_preview(text: &str, path: &AbsolutePathBuf, token_limit: usize) -> String {
    let footer = format!("\n\nFull hook output saved to: {}", path.display());
    let preview_policy =
        TruncationPolicy::Tokens(token_limit.saturating_sub(approx_token_count(&footer)));
    format!("{}{footer}", formatted_truncate_text(text, preview_policy))
}
```

Hai điều đáng rút ra:

1. Nói "Codex không có spill" là **sai**. Đúng phải là: Codex có cơ chế spill,
   nhưng mới áp cho hook output (ngưỡng 2 500 token), chưa áp cho tool result.
   Đường về là một đường dẫn file — model đọc lại bằng tool đọc file sẵn có,
   cùng một mẫu như deepseek-harness (`retrievalHint` = "read or grep the
   path") và như Claude Code (`file references`, CHANGELOG 2.1.0–2.1.7).
2. Cả hai harness đều bảo đảm **đường về không bị chính phép cắt ăn mất**,
   nhưng bằng hai cách NGƯỢC nhau. Codex trừ độ dài footer khỏi ngân sách
   trước khi cắt, nên kết quả luôn nằm dưới trần. `mini_harness` để marker
   NGOÀI ngân sách (`_prune_text`: "`limit` tính trên nội dung GỐC giữ lại;
   marker nằm ngoài con số đó"), nên kết quả vượt trần một chút nhưng `limit`
   giữ đúng nghĩa "bao nhiêu nội dung thật được giữ" — mà đó mới là con số
   `read_spill` cần để tính offset. Hai cách, cùng một bảo đảm; không bên nào
   phải sửa.

---

## 5. `/compact` thủ công vs tự động

`/compact` tồn tại ở tầng TUI:

```text
tui/src/slash_command.rs:96   SlashCommand::Compact => "summarize conversation to prevent hitting the conte..."
```

đi qua `Op::Compact` tới handler:

```rust
// core/src/session/handlers.rs:245-252
pub async fn compact(sess: &Arc<Session>, sub_id: String) {
    // Stop the old turn before the compact task picks up the next turn's environments.
    sess.abort_all_tasks(TurnAbortReason::Replaced).await;
    let turn_context = sess
        .new_turn_with_default_settings(sub_id, Default::default())
        .await;
    sess.spawn_task(turn_context, Vec::new(), CompactTask).await;
}
```

Không có bất kỳ điều kiện `token_limit_reached` hay ngưỡng nào ở đây — gọi là
chạy. Khác với đường tự động, luôn phải qua
`context_window_token_status(...).token_limit_reached` trước khi được gọi,
dù ở điểm pre-turn (`core/src/session/turn.rs:1316`,
`run_pre_sampling_compact`) hay mid-turn (`turn.rs:567-603`,
`should_roll_over`/`run_auto_compact`) hay post-turn
(`turn.rs:715-723`, cần thêm `model_post_turn_compact_threshold_percent > 0`).
Về phân loại pha, có hẳn một enum ghi lại bốn điểm kích hoạt khả dĩ:

```rust
// analytics/src/facts.rs:473-478
pub enum CompactionPhase {
    StandaloneTurn,   // /compact thủ công
    PreTurn,
    MidTurn,
    PostTurn,
}
```

Manual (`StandaloneTurn`) còn khác ở việc abort turn đang chạy trước
(`abort_all_tasks(TurnAbortReason::Replaced)`), còn nén tự động (Pre/Mid/Post)
chạy LỒNG vào trong vòng đời turn hiện tại, không abort gì.

(Ghi chú phụ, không phải trọng tâm: còn một đường "remote compaction v2"
— `core/src/compact_remote_v2.rs` — nơi việc tóm tắt được đẩy sang xử lý
phía server OpenAI thay vì model tự viết summary qua một turn nội bộ như ở
trên. Đã xem qua chữ ký ba hàm `run_remote_compact_task*` nhưng KHÔNG đọc kỹ
toàn bộ 1302 dòng; chính sách bên trong server đó không nằm trong repo này
nên không khẳng định được gì thêm về nó.)

---

## 6. Nén hỏng thì sao — không có lưới an toàn "gửi nguyên văn quá khổ"

Lỗi từ cả `run_inline_auto_compact_task` lẫn các biến thể remote/token-budget
đều được propagate bằng `?` lên `run_auto_compact`
(`core/src/session/turn.rs:1309-1330, 1484-1543`), rồi lên tới
`run_pre_sampling_compact`/vòng lặp turn — nghĩa là một lỗi nén (mất mạng,
model từ chối, v.v.) làm hỏng cả lời gọi, không có nhánh "bỏ qua, gửi tiếp
history quá khổ" ở tầng này. Bản thân `run_compact_task` (đường vào chung cho
cả manual lẫn auto) bắt lỗi ở cấp cao hơn và phát sự kiện lỗi cho người dùng
thay vì nuốt lặng lẽ:

```rust
// core/src/compact.rs:231-240 (trong run_compact_task, sau khi gọi *_impl)
if let Err(err) = &result
    && !matches!(phase, CompactionPhase::PostTurn)
    && !matches!(
        err.details(),
        CodexErrorDetails::Interrupted | CodexErrorDetails::TurnAborted
    )
{
    sess.track_turn_codex_error(turn_context.as_ref(), err);
    if !matches!(phase, CompactionPhase::PreTurn) {
        let event = EventMsg::Error(err.to_error_event(/*message_prefix*/ None));
        sess.send_event(&turn_context, event).await;
    }
}
```

Trường hợp duy nhất Codex TỰ XỬ LÝ một lỗi tràn cửa sổ là chính bên trong
vòng lặp của (a) — mục 1, `ContextWindowExceeded` thì bỏ item đầu rồi thử lại;
mọi lỗi khác (mạng, usage limit, v.v.) đều dừng và báo lỗi. Không thấy một
listener riêng kiểu "bắt lỗi context-window-exceeded của chính request LÀM
VIỆC (không phải request tóm tắt) rồi mới cắt" — đã tìm bằng grep
`ContextWindowExceeded` trong toàn bộ `core/src/` và chỉ thấy nó được xử lý
ở hai chỗ: bên trong vòng lặp nén (mục 1) và ở việc TÍNH ngưỡng trước khi gọi
model (mục 2) — không phải một lưới bắt-sau-khi-API-đã-từ-chối-request-chính.

**Kết luận Q6:** không có lưới an toàn "cứ gửi, hỏng thì gửi nguyên văn quá
khổ". Nén hỏng = turn lỗi, báo cho người dùng, không tự động thử phương án
khác (ngoại trừ chính retry nội bộ của mục 1).

---

## Câu không trả lời được / chưa đào sâu

- Chính sách bên trong `compact_remote_v2.rs` (OpenAI xử lý server-side) —
  chỉ xem chữ ký hàm, không đọc hết logic 1302 dòng; không khẳng định nó có
  tuân cùng ngưỡng 90%/95% hay có cơ chế bỏ-item-đầu riêng hay không.
- `core/src/compact_token_budget.rs` (đường "TokenBudget" feature-gated, dùng
  khi `Feature::TokenBudget` bật) — chỉ thấy nó được gọi thay thế cho đường
  summarization thường (`core/src/session/turn.rs:1502`,
  `core/src/tasks/compact.rs:40-43`), chưa đọc nội dung file để biết nó có
  tóm tắt hay chỉ "reset context window" thuần túy (tên file + comment tại
  `turn.rs:721-722`: "Token-budget resets do not summarize" gợi ý là KHÔNG
  tóm tắt, nhưng chưa xác nhận bằng cách đọc file).
- Guardian/review subsystem (`core/src/guardian/`, `history/src/guardian_history.rs`)
  có một lịch sử nén riêng cho reviewer — thấy tên nhưng không đọc, ngoài
  phạm vi câu hỏi gốc (không liên quan tới nén context chính mà model thấy).

---

## Chỗ mini_harness phải xem lại

1. **Luật "mất mát tăng dần, ba bước cố định trên mỗi request"
   (`mini_harness/core/session.py:217-234`) không phải một luật thiết kế phổ
   quát — nó là lựa chọn của deepseek/mini_harness, và Codex không xác nhận
   nó.** Nên sửa comment ở `session.py:217-220` từ giọng "đây là lý do của thứ
   tự này" (ngụ ý một nguyên lý chung) sang giọng "đây là đánh đổi mini_harness
   chọn, đối chiếu Codex thì khác: Codex cắt tool-result một lần vĩnh viễn lúc
   ghi (không phải một phép chiếu lặp lại), và chỉ bỏ item đầu như một retry
   hẹp để cứu CHÍNH request tóm tắt, không phải một bước tổng quát sau cùng."
2. Việc `mini_harness` giữ nguyên văn 16% ngân sách cuối (gồm cả
   assistant/tool) là một lựa chọn TỐT HƠN theo một tiêu chí (giữ ngữ cảnh làm
   việc), nhưng nên ghi rõ đây là khác biệt thiết kế có chủ đích với Codex
   (Codex chỉ giữ text của user message gần nhất, 20k token), không phải điều
   "Codex cũng làm vậy" — tài liệu hiện tại của `mini_harness` không so sánh
   điểm này nên dễ bị đọc nhầm là hội tụ với upstream.
3. Spill **không** phải chỗ `mini_harness` vượt lên trên các harness đã khảo
   sát — cả ba đều có (xem mục 4.1). Chỗ khác nhau là *đã nối vào đâu*:
   deepseek và Claude Code nối vào tool result, Codex mới nối vào hook output.
   Và chỗ khác nhau thứ hai, sâu hơn: ở cả ba harness kia kho spill là một kho
   RIÊNG ghi ra đĩa, còn ở `mini_harness` kho spill chính là event log, vì log
   append-only vốn đã giữ bản đầy đủ. Đó mới là chỗ đáng ghi một dòng.

4. Không có việc phải sửa ở `_prune_text`: bảo đảm "locator không bị cắt mất"
   đã có sẵn, chỉ là đạt bằng cách ngược với Codex (xem mục 4.1).
