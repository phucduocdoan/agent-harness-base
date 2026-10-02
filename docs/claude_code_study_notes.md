# Claude Code (anthropics/claude-code) — Study Notes

## 0. Repo này chứa gì và KHÔNG chứa gì

Đã tự kiểm chứng trước khi viết bất cứ dòng nào dưới đây: `claude-code/` **không
có** `package.json`, không có `src/`, không có thư mục nào chứa implementation
thật của CLI. `find claude-code -maxdepth 2 -type d` chỉ ra: `.claude/`,
`.claude-plugin/`, `.devcontainer/`, `examples/`, `.github/`, `mods/`,
`plugins/`, `Script/`, `scripts/`. Claude Code (binary thật) ship dưới dạng
bundle đã minify, **không nằm trong repo public này**.

Thứ thật sự đọc được, và là nguồn của toàn bộ ghi chú này:

- `CHANGELOG.md` (~867KB, 8082 dòng, 409 version từ 0.2.21 đến 2.1.287) —
  nhật ký tính năng theo version, nguồn chính cho Q1 và phần lớn Q3.
- `mods/` — 5 "Claude Mods" (`agents-md`, `diff`, `sec-default`, `telemetry`,
  `types`), mỗi cái là plugin TypeScript thật, có `hooks/register.ts` gọi
  `on(eventName, matcher, handler)` vào một event bus nội bộ, cộng một file
  type duy nhất `mods/types/claude-code.d.ts` (13186 dòng) khai báo
  `EngineInterface`, `On`, các event payload. Đây là **seam sâu nhất có source
  thật** trong repo — không phải suy ra, đọc trực tiếp được.
- `plugins/` — 13 plugin mẫu (ví dụ `security-guidance`, `ralph-wiggum`,
  `commit-commands`) dùng **hook công khai dạng cũ**: file `hooks/hooks.json`
  khai báo theo tên hook chuẩn (`SessionStart`, `PostToolUse`, `Stop`, ...),
  mỗi hook là lệnh shell ngoài tiến trình. Đây là seam nông, công khai, dùng
  được từ ngày hooks ra mắt.
- `examples/`, `README.md`, `CLAUDE.md` của chính repo này — tài liệu, không
  phải source.

**Hệ quả bắt buộc:** không cố trả lời "Claude Code hiện thực thế nào bên
trong" (ví dụ: vòng lặp agent loop thật chạy ra sao, prompt được build bằng
hàm gì). Không có bằng chứng cho câu đó trong repo. Những gì dưới đây chỉ nói
về: (a) **tính năng lộ ra ngoài** và dòng thời gian của chúng (từ CHANGELOG),
và (b) **hai seam mở rộng có code thật** (từ `mods/` và `plugins/`).

## 1. Trả lời ngắn

- **Q1 (quản lý ngữ cảnh):** đi từ "nén khi đầy" (auto-compact, 0.2.47) sang
  "đừng để đầy" (offload ra đĩa, defer tool description qua `ToolSearch`,
  2.1.0–2.1.7) sang "mở rộng cửa sổ" (1M context, 2.1.50) sang "làm compact
  đáng tin + có thể can thiệp" (circuit breaker, `PreCompact`/`PostCompact`
  hooks, 2.1.76–2.1.105). Không có bằng chứng về "microcompact" trong repo
  này (0 match).
- **Q2 (seam mở rộng):** có **hai tầng** tách biệt hẳn nhau. Tầng ngoài
  (`plugins/`): hook JSON cổ điển, chạy process con, nhận/trả JSON qua
  stdin/stdout, cắt tại các điểm thô (SessionStart, PreToolUse, PostToolUse,
  Stop...). Tầng trong (`mods/`): event bus TypeScript in-process, hàm
  `on(event, matcher, (ctx, event, next) => ...)` kiểu middleware (gọi
  `next(event)` để tiếp tục chuỗi, có thể sửa event trước/sau), cắt tại hạt
  mịn hơn nhiều (`tool.call`, `tool.check`, `prompt.context`, `agent.spawn`...).
- **Q3 (tool set):** đọc file và tìm-trong-file là **ba tool riêng, không
  phải hai**: `Read` (đọc nội dung theo path đã biết) tách khỏi `Grep` (tìm
  theo nội dung) và `Glob` (tìm theo tên/pattern file) — cả ba tên xuất hiện
  độc lập suốt CHANGELOG kể từ 0.2.82. Ranh giới giữa chúng không đổi qua các
  đợt tái cấu trúc tool (kể cả khi native build thay `Grep`/`Glob` bằng
  `ugrep`/`bfs` ở 2.1.x — tên tool vẫn giữ nguyên, chỉ đổi backend).
- **Q4 (khác biệt kiến trúc so với deepseek-harness):** deepseek-harness lộ
  **một** lớp plugin/hook (Cordis) cho mọi mục đích. Claude Code lộ **hai**
  lớp tách biệt theo mức tin cậy: hook nông (ai cũng cài được, chạy ngoài
  tiến trình, không đọc được state engine) và mod sâu (ký bởi Anthropic/tổ
  chức, in-process, thấy toàn bộ `EngineInterface`). Đây là lựa chọn kiến trúc
  thật — không phải khác biệt đặt tên.

## 2. Q1 — Quản lý ngữ cảnh: dòng tiến hoá

Lọc CHANGELOG theo các từ khoá compact/context/token/truncat/summariz/memory,
gắn version, rồi đọc theo thứ tự thời gian (CHANGELOG format mới-nhất-ở-đầu,
nên đọc từ cuối file lên đầu để thấy đúng chiều tiến hoá).

**Giai đoạn 1 — nén cơ bản (0.2.x):**
- `0.2.47`: "Automatic conversation compaction for infinite conversation
  length (toggle with /config)" — tính năng auto-compact ra đời ngay từ rất
  sớm, là cơ chế nền tảng chứ không phải bổ sung sau.
- `0.2.98`: fix "auto-compact was running twice" — bug cho thấy cơ chế này
  chạy như một side-effect tách rời vòng lặp chính, dễ bị trigger kép.

**Giai đoạn 2 — UI và hook quanh compact (1.0.x):**
- `1.0.8`: "Improved compacting UI".
- `1.0.11`: "Improved todo list handling during compaction" — state ngoài
  hội thoại (todo list) cũng phải được bảo toàn qua ranh giới compact, không
  chỉ là "tóm tắt text".
- `1.0.48`: **`PreCompact` hook** ra đời — điểm can thiệp đầu tiên cho người
  dùng/plugin trước khi compact chạy.
- `1.0.51`: ngưỡng cảnh báo auto-compact tăng từ 60% lên 80% context window.
- `1.0.77`: "Fix token limit errors in conversation summarization" — bản thân
  request tóm tắt cũng có thể vượt giới hạn token, một vấn đề tái diễn nhiều
  version sau.

**Giai đoạn 3 — chuyển từ "nén" sang "đừng để phải nén" (2.0–2.1.x sớm):**
- `2.0.31`: fix `/compact` fail với `prompt_too_long` bằng cách tôn trọng
  compact boundary đã có — tức là compact giờ là nhiều boundary tích luỹ,
  không phải một lần nén toàn bộ lịch sử mỗi lần.
- `2.0.64`: "Made auto-compacting instant"; cải thiện hiệu quả đếm token trên
  Bedrock.
- `2.0.65`, `2.0.70`, `2.0.74`: thêm thông tin context window vào status line
  và field `current_usage` — quan sát được context usage trở thành first-class
  UI, không chỉ nội bộ.
- `2.1.0`: hàng loạt thay đổi cùng lúc — cải thiện độ tin cậy compaction,
  **truncate output nền (background task) về 30K ký tự kèm file path
  reference** thay vì giữ hết trong context. Đây là bước ngoặt: thay vì nén
  text, bắt đầu **đẩy dữ liệu lớn ra ngoài context, giữ lại con trỏ**.
- `2.1.2`: mở rộng chiến lược đó — "large tool outputs... persisted to disk
  instead of truncated, providing full output access via file references";
  tương tự cho output của bash command. Từ đây model không mất thông tin, chỉ
  mất nó khỏi context ngay lập tức, đọc lại được qua `Read`.
- `2.1.7`: **`ToolSearch`/`MCPSearch`** — khi mô tả tool MCP vượt 10% context
  window, chúng được defer và chỉ discover khi cần, thay vì load hết upfront.
  Đây là chiến lược "đừng nạp vào context trừ khi cần" áp dụng cho tool
  definitions, song song với chiến lược áp dụng cho tool *outputs* ở 2.1.0–2.1.2.
- `2.1.32`: ngân sách ký tự cho mô tả skill **scale theo context window** (2%
  context) — cùng một tư duy: giới hạn không cố định bằng số tuyệt đối mà theo
  tỷ lệ cửa sổ hiện có.

**Giai đoạn 4 — mở cửa sổ (2.1.50, 2.1.75):**
- `2.1.50`: Opus 4.6 (fast mode) có full 1M context window; thêm biến môi
  trường để tắt nếu cần.
- `2.1.75`: 1M context mặc định cho Max/Team/Enterprise (trước đó cần mua
  thêm usage).
- Cùng nhịp: `2.1.50` cũng giảm memory usage bằng cách "clearing internal
  caches after compaction" — mở cửa sổ không thay thế được nhu cầu dọn cache,
  hai việc đi song song chứ không loại trừ nhau.

**Giai đoạn 5 — làm compact đáng tin cậy + có thể kiểm soát (2.1.6x–2.1.12x):**
- `2.1.69`: UI hiện "Compacted chat" dạng card gấp được; giảm token subagent
  report; sửa nhiều rò rỉ bộ nhớ liên quan compaction (teammate giữ cả lịch
  sử parent ngay cả sau `/clear`).
- `2.1.76`: **circuit breaker** — auto-compact dừng sau 3 lần thất bại liên
  tiếp thay vì retry vô hạn; thêm **`PostCompact` hook**.
- `2.1.83`: `MEMORY.md` index tự cắt ở 25KB/200 dòng — memory tách hẳn thành
  một tài liệu riêng có giới hạn kích thước riêng, không còn gộp chung vào
  CLAUDE.md.
- `2.1.89`: phát hiện "autocompact thrash loop" — context đầy lại ngay sau khi
  vừa compact ba lần liên tiếp thì dừng hẳn với lỗi rõ ràng thay vì đốt API
  call vô ích.
- `2.1.105`: **`PreCompact` hook có thể chặn compaction** (exit code 2 hoặc
  `{"decision":"block"}`) — từ chỗ chỉ được "biết trước" (1.0.48) đến chỗ
  "ngăn được" là một bước tăng quyền kiểm soát rõ rệt.
- `2.1.117`: `/resume` tự đề nghị tóm tắt session cũ/lớn trước khi đọc lại
  toàn bộ — áp dụng tư duy "đừng nạp hết vào context" cho cả việc resume,
  không chỉ cho turn hiện tại.

**Hình dạng tổng thể:** đường đi không phải "ngày càng nén nhiều hơn" mà là
nén → giảm nhu cầu phải nén (offload ra đĩa, defer tool defs) → mở cửa sổ vật
lý → siết lại độ tin cậy và trao quyền can thiệp (hook chặn được, circuit
breaker). Không tìm thấy từ "microcompact" trong CHANGELOG — nếu nó tồn tại
trong Claude Code thật, nó không lộ ra qua tên này ở bất kỳ changelog entry
nào tới `2.1.287`.

## 3. Q2 — Seam mở rộng: hai tầng, cắt ở hai độ sâu khác nhau

### 3.1. Tầng nông — `plugins/` (hook JSON công khai)

Bằng chứng: `plugins/security-guidance/hooks/hooks.json`,
`plugins/ralph-wiggum/hooks/hooks.json`. Cấu trúc: khai báo theo
`hooks.<EventName>[].hooks[]`, mỗi hook entry là `{"type": "command",
"command": "...", "matcher": "...", "if": "...", "timeout": ..., "asyncRewake": true}`.

- Event tên chuẩn: `SessionStart`, `UserPromptSubmit`, `PostToolUse`, `Stop`
  (và theo CHANGELOG: `PreToolUse`, `PreCompact`, `PostCompact`,
  `PermissionRequest`, `SubagentStart`, `SubagentStop`, `SessionEnd`,
  `Notification` — dòng thời gian ra mắt dàn trải từ lúc "Released hooks"
  (CHANGELOG dòng 7732) tới các bổ sung nhỏ giọt về sau).
- Chạy **ngoài tiến trình** (`bash "${CLAUDE_PLUGIN_ROOT}/..."`), nhận input
  qua JSON (stdin, theo tài liệu), trả quyết định qua exit code/JSON stdout.
  `matcher` lọc theo tên tool, `if` lọc theo nội dung command (ví dụ
  `"if": "Bash(git push:*)"`), `asyncRewake` cho hook chạy nền rồi "đánh thức"
  lại model bằng `rewakeMessage`.
- Seam này cắt ở **ranh giới turn/tool-call thô**: trước/sau một tool chạy,
  đầu/cuối session, lúc model định dừng. Không thấy quyền sửa *input* của tool
  call hay *state nội bộ engine* trong định dạng này (ngoại trừ CHANGELOG ghi
  nhận "PreToolUse hooks can now modify tool inputs" — nhưng đó vẫn qua kênh
  JSON stdout, không phải truy cập trực tiếp).

### 3.2. Tầng sâu — `mods/` (Claude Mods, TypeScript in-process)

Bằng chứng trực tiếp: CHANGELOG `2.1.287` — "Added Claude Mods: plugins may
now modify deeper behavior" — và code thật trong `mods/agents-md/hooks/register.ts`,
`mods/types/claude-code.d.ts` (13186 dòng).

- API là một hàm `on(eventName, matcher?, handler)` nơi `handler` có chữ ký
  `($: EngineInterface, e: Event, next) => result` — đúng mẫu **middleware
  kiểu onion**: handler nhận event, có thể sửa nó, gọi `next(e)` để đi tiếp
  xuống handler/engine phía dưới, rồi còn có thể sửa *kết quả* trả về trước
  khi trả lên trên. Ví dụ thật, `mods/agents-md/hooks/register.ts`:
  ```ts
  on('tool.call', { tool: 'Read' }, async ($, e, next) => {
    const result = await next(e)
    // ...kiểm tra, rồi gắn thêm context (AGENTS.md lồng nhau) vào result
    return { ...result, context: [...] }
  })
  ```
- Event nội bộ thật sự tồn tại (gom từ toàn bộ `grep on\(' mods/*/hooks/register.ts`):
  `session.start`, `session.end`, `prompt.context`, `prompt.compose`,
  `prompt.section`, `prompt.submit`, `tool.call`, `tool.check`, `tool.list`,
  `tool.describe`, `tool.register`, `agent.spawn`, `agent.offer`,
  `command.run`, `command.describe`, `skill.prompt`, `settings.read`,
  `plugin.register`, `engine.create`, `attribution.text`, `telemetry.log`,
  `telemetry.mark`, `ui.render`, `ui.focus`, `ui.close`, `ui.scroll`. Đây là
  một bề mặt mịn hơn hẳn 10+ hook event công khai của tầng `plugins/`.
- `EngineInterface` (`$`) cho handler gọi lại chính engine: `$.tool.call(...)`,
  `$.fs.ancestors(...)`, `$.session.root()`, `$.telemetry.log(...)`,
  `$.ui.log(...)`, `$.env.get(...)` — tức là mod không chỉ quan sát, nó **gọi
  được năng lực của engine** từ trong handler.
- Phân quyền: `mods/sec-default/hooks/hooks.json` (mô tả) cho thấy tầng mod
  có khái niệm "tier" (`user` tier vs tổ chức), và có thể **từ chối một mod
  khác đăng ký** (`refuses a user-tier hooks module at plugin.register while
  managed settings set its allowManagedModsOnly option`) — nghĩa là seam sâu
  này tự nó có một lớp policy kiểm soát ai được dùng nó, khác hẳn tầng
  `plugins/` vốn không phân biệt nguồn.
- Có cầu nối giữa hai tầng: `claude-code.d.ts` nhắc tới `classic.PreToolUse`
  (dòng ~4729: "`classic.PreToolUse` shares `tool.call`'s envelope") — tức là
  hook công khai cũ được tái hiện lại như một event trong event bus mới, hai
  tầng không hoàn toàn tách rời mà tầng sâu "bao" lấy tầng nông.

### 3.3. Kết luận cho Q2

Không phải một seam có nhiều điểm cắt, mà là **hai cơ chế mở rộng khác hẳn
nhau về mô hình tin cậy và độ chi tiết**, chọn tuỳ theo ai được cấp quyền viết
mod: ai cũng viết được shell-hook JSON; TypeScript mod (ít nhất theo
`mods/sec-default`) có khái niệm tier và có thể bị tổ chức khoá lại.

## 4. Q3 — Bộ tool model-facing và ranh giới

**Đọc file vs tìm trong file — ba tool, không phải hai:**
- `Read` — đọc nội dung theo path đã biết. Nguồn gốc tên: CHANGELOG dòng 7955
  (`0.2.82`) "Renamed tools for consistency: LSTool -> LS, View -> Read".
- `Grep` — tìm theo nội dung. Tái thiết kế lớn ở dòng 7691: "Redesigned Search
  (Grep) tool with new tool input parameters and features".
- `Glob` — tìm theo tên/pattern file, luôn được nhắc **cùng nhưng tách khỏi**
  `Grep` trong suốt lịch sử, ví dụ dòng 4904 (native build thay cả hai bằng
  `bfs`/`ugrep` — nhưng vẫn là hai tool riêng ở tầng model-facing, chỉ đổi
  backend thực thi), dòng 4821, dòng 3886 (`--tools` cho phép liệt kê riêng
  `Grep`/`Glob`).
- Bằng chứng ranh giới không đổi: dòng 6491 — "guide the model toward using
  dedicated tools (Read, Edit, Glob, Grep) instead of bash equivalents (cat,
  sed, grep, find)" — bốn tool được liệt kê tách bạch, ánh xạ 1-1 với bốn lệnh
  Unix tương ứng, xác nhận rằng tool set model-facing cố tình giữ Read và
  Grep/Glob là các khái niệm riêng (đọc theo path đã biết ≠ tìm khi chưa biết
  path).

**Các cặp/họ tool khác đáng chú ý theo CHANGELOG:**
- `WebFetch` (lấy nội dung một URL đã biết) tách khỏi `WebSearch` (tìm URL
  chưa biết) — hai biến môi trường bật/tắt riêng (`CLAUDE_CODE_DISABLE_WEB_FETCH`
  dòng 205; giới hạn số lần gọi `CLAUDE_CODE_MAX_WEB_SEARCHES_PER_SESSION`
  dòng 3031) — cùng mẫu "đã-biết-vị-trí vs tìm-kiếm" như Read/Grep.
- `Agent` tool (trước gọi là `Task` tool — dòng 7968 "Task tool can now
  perform writes and run bash commands") để chạy subagent; tham số `mode`
  trên Task tool bị deprecate (dòng 3070) khi subagent chuyển sang kế thừa
  permission mode của parent.
- Nhóm quản lý việc (`TaskCreate/Get/Update/List`, `TodoWrite`) là tool
  **riêng khỏi** `Agent` tool — quản lý todo-list của chính phiên làm việc,
  không phải spawn subagent; bị giới hạn theo model (dòng 2570: không có sẵn
  trên một số model mới nhất trừ khi set env var).
- `AskUserQuestion` là tool riêng để hỏi người dùng giữa chừng — có cơ chế
  idle-timeout riêng, không trộn vào luồng text thường.
- `ToolSearch`/`MCPSearch` (seam Q1 giao Q3): bản thân việc "tìm tool" cũng là
  một tool, chỉ bật khi mô tả tool MCP vượt ngưỡng context — một tool
  model-facing được sinh ra để giải quyết vấn đề quản lý context, không phải
  để làm việc trên codebase.

**Tổng kết ranh giới:** mẫu lặp lại nhất quán trong toàn bộ tool set là tách
**"biết vị trí, lấy nội dung" (Read/WebFetch)** khỏi **"chưa biết vị trí, phải
tìm" (Grep+Glob/WebSearch)**, và tách riêng **hành động điều phối** (Agent,
TaskCreate*, AskUserQuestion) khỏi cả hai nhóm trên.

## 5. Q4 — So với deepseek-harness: khác biệt nào là lựa chọn kiến trúc thật

(Đối chiếu với `docs/deepseek_harness_cordis_study_notes.md` đã đọc trong
cùng phiên.)

- **Một seam vs hai seam.** deepseek-harness lộ một plugin/event system
  (Cordis) cho mọi mục đích mở rộng, không phân biệt mức tin cậy. Claude Code
  tách hẳn "hook ai cũng cài được, chạy ngoài tiến trình, thô" (`plugins/`)
  khỏi "mod có tier, chạy trong tiến trình, mịn" (`mods/`). Đây **là** một
  lựa chọn kiến trúc: đánh đổi một bề mặt API duy nhất lấy việc kiểm soát
  được ai chạm vào state engine thật.
- **Context management là tính năng được đầu tư liên tục qua hàng trăm
  version**, không phải một cơ chế cố định dựng một lần. deepseek-harness,
  theo ghi chú đã có, không mô tả một dòng tiến hoá tương đương (không có
  bằng chứng nào trong notes cũ về "circuit breaker cho auto-compact" hay
  "ToolSearch cho tool definitions"). Đây là khác biệt về **độ trưởng thành
  vận hành** ghi nhận được, không chắc là khác biệt kiến trúc gốc — cần đọc
  thêm deepseek-harness changelog (nếu có) để kết luận chắc hơn; **không chắc**.
- **Read/Grep/Glob tách ba, không hợp nhất "file tool" làm một.** Đây khớp
  với nhận xét trong brief rằng deepseek-harness tách `read` khỏi `grep`/`glob`
  thành hai package — Claude Code cũng tách, nhưng tách thành **ba** tool
  model-facing riêng biệt (Read, Grep, Glob) chứ không phải gộp Grep+Glob làm
  một "search tool". Khác biệt nhỏ nhưng là lựa chọn thật: với Claude Code,
  "tìm theo tên file" và "tìm theo nội dung" là hai năng lực tách bạch ở tầng
  model thấy được, không chỉ tách ở tầng package nội bộ.
- Những khác biệt **bỏ qua vì chỉ là đặt tên**: "Task tool" đổi tên thành
  "Agent tool" (dòng 7968 → về sau), "View" đổi tên thành "Read" (dòng 7955)
  — đổi tên không phải đổi ranh giới.

## 6. Chỗ đáng mang về `mini_harness`

- **Tách Read khỏi Grep/Glob như hai (hoặc ba) tool riêng.** Đáng mang về:
  bằng chứng từ cả hai harness (deepseek lẫn Claude Code) hội tụ về cùng một
  ranh giới — "đọc theo path đã biết" và "tìm khi chưa biết path" là hai thao
  tác có input/output shape khác nhau đủ để tách tool, không phải tách cho
  đẹp.
- **Offload-ra-đĩa-giữ-con-trỏ thay vì truncate-mất-luôn** (CHANGELOG 2.1.0,
  2.1.2, 2.1.51). Đáng mang về: `mini_harness` hiện tại (theo tên các commit
  gần đây — "cắt tool result ở phép chiếu", "spill: khúc bị cắt vẫn đọc lại
  được") có vẻ đã học đúng hướng này; Claude Code xác nhận đây không phải ý
  tưởng ngẫu nhiên mà là một bước chuyển có chủ đích, lặp lại nhiều version
  liền (2.1.0 cho background output, 2.1.2 cho tool output nói chung, 2.1.51
  hạ ngưỡng từ 100K xuống 50K ký tự) — tức ngưỡng cần tinh chỉnh dần, không
  có công thức đúng ngay từ đầu.
- **`PreCompact` có thể chặn (exit code 2 / `decision: block`), không chỉ
  được thông báo trước.** Đáng mang về nếu `mini_harness` có hook quanh
  compact: phân biệt "hook chỉ xem" và "hook có quyền chặn" là hai mức quyền
  khác nhau, Claude Code đi từ mức 1 (1.0.48) lên mức 2 (2.1.105) chỉ sau khi
  mức 1 đã chạy ổn định qua rất nhiều version — gợi ý thứ tự làm: xem trước,
  chặn sau.
- **Circuit breaker cho auto-compact thất bại liên tiếp** (2.1.76) và
  "autocompact thrash loop" detection (2.1.89). Đáng mang về: mọi cơ chế tự
  động phản ứng với áp lực context (ở đây là compact) cần một giới hạn số lần
  thử trước khi dừng hẳn và báo lỗi rõ ràng — nếu không, hệ thống có thể đốt
  tài nguyên vô hạn trong vòng lặp "compact xong lại đầy ngay".
- **Scale giới hạn theo tỷ lệ cửa sổ, không theo số tuyệt đối** (skill budget
  = 2% context window, 2.1.32). Đáng mang về: nếu `mini_harness` hard-code
  ngưỡng ký tự/token cố định ở đâu đó, đây là bằng chứng nên đổi sang tỷ lệ
  phần trăm context window hiện có, vì context window tự nó có thể thay đổi
  (1M vs 200K) và ngưỡng tuyệt đối sẽ sai lệch theo.
- **Chưa đáng mang về ngay: event bus kiểu `mods/`** (on/next/middleware với
  hàng chục event). Lý do: đây là seam cho bên thứ ba viết plugin phức tạp
  chạm sâu vào engine — `mini_harness` là dự án học kiến trúc một người dùng,
  chưa có nhu cầu phân quyền nhiều tầng plugin. Ghi nhận mẫu thiết kế (middleware
  với `next()`) là hữu ích để hiểu, nhưng dựng một event bus đầy đủ ngay bây
  giờ là over-engineering so với quy mô hiện tại của `mini_harness`.

## 7. Câu không trả lời được (và vì sao)

- Claude Code thật sự tóm tắt hội thoại bằng prompt gì, hay có ép cấu trúc gì
  cho bản tóm tắt không — không có source, CHANGELOG chỉ ghi tên tính năng.
- Thuật toán/ngưỡng quyết định *khi nào* auto-compact trigger (ngoài các con
  số % đã nêu) — không thấy trong repo.
- "Microcompact" có tồn tại trong Claude Code thật hay không — 0 kết quả
  trong CHANGELOG tới `2.1.287`; không đủ bằng chứng để nói có hay không.
- Cách engine thật thực thi `on(event, matcher, handler)` (thứ tự ưu tiên
  nhiều mod cùng đăng ký một event, cách `next()` được dựng) — chỉ thấy phía
  gọi (`register.ts`), không thấy phía engine (nằm trong bundle đã minify).
