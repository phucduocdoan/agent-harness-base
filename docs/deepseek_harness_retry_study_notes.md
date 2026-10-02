# Deepseek-harness: retry/lỗi của model call hoạt động thế nào

File này trả lời 6 câu hỏi về cơ chế retry và xử lý lỗi stream của
`deepseek-harness`, đọc từ commit `e73906d06f1b62675fd6810d4397ae47217dd51f`.
Mục tiêu là đối chiếu với `mini_harness` (bản Python đang dựng lại), không mô
tả toàn bộ package.

## 1. Retry nằm ở tầng nào?

Retry nằm trong package `packages/llm/llm-retry` — một **function plugin**
(không phải middleware bọc quanh stream, không nằm trong provider). Nó đăng
ký một listener trên waterfall `agent/request-error` của agent loop:

```
packages/llm/llm-retry/src/index.ts:243-252
const disposeListener = ctx.on('agent/request-error', (payload, next) => {
  if (lifetime.signal.aborted) return Promise.resolve(undefined)
  return track(recover(payload, next))
})
```

`agent/request-error` được agent loop phát ra ở **mức step đã kết thúc**
(step hoàn tất vòng lặp đọc chunk, không phải mức từng chunk):
`packages/core/agent-loop/src/agent.ts:432`. Policy (`retryPolicy`) không nằm
trong `llm-retry` — nó được khai báo trên từng provider adapter (vd.
`llm-deepseek`), `llm-retry` chỉ là bộ **thực thi** policy đó
(`packages/llm/llm-retry/README.md`, mục "Use this package": "the retry
policy itself lives on each provider adapter's configuration, and this
package has no configuration of its own").

Quan trọng: nó **không bọc** `ctx.llm.stream()`. README nói thẳng: "It does
not wrap the streaming call itself... direct `ctx.llm.stream()` consumers
stay single-attempt." Retry chỉ có tác dụng khi gọi qua agent loop (nơi có
waterfall `agent/request-error`); gọi thẳng `ctx.llm.stream()` thì không có
retry nào cả, một lần là một lần.

## 2. Có phân biệt lỗi TRƯỚC và SAU token đầu tiên không?

**Không phân biệt theo "đã phát token hay chưa" để quyết định có retry hay
không.** Retry được quyết định dựa trên **loại lỗi** (terminal finish chunk
`kind: 'error'`/`'aborted'`) chứ không dựa vào việc đã có chunk nào được đẩy
ra UI hay chưa. Code tại `packages/core/agent-loop/src/agent.ts:354-440`:

```
packages/core/agent-loop/src/agent.ts:354-362
live.start()
started = true
for await (const chunk of stream) {
  signal.throwIfAborted()
  live.push(chunk)      // mỗi chunk vẫn được emit ra UI qua agent/assistant-stream
}
```

Vòng `for await` chạy hết (không throw) ngay cả khi provider lỗi giữa
chừng, vì adapter tự bắt lỗi và **gói nó thành một "finish chunk" cuối
cùng** (`type:'finish', reason:{kind:'error', failure}`) — xem
`packages/llm/llm/src/index.ts:1109-1115` (`adapterFailureChunk`) và
comment tại `packages/llm/llm/src/index.ts` dòng ngay trước `stream()`:
"Adapter selection, dispatch, and iteration failures become terminal
`error` or `aborted` finish chunks; middleware, nested-call, cleanup, and
consumer failures remain thrown." Sau khi vòng lặp kết thúc,
`agent.ts:424-440` kiểm tra `live.finish.kind === 'error' || 'aborted'` rồi
mới gọi waterfall `agent/request-error` để xin quyết định retry — **bất kể
trước đó đã push được bao nhiêu chunk**.

Vậy **không có khái niệm `hasStarted`/`firstChunk`/`emitted` dùng để chặn
retry**. Biến gần giống nhất là `started` (`agent.ts:349, 364, 367`), nhưng
nó chỉ dùng để quyết định có **ghi durable** attempt hay không khi
`stream()` chính nó *throw* (lỗi hạ tầng thật sự, không phải finish chunk):
nếu `!started` thì ném thẳng lỗi lên trên, không có retry, không ghi gì
(dòng `agent.ts:367`: `if (!started) throw error`). Trường hợp này hiếm vì
adapter đã cố gắng không throw mà đóng gói thành finish chunk.

**Xử lý text đã hiển thị khi retry:** không "nối tiếp", không "gửi lại
đúng phần đã mất" — toàn bộ attempt cũ bị coi là hỏng, request được **gửi
lại từ đầu** (y nguyên lịch sử durable, không có phần output lỗi). Cơ chế
để UI biết "attempt cũ đã chết, có attempt mới" là **framing theo attempt**:
mỗi `AssistantStreamAttempt` mới có `attemptId` riêng, phát một frame
`type:'start'` mở đầu và một frame `type:'end'` kết thúc với
`outcome.kind` là `'committed'` (ghi vào `assistant/attempt`, không phải
`assistant/message`) hoặc `'abandoned'`
(`packages/core/agent-loop/src/assistant-stream.ts:49-109`). Khi retry xảy
ra, `step()` tạo `live` mới (`agent.ts` trong vòng `while(true)`, biến
`live = new AssistantStreamAttempt(...)`) và gọi lại toàn bộ
`buildRequest` → `stream()` từ đầu. Vì event `assistant/attempt` không nằm
trong surface history (`deriveMessages()`), request retry được build lại y
hệt request gốc, không chứa phần output cũ. README nói thẳng: "failed
chunks never enter derived messages."

## 3. Lỗi nào được thử lại, lỗi nào không?

Mặc định (mode `normal`, khi provider không khai `retryPolicy`):
`packages/llm/llm/src/retry-policy.ts:14-24`

```
packages/llm/llm/src/retry-policy.ts:14-24
const DEFAULT_MAX_RETRIES = 5
const DEFAULT_INITIAL_DELAY_MS = 500
const DEFAULT_MAX_DELAY_MS = 10_000
const DEFAULT_JITTER_RATIO = 0.1
const DEFAULT_RETRYABLE_CODES = Object.freeze([
  EMPTY_RESPONSE_CODE,  // 'EMPTY_RESPONSE'
  'RATE_LIMIT',
  'SERVER',
  'TIMEOUT',
  'TRANSPORT',
])
```

Các lỗi **không** nằm trong danh sách trên (vd. auth, quota, invalid
request, protocol, context-overflow) thì ở mode `normal` sẽ **không** được
retry, đi thẳng `next()` (ủy xuống handler sau trong waterfall, cuối cùng
thành lỗi thật) — `packages/llm/llm-retry/src/index.ts:215-217`:
`else if (!policy.retryableCodes.includes(failure.code)) return next()`.
Mode `always` (phải bật tường minh qua config) thì retry **mọi** lỗi,
không giới hạn số lần, kể cả các lỗi "vĩnh viễn" kể trên — README liệt kê
rõ trong mục "Known Limitations": "Always mode retries permanent failures —
authentication, quota, invalid-request, protocol, and unrecoverable context
errors continue until success, cancellation, or disposal."

**`Retry-After`:** có đọc, qua field `failure.providerRetryAfterMs`
(`packages/llm/llm-retry/src/index.ts:226-238`). Nếu giá trị hợp lệ
(`Number.isFinite`, `> 0`) và `<= policy.maxDelayMs` thì dùng thẳng giá trị
provider; nếu vượt `maxDelayMs` thì ở mode `normal` **bỏ cuộc** (`next()`,
không retry), ở mode `always` thì rơi về backoff cục bộ thay vì tuân theo
delay quá lớn của provider — đúng như README: "an over-cap provider delay
uses the configured local backoff so the policy cannot terminate on that
instruction."

**Backoff:** luỹ thừa có chặn trên, cộng jitter đối xứng quanh giá trị đó,
không phải hằng số:

```
packages/llm/llm-retry/src/index.ts:59-64
function localDelay(config, retry, random) {
  const exponent = Math.min(retry - 1, 1024)
  const exponential = Math.min(config.initialDelayMs * 2 ** exponent, config.maxDelayMs)
  const jitter = 1 - config.jitterRatio + 2 * config.jitterRatio * random()
  return Math.min(exponential * jitter, config.maxDelayMs)
}
```

Mặc định: bắt đầu 500ms, trần 10s, jitter ±10%, tối đa 5 lần thử lại
(mode `normal`).

## 4. Text dở có được ghi vào session không?

Có, nhưng **phân biệt rõ 2 trường hợp theo nguyên nhân dừng** —
`packages/core/agent-loop/src/agent.ts:373-411`:

- **Bị abort (hủy, không phải lỗi provider):** nếu còn nội dung hiển thị
  được (`live.interruptedBlocks()` không rỗng), nó được ghi thành một
  **`assistant/message` thật sự** (có mặt trong lịch sử, model thấy được ở
  turn sau) với cờ `interrupted: true`:
  `agent.ts:378-389` — `this.session.append('assistant/message', {..., interrupted: true, ...})`.
  Nếu không còn gì hiển thị được thì ghi `assistant/attempt` (không surface).
- **Lỗi provider (không phải abort), dù đã phát bao nhiêu chunk:** luôn
  ghi `assistant/attempt` (`agent.ts:424-427`), **không bao giờ** thành
  `assistant/message`. Đây là event non-surface — không nằm trong
  `deriveMessages()`, chỉ tồn tại để lịch sử nhất quán và để
  `llm-retry` tính `policyKey`/đếm số lần retry qua
  `sessionProjections`.

**Tool-call dở (JSON cụt) có bị vứt không — có.** `interruptedBlocks()`
chỉ giữ lại block loại `text`/`reasoning`, loại bỏ hẳn `tool-call`:

```
packages/llm/llm/src/assembler.ts:169-178
interruptedBlocks(): ContentBlock[] {
  return this.order
    .map((index) => {
      const partial = this.mustGet(index)
      const type = partial.block?.type ?? partial.blockType
      if (type !== 'text' && type !== 'reasoning') return undefined
      return this.assemble(partial, index)
    })
    .filter((block) => (block?.type === 'text' || block?.type === 'reasoning') && block.text.trim() !== '')
}
```

Tức là một tool-call đang được stream dở dang (arguments JSON chưa đóng)
sẽ không bao giờ lọt vào message bị interrupt — nó bị âm thầm bỏ qua
("vứt"), không có nỗ lực sửa/đóng JSON cụt.

`appendSkippedToolCall`
(`packages/core/agent-loop/src/tool-calls.ts:250-260`) là một cơ chế
**khác, không liên quan** đến JSON cụt do stream lỗi. Nó chỉ chạy khi một
tool-call đã **parse xong hoàn chỉnh** (đã nằm trong một `assistant/message`
đã commit) nhưng bị abort **trong lúc thực thi tool** (trước khi tool kịp
chạy) — nó ghi một cặp `tool/call` + `tool/result` giả với lỗi
`tool call aborted before dispatch` để việc replay/ghép call-result không
bị lệch. Đây là tầng tool-execution, không phải tầng LLM-stream-parsing.

## 5. Thông báo "đang thử lại" đi đường nào ra giao diện?

Đi **kênh sự kiện session log riêng**, không chung với kênh chunk/text của
model. `llm-retry` ghi hai loại durable session event: `llm/retry` (trước
khi chờ) và `llm/retry-started` (ngay trước khi gửi lại request) —
`packages/llm/llm-retry/src/index.ts:188,190`. Hai event này **không** đi
qua `agent/assistant-stream` (kênh live-frame cho text/chunk;
`packages/core/agent-loop/src/agent.ts:356`: `this.dispatch.emit('agent/assistant-stream', { frame })`).

Ở phía UI, `packages/client/ui-chat/src/client/conversation-nodes/retry.ts`
đăng ký một **conversation-node riêng biệt** tên `model-retry`
(`retry.ts:41-89`), lắng nghe đúng hai loại event `llm/retry` /
`llm/retry-started`, dựng ra chuỗi `attempts` với trạng thái
`scheduled` → `started` → (hoặc `cancelled` nếu step/turn đã đóng trước
khi kịp chạy, hàm `isClosed` dòng 35-38). README của `llm-retry` xác nhận
lại: "No retry event, delay, provider error, or failed partial output is
model-visible" — tức là kênh `llm/retry` chỉ dành cho UI người dùng xem
("đang thử lại lần N..."), hoàn toàn không lọt vào ngữ cảnh gửi cho model.

## 6. Có gì mà bản Python chưa nghĩ tới không?

- **Khóa trạng thái theo policy, không chỉ theo provider.** Số lần đã
  retry được tính theo cặp `(provider, policyKey)` chứ không chỉ theo
  provider (`packages/llm/llm-retry/src/index.ts:66-81`,
  `retryPolicyKey`/`retryStateKey`). Nếu provider đổi retryPolicy (đổi số
  lần tối đa, đổi danh sách mã lỗi, đổi backoff) giữa chừng, bộ đếm bắt
  đầu lại từ 0 cho policy mới — tránh một policy mới "thừa kế" nhầm ngân
  sách retry của policy cũ.
- **Durable-before-wait, chống crash giữa chừng retry.** Event `llm/retry`
  được ghi vào session log **trước khi** timer backoff bắt đầu chờ, không
  phải sau. Nếu tiến trình crash giữa lúc đang chờ backoff, log vẫn nhất
  quán — khi replay sẽ biết đã có một retry được lên lịch. Đây là một
  nguyên tắc thiết kế tường minh ("durable before wait") ghi hẳn trong
  README, không chỉ là chi tiết cài đặt tình cờ.
- **Có "invariant checker" riêng cho retry.** Package có
  `src/invariant.ts` (được publish riêng là `./invariant`) để **validate**
  mỗi retry đã lên lịch so với session log: đúng turn/step đang mở, đúng
  provider của request lỗi, và mỗi `llm/retry-started` phải khớp đúng một
  `llm/retry` trước đó cùng `retryId`/turn/step/số-lần-retry
  (README, mục Dev Note). Đây là lớp tự kiểm tra tính nhất quán, không
  phải cơ chế retry chính nhưng đáng học nếu muốn làm test cho mini_harness.
- **Hủy giữa chừng backoff là cancellable, không phải "chờ hết rồi mới
  check abort".** `cancellableDelay` dùng `AbortSignal` đua với
  `setTimeout`, trả về `false` ngay khi bị abort
  (`packages/llm/llm-retry/src/index.ts:83-96`). Khi plugin bị dispose,
  nó abort một `lifetime` controller riêng và **chờ drain** hết các
  retry đang active trước khi coi là dispose xong
  (`index.ts:254-258`, `Promise.allSettled([...active])`) — tránh một
  retry "mồ côi" chạy tiếp sau khi plugin đã gỡ.
- **Mode `always` nhường quyền cho downstream trước.** Ở mode `always`,
  `llm-retry` không tự ý retry ngay — nó gọi `next()` (các policy đứng sau
  trong waterfall) trước, chỉ áp dụng backoff của chính nó nếu downstream
  không quyết định retry (`index.ts:194-217`). Đây là một mô hình
  waterfall nhiều lớp chồng chính sách retry mà bản Python (hiện chỉ có
  một `generate()` không có lớp policy) chưa có khái niệm tương đương.
- Không thấy trong source: circuit breaker, giới hạn token/phút phía
  client, chuyển sang model dự phòng (fallback model) khi hết retry, dedupe
  request, hay resume stream (tiếp tục đúng từ chunk bị mất) — đã tìm bằng
  `rtk proxy rg` các từ khóa `circuit`, `fallback`, `dedupe`, `resume` trong
  `packages/llm/llm-retry` và `packages/core/agent-loop` nhưng không có
  kết quả liên quan đến retry. Nếu các cơ chế này tồn tại thì không nằm ở
  hai package được giao đọc.

## Đối chiếu với bản Python

| Harness thật làm gì | `mini_harness` hiện tại | Chênh ở đâu |
|---|---|---|
| Retry là một listener trên waterfall `agent/request-error`, chạy ở mức step đã kết thúc, tách khỏi tầng stream/gộp chunk | `core/loop.py` không có điểm mở rộng lỗi nào; `llm/stream.py` gộp chunk xong là xong, không ai bắt lỗi provider để quyết định retry | Chưa có lớp policy/extension point riêng cho lỗi request — generate() hỏng là hỏng luôn |
| Lỗi giữa chừng được adapter gói thành "finish chunk" (`kind:'error'`) thay vì throw giữa `for await`, nên vòng lặp đọc chunk luôn kết thúc "sạch" rồi mới xét retry | Lỗi giữa chừng stream rất có thể throw thẳng ra khỏi `for` trong `stream.py`, cắt ngang `StreamAccumulator` | Cần quyết định: để provider-wrapper tự bắt lỗi và trả "chunk kết thúc lỗi", hay để loop bắt exception — ảnh hưởng trực tiếp đến việc có retry được hay không |
| Dù đã hiển thị text ra màn hình, status quyết định retry vẫn dựa vào loại lỗi, không dựa vào "đã emit chưa"; khi retry thì gửi lại request từ đầu, có framing attempt (`start`/`end`) để UI biết attempt cũ đã bỏ | `core/loop.py` chỉ `append` sau khi `generate()` xong — không có khái niệm "attempt", không có cách nào nói với người dùng "attempt này hỏng, bắt đầu attempt mới" | Thiếu một lớp "attempt" tách biệt khỏi message thật để UI/log phân biệt được |
| Text dở do abort (không phải lỗi provider) vẫn được ghi thành message thật, có cờ `interrupted: true`; text dở do lỗi provider thì ghi non-surface, không vào context lần sau | Không ghi gì cả khi stream vỡ giữa chừng — đúng như mô tả trong yêu cầu | Thiếu cả hai nhánh: (1) ghi lại phần dở khi hủy có chủ đích, (2) ghi non-surface khi lỗi để debug/đếm retry |
| Tool-call dở dang (JSON cụt) bị lọc bỏ hoàn toàn khỏi phần nội dung interrupted, không cố sửa/đóng JSON | Chưa có pha tương ứng (do chưa ghi message dở) | Khi thêm "ghi message dở", phải nhớ loại trừ tool-call đang mở, không đưa JSON cụt vào context |
| Thông báo "đang thử lại" đi qua event log riêng (`llm/retry`/`llm/retry-started`), UI có conversation-node riêng, tách hẳn khỏi kênh chunk | Không có khái niệm retry nên cũng không có kênh thông báo | Nếu thêm retry, nên tách kênh thông báo khỏi `on_text`, đừng lẫn vào text model |
