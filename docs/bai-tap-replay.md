# Bài tập: `ReplayLLM` — chạy harness không cần API key

## Đây không phải bài tập bịa

Replay có thật trong harness gốc, ở **ba** tầng khác nhau. Kiểm tra được:

```bash
wc -l deepseek-harness/packages/test-support/llm-replay/src/index.ts   # 1344 dòng
grep -n 'ReplayEnvelope' deepseek-harness/packages/llm/llm/src/types.ts
grep -n 'replay' deepseek-harness/packages/core/session/src/types.ts
```

1. **`packages/test-support/llm-replay/` (1344 dòng)** — đúng cái mình sắp làm, ở quy mô thật.
   Docstring của nó: *"Keyless snapshot-test LLM replay. It derives one model-call script per
   recorded session from v2 embedded Assistant streams and explicitly marked local compaction
   calls, then binds fresh live sessions to parent/child scripts by first-call order."*
   Dịch ý: từ **session log** dựng ra **kịch bản model call**, rồi gắn kịch bản đó vào một
   phiên mới. Chính xác là bài này, cộng thêm phần cha/con cho subagent.

2. **`ReplayEnvelope` trong `packages/llm/llm/src/types.ts:356`** — không phải test support, mà là
   type của package LLM **production**, và nó được import bởi
   `packages/core/agent-loop/src/assistant-stream.ts` — tức là bản thật của
   `StreamAccumulator` bên mình. Lý do: với model reasoning, provider trả về những
   signature không đọc được (`thinkingSignature`, `thoughtSignature`) mà **bắt buộc phải gửi
   lại nguyên văn** ở request sau, không thì API từ chối. Nên accumulator vừa gom text vừa
   giữ metadata provider-native để dựng lại assistant message về sau.
   → Đây là lý do thực dụng nhất cho câu "log ≠ message list": nếu log chỉ lưu đúng cái
   gửi lên dây, những signature này không có chỗ nào để sống.

3. **`packages/core/session/src/types.ts`** — `seed` của session nhận *"initial replay or fork
   history"*. Replay là một **lifecycle hạng nhất** của session, ngang với resume và fork.

Nói cách khác: `resume` mình đã viết là một nửa; replay là nửa còn lại của cùng một ý tưởng
"log là nguồn sự thật, mọi thứ khác chiếu ra từ nó".

## Đề bài

Tạo `mini_harness/llm/replay.py`:

```python
class ReplayLLM:
    """Phát lại assistant event từ một log cũ. Không cần API key."""

    def __init__(self, log_path: Path, on_text: OnText | None = None) -> None: ...

    async def generate(
        self, *, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> AssistantMessage: ...
```

Rồi thêm vào `PROVIDERS` trong `app.py` để chạy được:

```bash
python3 -m mini_harness --replay chat.jsonl "câu hỏi bị bỏ qua, nó phát lại log"
```

## Ràng buộc

1. **Không sửa một dòng nào trong `mini_harness/core/`.** Nếu thấy cần sửa thì dừng lại —
   đó là tín hiệu mô hình đang lệch, và chỗ lệch đó đáng giá hơn cả bài tập.
2. `run_turn` phải dùng nó **y như** `AzureLLM`, không có nhánh `if` nào cho replay.
3. `on_text` phải được gọi, để text hiện dần như streaming thật.
4. Hết kịch bản thì làm gì — **bạn quyết**, nhưng phải quyết có lý do.

## Tiêu chí xong

```bash
python3 -m pytest -q                      # 67 passed (60 cũ + 7 của bài này)
git diff --stat mini_harness/core/         # phải RỖNG
python3 -m mini_harness --replay <log>     # chạy thật, thấy text hiện dần
```

`tests/test_replay.py` đã viết sẵn. Hiện nó **skip** vì chưa có `replay.py`; tạo file là nó
tự chạy.

## Ba câu phải tự trả lời trước khi viết dòng đầu tiên

1. Log chứa **event**, không phải message. Bạn lọc ra cái gì, bỏ cái gì, và tại sao?
2. `tool_calls` trong log có shape của **log**, `AssistantMessage` cần shape của **core**.
   Hai shape đó khác nhau ở đâu? (mở `session.py:120-128` xem `to_messages` làm gì, rồi
   nhớ rằng bạn đi **ngược** chiều đó)
3. `generate` nhận `system`, `messages`, `tools`. Log **không lưu** system prompt.
   Vậy ba tham số đó bạn dùng cái nào, bỏ cái nào?

<details>
<summary>Gợi ý 1 — cấu trúc (mở khi đã trả lời được 3 câu trên)</summary>

`ReplayLLM` là một **con trỏ chạy trên một danh sách**, không hơn. Trong `__init__`: đọc log,
lọc, đổi shape, cất thành `list[AssistantMessage]`. Trong `generate`: lấy phần tử kế tiếp,
gọi `on_text`, trả về. `FakeLLM` ở `tests/fakes.py` là đúng khung này (`self._script.pop(0)`)
— khác duy nhất ở chỗ kịch bản đến từ file thay vì từ tham số. Đọc nó trước, đừng chép.

Hệ quả cần thấy: `messages` và `tools` **không được dùng**. Điều đó đúng và không sao —
protocol là hợp đồng chung, implementation có quyền không cần hết. Nhưng hãy viết một dòng
comment nói rõ vì sao bỏ, không thì người sau sẽ nghĩ là quên.
</details>

<details>
<summary>Gợi ý 2 — ba chỗ dễ sai</summary>

- Field trong log là `"arguments"` (string JSON thô), field của `ToolCall` là
  `arguments_json`. Không parse, cứ chuyền nguyên string — cùng lý do
  `StreamAccumulator.finish()` không parse.
- `AssistantMessage.tool_calls` là **`tuple`**, không phải `list`.
- Event `assistant` có thể có `"content": ""` (khi model chỉ gọi tool, không nói gì) và
  `"tool_calls": []` (khi nó chỉ trả lời). Cả hai đều hợp lệ, đừng lọc bỏ.
</details>

<details>
<summary>Gợi ý 3 — "hết kịch bản thì làm gì", và cái bẫy thật</summary>

Không có đáp án đúng duy nhất, có ba lựa chọn và mỗi cái nói một điều khác nhau:

- **Ném exception** — replay là để kiểm chứng, hết kịch bản nghĩa là phiên mới đi lệch
  phiên cũ, và đó là *thông tin cần biết ngay*, không phải chuyện im lặng cho qua.
  `FakeLLM` chọn cách này, kèm câu báo lỗi nói rõ đang ở request thứ mấy.
- **Trả về message rỗng không tool_calls** — turn kết thúc êm. Nhưng bạn vừa che mất
  chuyện phiên mới không khớp phiên cũ.
- **Lặp lại phần tử cuối** — đừng.

Còn cái bẫy mà harness thật đã đụng và ghi lại trong docstring:

> *"Throw and hang cases require an explicit override because a session log cannot
> reconstruct them alone."*

Log ghi những gì **đã xảy ra**. Một request từng lỗi timeout, hay từng treo, thì trong log
không để lại gì để phát lại — thiếu chính cái event mà nó không kịp sinh ra. Nên replay
thuần từ log **không bao giờ** dựng lại được đường lỗi; muốn test đường đó thì phải có file
override riêng. Đây là giới hạn của thiết kế, không phải thiếu sót của người viết — và nó
đáng nhớ hơn cả bài tập này.
</details>
