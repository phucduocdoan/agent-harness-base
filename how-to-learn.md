# Cách học agent harness

Về cách học, mình khuyên đừng đọc repo từ đầu đến cuối. Với background AI/Python của bạn, đi theo thứ tự này sẽ hiệu quả hơn.

> **Trước khi bắt đầu:** DeepSeek Harness là monorepo **TypeScript** (`packages/`, pnpm workspaces). `python/` chỉ là **SDK client** nói chuyện với harness qua JSON-RPC/stdio — không chứa agent loop. Nên phần đọc source ở dưới là đọc TS; phần tự implement thì viết Python thoải mái.

---

## 1. Chốt mental model trước

Trước khi đọc code, phải thuộc bức tranh:

| Thành phần | Vai trò |
| --- | --- |
| **Cordis** | runtime / environment |
| **DeepSeek Harness** | agent architecture chạy trong Cordis |
| **Agent Loop** | control flow |
| **Session** | state / history |
| **Tools** | capabilities |
| **LLM adapter** | model interface |
| **System Prompt** | context assembly |

Nếu ai hỏi:

> Cordis có phải agent framework không?

Bạn phải trả lời được ngay:

> Không hẳn. Cordis cung cấp plugin runtime, DI, services, events và lifecycle.
> DeepSeek tự xây agent semantics trên đó.

Đạt mức này là đủ, chưa cần đọc implementation Cordis sâu.

---

## 2. Đọc agent-loop trước Cordis

Đây là thứ mình nghĩ bạn nên làm ngay tiếp theo.

Trace đúng một flow:

```text
user input
   ↓
agent loop
   ↓
session
   ↓
system prompt
   ↓
LLM
   ↓
tool_calls?
   ↓
tools.execute()
   ↓
session append
   ↓
LLM tiếp
```

Khi đọc source, chỉ trả lời 5 câu:

1. Entry point của một turn ở đâu?
2. `while` loop ở đâu?
3. LLM được gọi ở đâu?
4. Tool call được detect/execute ở đâu?
5. Khi nào loop kết thúc?

Đừng quan tâm Cordis implementation trong bước này.

### File cần đọc

> ⚠️ **Không phải `python/sdk/src/deepseek_harness/client.py`.** File đó là JSON-RPC client qua stdio: nó `subprocess` spawn binary `dsh` rồi gửi/nhận message. Nó ở *ngoài* harness. Agent loop viết bằng TypeScript.

```
packages/core/agent-loop/src/
├── agent.ts            597 dòng  ← ReactLoopAgent: LOOP THẬT Ở ĐÂY
├── index.ts            925 dòng  ← AgentLoop (Cordis Service + AgentFactory)
├── tool-calls.ts       290 dòng  ← executeToolCalls()
├── assistant-stream.ts 140 dòng  ← 1 lần stream assistant
├── inbox.ts            247 dòng  ← steer / followup / inject
├── runtime-context.ts   76 dòng  ← loop lấy session/llm/tools từ ctx
└── invariant.ts         63 dòng
```

Mở `agent.ts` trước. Đừng mở `index.ts` — đó là phần Cordis wiring, để dành tới bước 5.

### Bản đồ trả lời 5 câu hỏi

| Câu hỏi | Vị trí |
| --- | --- |
| Entry point của một turn? | `send()` `agent.ts:124` → `kick()` `agent.ts:221` |
| `while` loop ở đâu? | 3 tầng: `agent.ts:223` (turn), `agent.ts:274` (step), `agent.ts:350` (request attempt) |
| LLM được gọi ở đâu? | `agent.ts:373` `llm.stream(request)`, consume ở `agent.ts:377` |
| Tool call detect/execute? | detect `agent.ts:469-470`, execute `agent.ts:472` → `tool-calls.ts:60` |
| Khi nào loop kết thúc? | `agent.ts:470`: `toolCalls.length === 0` → `{ kind: 'completed' }` |

### Thực tế phức tạp hơn sơ đồ trên

Sơ đồ `while True` ở trên là **mental model**, không phải code thật. DeepSeek có **3 vòng lặp lồng nhau**:

```text
turn      (agent.ts:223)   1 lượt hội thoại — user gửi gì đó, agent chạy tới khi hết việc
  └─ step (agent.ts:274)   1 model call + tool calls của nó
       └─ request attempt (agent.ts:350)   retry khi stream lỗi
```

Lý do tách 3 tầng: cancel, retry stream, và **inbox** (user chen ngang giữa turn qua `steer`/`followup`/`inject`). Đọc `agent.ts:257-290` để thấy ranh giới turn vs step.

---

## 3. Sau đó đọc Session

Sau Agent Loop, tìm hiểu: **Session lưu cái gì?**

Đặc biệt phân biệt:

- `message history`
- `session state`
- `session event`

Đây là phần rất quan trọng nếu sau này bạn muốn tự viết harness có:

- resume
- checkpoint
- trace
- replay
- debug

### File cần đọc

```
packages/core/session/src/
├── index.ts    1248 dòng  ← Session: append() / eventAt() / seq
├── types.ts     489 dòng  ← SessionEventMap: TẤT CẢ loại event
└── surface.ts   479 dòng  ← projection: event log → messages gửi cho LLM
```

Insight quan trọng: session **không** lưu `messages[]`. Nó là **append-only event log** (`session.append('user/message', ...)`, `append('tool/call', ...)`), và message list gửi cho LLM được **project ra** từ log đó (`surface.ts`). Đây là lý do resume/replay hoạt động được.

Nhóm plugin xử lý phần durable ở `packages/session/` (persistence-jsonl, projection, format migration...) — chưa cần đọc bây giờ.

---

## 4. Đọc Tool subsystem

Tiếp tục trace:

```text
LLM response
{
    tool_calls: [...]
}
      ↓
Tool Registry
      ↓
lookup("search")
      ↓
validate args
      ↓
execute()
      ↓
ToolResult
      ↓
Session
```

### File cần đọc

```
packages/core/tools/src/
├── index.ts       1936 dòng  ← Tools registry: register() / execute() / schemas()
├── schema.ts       617 dòng  ← defineTool() + DSL schema + validateArgs()
├── json-schema.ts  656 dòng  ← subset JSON Schema được hỗ trợ + validator
├── presentation.ts 389 dòng  ← card UI cho tool call
└── ptc.ts          678 dòng  ← PTC mode (model viết code gọi tool)
```

### Vòng lặp tool call chia làm 2 nửa

Đây là phần trả lời câu "làm sao agent hiểu và trả về đúng schema". Không có magic — chỉ là **JSON Schema đi ra, JSON đi vào, validate ở giữa**.

**Nửa đi ra (declare → model):**

```text
defineTool({ name, description, parameters })      schema.ts:545
        ↓  parameterSchemaSpecToJsonSchema()       schema.ts:449
   JSON Schema thật { type:'object', properties, required }
        ↓  ctx.tools.register(definition)          index.ts (registry)
   Tools registry
        ↓  systemPrompt.tools(provider)            system-prompt/index.ts:515
   assembly.tools: ToolSchema[]                    system-prompt/index.ts:614
        ↓  agent.ts:355 → buildRequest()           agent.ts:488
   request.tools (chỉ name/description/parameters)  index.ts:1246 schemaOf()
        ↓  serialize.ts:349
   wire: { type:'function', function:{ name, description, parameters } }
        ↓
   DeepSeek API
```

**Nửa đi vào (model → execute):**

```text
model trả tool_calls[].arguments  (JSON dạng STRING)
        ↓  parseArguments()                        tool-calls.ts:105
   unknown  (JSON hỏng thì giữ nguyên raw string, không throw)
        ↓  executeToolCalls()                      tool-calls.ts:60
   registry.execute()
        ↓  validate(args) trong defineTool         schema.ts:586
   violations.length > 0 ?
        ├─ CÓ  → throw ToolArgsError               schema.ts:587
        │         ↓ toolErrorResult()              index.ts:1860
        │    { isError: true, content: "Error: invalid arguments: ..." }
        │         ↓
        │    session.append('tool/result') → model ĐỌC ĐƯỢC lỗi và tự sửa
        └─ KHÔNG → userExecute(args, exec)         schema.ts:588
                  ↓ output.schema validate
             render(args, value) → ContentBlock[]
                  ↓
             session.append('tool/result')
```

### 3 điểm thiết kế đáng học

1. **`ToolSchema` chỉ có đúng 3 field** (`packages/llm/llm/src/types.ts:399`): `name`, `description`, `parameters`. `schemaOf()` (`index.ts:1246`) whitelist đúng 3 field này — `timeoutMs`, `isConcurrencySafe`, `presentCall` **không bao giờ** tới model. Model chỉ thấy đúng những gì nó cần.

2. **Args hỏng không phải là crash, mà là một message gửi lại cho model.** `parseArguments` bắt JSON lỗi và trả về raw string thay vì throw; string đó fail validate; `ToolArgsError` biến thành `tool/result` với `isError: true`, text = `Error: invalid arguments: "/todos" must be an array`. Model đọc câu đó ở lượt sau và tự sửa. **Đây chính là cơ chế "agent trả về đúng schema"** — không phải model thông minh sẵn, mà là loop feedback lỗi validate về cho model.

3. **Type-level inference.** `defineTool` dùng `InferArgs<S>` để `execute(args)` có type đúng từ chính cái `parameters` bạn khai. Một nguồn sự thật: khai schema một lần, ra cả JSON Schema cho model *và* TypeScript type cho code.

### Ví dụ thật: `todo_write`

`packages/todo/tool-todo/src/index.ts:146`

```ts
ctx.tools.register(defineTool({
  name: 'todo_write',
  description: describe(allowParallel),
  parameters: {
    todos: {
      type: 'array',
      required: true,                          // ← required là annotation per-property
      description: 'The COMPLETE task list, replacing any previous list.',
      items: {
        type: 'object',
        additionalProperties: false,
        properties: {
          content: { type: 'string', required: true, description: '...' },
          status: { type: 'string', required: true, enum: [...STATUSES], description: '...' },
        },
      },
    },
  },
  output: { schema: {...}, render(args, value) {...} },
  async execute(args, exec) { /* args.todos đã có type */ },
}))
```

Chú ý: root của `parameters` là **implicit open object** — bạn viết map property, `parameterSchemaSpecToJsonSchema` tự bọc thành `{ type: 'object', properties, required }`. `required: true` là annotation trên từng property chứ không phải array `required` như JSON Schema thuần.

### So với LangGraph

Đây là đoạn bạn có thể liên hệ rất nhiều với những agent bạn từng làm bằng LangGraph.

Khác biệt là thay vì:

```python
graph.add_node("tools", ToolNode(...))
```

DeepSeek có thể đơn giản là:

```python
while True:
    response = await llm(...)

    if response.tool_calls:
        await tools.execute(...)
```

Đây là insight đáng học nhất.

---

## 5. Sau đó mới quay lại Cordis

Lúc này bạn sẽ thấy một câu hỏi tự nhiên:

> Tại sao agent-loop lấy được session, tools, llm mà không import cứng?

Đó chính là lúc học Cordis. Chỉ cần học 5 thứ:

1. Context
2. Service
3. Plugin
4. Event
5. Dispose

Ví dụ:

```ts
ctx.llm
ctx.tools
ctx.sessions
```

và hiểu:

```text
Plugin A
   ↓ provides
Service X
   ↑ requires
Plugin B
```

Thế là đủ khoảng 80% giá trị Cordis.

### Chỗ xem DI thật trong repo

- `packages/core/agent-loop/src/index.ts:360` — `static inject = ['agents', 'sessions', 'llm', 'tools', 'systemPrompt', 'sessionProjections']`. Đúng một dòng này trả lời câu hỏi "tại sao không import cứng".
- `packages/core/agent-loop/src/runtime-context.ts` (76 dòng) — loop đọc service qua `ctx`.
- `docs/cordis-primer.md` — primer chính thức của repo.
- `vendor/` — source Cordis được vendor vào, pinned theo SHA.

Lưu ý convention của repo (`AGENTS.md`): `ctx.<name>` chỉ dùng cho service đã declare trong `inject`; service optional phải đọc bằng `ctx.get(name)`.

---

## 6. Tự viết một mini harness bằng Python

Đây mới là bước giúp bạn thực sự hiểu.

> Không LangChain. Không LangGraph.

**V1** chỉ cần:

```text
mini-harness/
├── session.py
├── llm.py
├── tools.py
├── agent_loop.py
└── main.py
```

Agent loop khoảng:

```python
while True:

    response = await llm.generate(
        session.messages,
        tools=tools.schemas()
    )

    session.append(response)

    if not response.tool_calls:
        return response

    for call in response.tool_calls:
        result = await tools.execute(call)

        session.append_tool_result(result)
```

Cho nó đúng 2 tools:

- `calculator`
- `get_current_time`

Nếu chạy được:

```text
User: "100 USD đổi sang VND rồi cộng thêm 10%"

LLM
 ↓
calculator
 ↓
result
 ↓
LLM
 ↓
final answer
```

thì bạn đã hiểu phần quan trọng nhất của agent harness.

---

## 7. Sau đó mới refactor nó giống Cordis

**V2:**

```text
Context
├── llm
├── tools
└── session
```

Thay:

```python
agent_loop(llm, tools, session)
```

bằng:

```python
agent_loop(ctx)
```

Rồi:

```python
ctx.provide("llm", llm)
ctx.provide("tools", tools)
ctx.provide("sessions", sessions)
```

---

## 8. Plugin hóa

**V3:**

```python
ctx.install(OpenAIPlugin())
ctx.install(ToolPlugin())
ctx.install(SessionPlugin())
```

Sau đó thử:

```python
ctx.install(VLLMPlugin())
```

mà không sửa `AgentLoop`.

Nếu làm được thì bạn đã hiểu vì sao DeepSeek dùng kiến trúc này.

---

## 9. Cuối cùng mới thêm Events

Ví dụ `AgentLoop` emit:

```text
agent.turn.start
llm.request
llm.response
tool.start
tool.end
agent.turn.end
```

Rồi viết:

- `LoggingPlugin`
- `TracingPlugin`
- `MetricsPlugin`

**Điều kiện:** không được sửa business logic của `AgentLoop` để thêm logging.

Ví dụ:

```python
ctx.emit("tool.start", call)
```

Logger chỉ subscribe:

```python
ctx.on("tool.start", logger)
```

Lúc này bạn sẽ thực sự thấy giá trị của Cordis.

---

## Thứ tự mình khuyên bạn học

### PHẦN QUAN TRỌNG NHẤT

| # | Chủ đề | Độ ưu tiên |
| --- | --- | --- |
| 1 | Agent Loop | `██████████` |
| 2 | Session | `████████` |
| 3 | Tools | `████████` |
| 4 | LLM abstraction | `███████` |
| 5 | Prompt assembly | `█████` |

### ARCHITECTURE

| # | Chủ đề | Độ ưu tiên |
| --- | --- | --- |
| 6 | Context / DI | `█████` |
| 7 | Plugin | `████` |
| 8 | Event | `███` |
| 9 | Dispose | `██` |

### ADVANCED

| # | Chủ đề |
| --- | --- |
| 10 | Persistence |
| 11 | Tracing |
| 12 | Config/plugin loader |
| 13 | API/UI |

---

## Đường đi nên theo

Đặc biệt đừng học theo thứ tự `Cordis → Cordis internals → Harness`. Bạn rất dễ rơi vào việc học framework nhưng không hiểu tại sao framework đó tồn tại.

Nên đi:

```text
DeepSeek Agent Loop
        ↓
"Ồ, nó cần LLM / Session / Tools"
        ↓
"Những dependency này được nối thế nào?"
        ↓
Cordis
        ↓
"Tại sao cần plugin/event/lifecycle?"
        ↓
Tự implement
```

---

## Bước tiếp theo

Nếu tiếp tục ngay từ đây, bước hợp lý nhất là mở `packages/core/agent-loop/src/agent.ts` và cùng trace từng function một theo một request thực tế. Sau đó ta có thể tự dựng mini DeepSeek Harness bằng Python khoảng 300–500 dòng, mình nghĩ đây sẽ là bài học giá trị nhất với mục tiêu AI Engineer của bạn.

Nếu muốn thấy loop chạy thật trước khi đọc code:

```sh
pnpm install
pnpm dsh --profile headless "task"    # cần DEEPSEEK_API_KEY
```

Nếu muốn vào từ phía Python SDK (thấy biên ngoài trước rồi mới vào loop):

1. `python/sdk/examples/minimal.py` — một request thực tế trông như thế nào
2. `python/sdk/src/deepseek_harness/client.py:38` `HarnessClient` — spawn `dsh` + JSON-RPC framing
3. rồi nhảy sang `packages/core/agent-loop/src/agent.ts` để xem request đó biến thành loop ra sao
