# DeepSeek Harness & Cordis — Study Notes

## 1. Bức tranh lớn

DeepSeek Harness không dùng LangGraph làm orchestration framework. Thay vào đó, nó tách kiến trúc thành hai lớp lớn:

- **Cordis**: runtime / composition environment
- **DeepSeek Harness core packages**: các thành phần tạo nên agent harness

Có thể hình dung:

```text
Cordis
└── cung cấp môi trường:
    ├── Context
    ├── Dependency Injection / Service Registry
    ├── Plugin lifecycle
    ├── Typed Events
    └── Disposable Effects / Cleanup

DeepSeek Harness
└── mount các subsystem vào Cordis:
    ├── Session
    ├── System Prompt
    ├── Tools
    ├── LLM adapters
    ├── Agent interface
    └── Agent Loop
```

Ý chính:

> Cordis không biết "agent", "LLM" hay "tool call" là gì.  
> DeepSeek định nghĩa các semantics đó và triển khai chúng dưới dạng plugin/service chạy trong Cordis.

---

## 2. Cordis thực sự là gì?

Cordis gần với một **plugin/application runtime** hơn là một agent framework.

Nó giải quyết các bài toán infrastructure:

1. Thành phần nào đang tồn tại?
2. Thành phần nào cung cấp service gì?
3. Plugin A tìm dependency B như thế nào?
4. Event được phát và subscribe ra sao?
5. Khi plugin bị unload thì listener/resource nào cần cleanup?
6. Làm sao thay implementation mà không sửa toàn hệ thống?

### 2.1. Context

`Context` là không gian chung nơi các plugin tương tác.

Conceptually:

```ts
ctx.sessions
ctx.tools
ctx.llm
ctx.agents
ctx.systemPrompt
```

Không nên hiểu đây chỉ là một object chứa global variables.

Nó giống:

- service container
- dependency injection context
- plugin scope
- event environment

---

## 3. Service

Plugin có thể publish một service vào context.

Ví dụ concept:

```text
Tool plugin
    ↓ exposes
ctx.tools

Session plugin
    ↓ exposes
ctx.sessions
```

Consumer phụ thuộc vào **service contract**, thay vì import trực tiếp implementation.

Thay vì:

```python
from my_project.openai_client import OpenAIClient
```

ta muốn logic kiểu:

```python
llm = ctx.llm
```

Điều này giúp:

- swap provider
- mock khi test
- plugin hóa implementation
- giảm coupling

---

## 4. Plugin

Trong DeepSeek Harness, tư duy quan trọng là:

> Everything is a plugin.

Các subsystem quan trọng cũng được biểu diễn như plugin:

```text
SessionPlugin
ToolPlugin
LLMPlugin
SystemPromptPlugin
AgentPlugin
AgentLoopPlugin
```

Không có một class `DeepSeekHarnessCore` khổng lồ giữ toàn bộ logic.

Thay vào đó:

```text
Cordis Context
├── plugin A → cung cấp sessions
├── plugin B → cung cấp tools
├── plugin C → cung cấp llm
├── plugin D → cung cấp system prompt
└── plugin E → agent loop dùng các service trên
```

---

## 5. Event

Event dùng để giảm coupling giữa các plugin.

Ví dụ:

```text
agent starts turn
    ↓
event: agent/turn/start
    ↓
logger plugin
metrics plugin
trace plugin
UI plugin
```

Agent loop không cần biết có bao nhiêu consumer.

Producer chỉ emit event.

---

## 6. Effect / Dispose / Cleanup

Đây là một điểm rất đáng học từ Cordis.

Khi plugin đăng ký:

- event listener
- service
- callback
- timer
- resource

thì hệ thống cần biết cách gỡ nó khi plugin unload.

Concept:

```text
mount plugin
    ↓
register effects
    ↓
plugin active
    ↓
unmount
    ↓
dispose effects
```

Điều này tránh:

- listener leak
- duplicate registration
- stale state
- test pollution

---

# 7. DeepSeek Harness thêm gì lên trên Cordis?

Cordis chỉ là môi trường.

DeepSeek Harness thêm các domain primitives của agent:

```text
Session
Agent
Agent Loop
LLM
Tools
System Prompt
```

Đây mới là phần biến runtime thành một **agent harness**.

---

## 8. Session

Session chịu trách nhiệm cho state/history của cuộc hội thoại hoặc agent run.

Concept:

```text
Session
├── messages
├── tool calls
├── tool results
├── turn state
└── events/history
```

Một thiết kế tốt không chỉ coi session là `list[Message]`.

Nó nên là một abstraction đủ ổn định để:

- append events
- reconstruct state
- persist
- resume
- inspect
- debug

---

## 9. System Prompt Assembly

System prompt không nhất thiết là một string hard-code.

Nó có thể được compose từ nhiều nguồn:

```text
base instructions
+ environment context
+ tool descriptions
+ agent-specific instructions
+ runtime state
```

Điều này làm prompt trở thành một subsystem riêng.

---

## 10. LLM Adapter

LLM adapter cung cấp interface thống nhất cho model backend.

Ví dụ:

```python
class LLM:
    async def generate(self, messages, tools=None):
        ...
```

Các implementation:

```text
OpenAI
Anthropic
DeepSeek
local vLLM
mock LLM
```

Agent loop không cần biết provider thực tế.

---

## 11. Tool Registry

Tool subsystem thường cần:

```text
register tool
lookup tool
validate arguments
execute tool
return structured result
handle errors
```

Concept:

```python
ctx.tools.register("search", search_tool)
ctx.tools.register("read_file", read_file_tool)
```

Agent loop chỉ cần:

```text
tool_call
    ↓
tool registry
    ↓
execute
    ↓
tool result
```

---

# 12. Agent Loop — bộ điều khiển thực sự

Nếu Cordis là "môi trường", thì **Agent Loop là control loop**.

Một turn cơ bản:

```text
User Input
    ↓
Session.openTurn()
    ↓
Build System Prompt
    ↓
Build model messages
    ↓
LLM.generate()
    ↓
Tool Calls?
   / \
 no   yes
 ↓     ↓
End   execute tools
        ↓
     append results
        ↓
       LLM again
```

Pseudo-code:

```python
async def run_turn(ctx, session, user_message):
    session.append_user(user_message)

    while True:
        prompt = ctx.system_prompt.build(session)

        response = await ctx.llm.generate(
            messages=session.messages,
            system_prompt=prompt,
            tools=ctx.tools.schemas(),
        )

        session.append_assistant(response)

        if not response.tool_calls:
            return response

        for call in response.tool_calls:
            result = await ctx.tools.execute(call)
            session.append_tool_result(result)
```

Đây là trái tim của harness.

---

# 13. Cordis vs LangGraph

## LangGraph

Trọng tâm:

```text
State
Node
Edge
Graph
Checkpoint
```

Flow được model hóa explicit dưới dạng graph.

Ví dụ:

```text
START
  ↓
LLM
  ↓
has tool?
 /     \
yes     no
 ↓       ↓
tool    END
 ↓
LLM
```

Ưu điểm:

- workflow rõ
- branching dễ quan sát
- checkpoint/resume mạnh
- phù hợp orchestration phức tạp

---

## Cordis + DeepSeek Harness

Cordis không quyết định agent flow.

```text
Cordis
    ↓
provides runtime primitives

DeepSeek AgentLoop
    ↓
implements control flow bằng code
```

Không cần graph.

Control flow nằm trực tiếp trong:

```python
while True:
    ...
```

### So sánh nhanh

| LangGraph | Cordis + DeepSeek Harness |
|---|---|
| Graph orchestration framework | Plugin/runtime architecture |
| Node + edge | Service + plugin |
| Graph quyết định flow | Agent loop code quyết định flow |
| State thường đi qua graph | Session subsystem |
| Checkpoint là first-class | Persistence do subsystem thiết kế |
| Agent là workflow | Agent là tập services + control loop |

---

# 14. Cách hiểu đúng về "Core"

Không nên hình dung:

```text
Cordis
└── DeepSeekCore
    ├── llm
    ├── tools
    └── session
```

Tốt hơn là:

```text
Cordis Context
├── Session Plugin
├── Tool Plugin
├── LLM Plugin
├── Prompt Plugin
├── Agent Plugin
└── Agent Loop Plugin
```

DeepSeek thiết kế **product spine**, nhưng các thành phần của spine vẫn là plugin.

Điều này tạo khả năng replace:

```text
Default AgentLoop
        ↓ replace
Custom AgentLoop

Default LLM
        ↓ replace
Local vLLM

Default Session Store
        ↓ replace
PostgreSQL Session Store
```

---

# 15. Insight quan trọng nhất

Nếu muốn tự xây một agent harness Python, không cần clone Cordis đầy đủ ngay.

Một runtime tối thiểu chỉ cần:

```text
Context
ServiceRegistry
Plugin
EventBus
Disposable
```

Sau đó tập trung vào phần agent:

```text
Session
ToolRegistry
LLM interface
Prompt assembly
AgentLoop
```

Đây là phần 80/20.

---

# 16. Minimal Python Harness Architecture

Một cấu trúc repo có thể bắt đầu:

```text
harness/
├── runtime/
│   ├── context.py
│   ├── service.py
│   ├── plugin.py
│   ├── events.py
│   └── disposable.py
│
├── core/
│   ├── session.py
│   ├── llm.py
│   ├── tools.py
│   ├── prompt.py
│   ├── agent.py
│   └── agent_loop.py
│
├── plugins/
│   ├── openai_llm.py
│   ├── local_vllm.py
│   ├── file_tools.py
│   └── logging.py
│
└── app.py
```

---

# 17. Bản tối thiểu nên code

## Context

```python
class Context:
    def __init__(self):
        self.services = {}

    def provide(self, name, service):
        self.services[name] = service

    def get(self, name):
        return self.services[name]
```

---

## Plugin

```python
class Plugin:
    def setup(self, ctx):
        raise NotImplementedError

    def dispose(self):
        pass
```

---

## Tool Registry

```python
class ToolRegistry:
    def __init__(self):
        self.tools = {}

    def register(self, name, fn):
        self.tools[name] = fn

    async def execute(self, name, **kwargs):
        return await self.tools[name](**kwargs)
```

---

## LLM Interface

```python
from typing import Protocol

class LLM(Protocol):
    async def generate(self, messages, tools=None):
        ...
```

---

## Session

```python
class Session:
    def __init__(self):
        self.events = []

    def append(self, event):
        self.events.append(event)
```

---

## Agent Loop

```python
async def run_agent(ctx, session, user_input):
    session.append({
        "type": "user",
        "content": user_input,
    })

    while True:
        response = await ctx.get("llm").generate(
            session.events,
            tools=ctx.get("tools"),
        )

        session.append(response)

        if not response.get("tool_calls"):
            return response

        for call in response["tool_calls"]:
            result = await ctx.get("tools").execute(
                call["name"],
                **call["arguments"],
            )

            session.append({
                "type": "tool_result",
                "tool": call["name"],
                "result": result,
            })
```

---

# 18. Learning Roadmap — 80/20

## Phase 1 — Hiểu architecture

Mục tiêu:

> Giải thích được DeepSeek Harness mà không nhìn source.

Phải trả lời được:

1. Cordis giải quyết vấn đề gì?
2. Context là gì?
3. Service khác Plugin như thế nào?
4. Event dùng để giảm coupling ra sao?
5. Dispose giải quyết leak thế nào?
6. Agent Loop làm gì?
7. Session giữ cái gì?
8. Tool Registry hoạt động ra sao?
9. LLM adapter giúp swap model thế nào?
10. Vì sao DeepSeek không cần LangGraph?

---

## Phase 2 — Trace một request

Chọn một user message và trace:

```text
HTTP / CLI / UI
    ↓
Agent
    ↓
Agent Loop
    ↓
Session
    ↓
Prompt
    ↓
LLM
    ↓
Tool
    ↓
Session
    ↓
LLM
    ↓
Response
```

Không cần đọc toàn repo.

Chỉ trace call stack của một request.

---

## Phase 3 — Tự code mini harness

Không dùng LangChain/LangGraph.

Yêu cầu:

```text
User Input
    ↓
LLM
    ↓
Tool Call
    ↓
Tool Execution
    ↓
LLM
    ↓
Final Answer
```

Implement:

- Session
- Tool Registry
- LLM abstraction
- Agent Loop

Sau khi chạy được mới thêm plugin runtime.

---

## Phase 4 — Plugin hóa

Refactor mini harness:

```text
OpenAILLM
LocalLLM
ToolRegistry
Logger
SessionStore
```

thành plugins.

Mục tiêu:

```python
ctx.install(OpenAIPlugin())
ctx.install(ToolPlugin())
ctx.install(LoggerPlugin())
```

Sau đó thử swap:

```python
ctx.install(LocalVLLMPlugin())
```

mà AgentLoop không đổi.

---

## Phase 5 — Event + Dispose

Thêm:

```text
agent/start
agent/end
llm/request
llm/response
tool/start
tool/end
```

Sau đó viết:

```text
LoggingPlugin
MetricsPlugin
TracingPlugin
```

không sửa AgentLoop.

Đây là lúc bạn thực sự cảm nhận được lợi ích của architecture.

---

# 19. Bài tập nên làm

## Exercise 1

Viết:

```python
Context.provide()
Context.get()
```

và inject:

```text
LLM
Tools
Session
```

---

## Exercise 2

Viết agent loop hỗ trợ đúng một tool:

```text
calculator
```

---

## Exercise 3

Cho phép register nhiều tools:

```text
calculator
weather
search
```

---

## Exercise 4

Swap:

```text
FakeLLM
→ OpenAI
→ local vLLM
```

Agent loop không đổi.

---

## Exercise 5

Thêm LoggingPlugin.

Không được thêm `print()` trực tiếp vào AgentLoop.

Dùng event.

---

## Exercise 6

Thêm persistence:

```text
InMemorySessionStore
SQLiteSessionStore
```

và swap qua interface.

---

# 20. Checklist: Khi nào đã thực sự hiểu?

Bạn đã hiểu Cordis/Harness khi có thể tự giải thích:

```text
Why Context?
Why Service?
Why Plugin?
Why Event?
Why Dispose?
Why AgentLoop?
Why Session?
```

và quan trọng hơn:

> Nếu bỏ Cordis đi, bạn vẫn tự dựng được agent harness.

Cordis là kiến trúc để harness modular hơn.

Nó không phải thứ tạo ra agent intelligence.

---

# 21. Mental Model cuối cùng

```text
                    ┌─────────────────┐
                    │     Cordis      │
                    │ Runtime / DI    │
                    │ Event / Plugin  │
                    └────────┬────────┘
                             │
           ┌─────────────────┼─────────────────┐
           ↓                 ↓                 ↓
       Session            Tools              LLM
           │                 │                 │
           └──────────┬──────┴─────────────────┘
                      ↓
                 Agent Loop
                      ↓
              Agent Behaviour
```

Một câu để nhớ:

> **Cordis tạo môi trường. DeepSeek Harness định nghĩa agent primitives. Agent Loop điều khiển hành vi.**

---

# 22. Nếu tự xây từ đầu: thứ tự tốt nhất

```text
1. Agent Loop
2. Session
3. Tool Registry
4. LLM abstraction
5. Prompt assembly
6. Context / DI
7. Plugin lifecycle
8. Events
9. Dispose
10. Persistence / tracing / metrics
```

Đừng bắt đầu bằng việc clone toàn bộ Cordis.

Hãy làm agent chạy được trước.

Sau đó mới refactor thành architecture giống DeepSeek Harness.
