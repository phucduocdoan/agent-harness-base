# agent-harness-base

Học kiến trúc agent harness bằng cách **đọc một harness thật rồi tự dựng lại bản tối giản**.

Harness được đọc để đối chiếu là [deepseek-ai/deepseek-harness](https://github.com/deepseek-ai/deepseek-harness)
(TypeScript, MIT) — repo đó không nằm trong đây, clone riêng cạnh thư mục này nếu
muốn tra cứu. Comment trong code có dòng "Đối chiếu: …" trỏ tới file tương ứng bên đó.

## Trong repo

| | |
|---|---|
| [`docs/how-to-learn.md`](docs/how-to-learn.md) | Thứ tự đọc, không đọc repo từ đầu đến cuối |
| [`docs/deepseek_harness_cordis_study_notes.md`](docs/deepseek_harness_cordis_study_notes.md) | Ghi chú kiến trúc: Cordis runtime + core packages |
| `mini_harness/`, `tests/` | Bản Python tối giản, tự viết |

## Harness tối giản

```
mini_harness/
  core/    types.py  loop.py  session.py     # không import implementation nào
           compaction.py                     # nén khúc đầu bằng summary model viết
           prompt.py  agent.py               # ráp system prompt; agent = dữ liệu
  llm/     stream.py  deepseek.py  azure.py  # chỗ duy nhất biết wire format
  tools/   registry.py  calculator.py  write_file.py  web_search.py
           read_spill.py                              # đọc / tìm trong khúc đã cắt
           task.py                                    # giao việc cho sub-agent
  cli/     terminal.py  chat.py              # chỗ duy nhất in ra màn hình
  context.py                                 # giờ, workspace — chạm thế giới thật
  profiles.py                                # general, tutor, research, lead
  app.py                                     # chỗ duy nhất biết tất cả những thứ trên
```

Ý chính: `core/loop.py` chỉ khai báo Protocol cho ba thứ nó cần (LLM, Tools,
Session) rồi lái vòng lặp. Đổi provider hay thêm tool không cần sửa nó. Session
là **event log append-only**; message list gửi cho model là thứ *phái sinh*
(`to_messages()`) — đó là cái làm resume, replay và **compaction** khả thi: cắt
ngữ cảnh cho vừa cửa sổ model là cắt ở phép chiếu, log vẫn nguyên vẹn.

Ba phép cắt, xếp theo mức mất mát **tăng dần**:

1. **nén** — khúc đầu hội thoại được thay bằng một bản tóm tắt *do chính model
   viết* (`core/compaction.py`). Mất chi tiết, giữ lại ý. Đây là phép cắt duy
   nhất tốn một lần gọi model, nên nó nổ ở ngưỡng 0.8 ngân sách chứ không đợi
   tràn — còn kịp chỗ cho chính request tóm tắt.
2. **cắt ruột tool result** quá khổ, giữ đầu + đuôi. Gần như không mất gì.
3. **bỏ trọn turn cũ nhất.** Mất hẳn, không có đường về — nên nó là *lưới an
   toàn*, chỉ chạy khi hai bước trên đã làm hết sức mà vẫn chưa vừa.

Thứ tự đó không phải luật của ngành. Nó là **hệ quả** của việc tách log khỏi
phép chiếu, và chỉ phát biểu được vì cả ba phép cắt đều xảy ra ở phép chiếu.
Codex không xếp được ba phép này, vì với nó câu hỏi không tồn tại: tool result
bị cắt ruột ngay lúc *ghi vào history*, mất vĩnh viễn, xong trước khi phép nén
kịp được cân nhắc; còn việc bỏ item cũ nhất chỉ chạy bên trong vòng retry của
chính phép nén, khi request tóm tắt tự nó bị API trả `ContextWindowExceeded`.
Ba phép cắt có thứ tự là thứ bạn **được** khi log còn nguyên, không phải thứ
phải có (`docs/codex_study_notes.md`).

Và ba không phải là hết. Claude Code có phép cắt thứ tư mà `mini_harness` không
có: khi mô tả tool chiếm quá 10% cửa sổ, nó hoãn chính *định nghĩa tool* lại và
bắt model đi tìm khi cần (`MCPSearch`). Phép đó cắt ở schema chứ không cắt ở
message — một trục khác hẳn, và nó chỉ đáng làm khi số tool đã lớn
(`docs/claude_code_study_notes.md`).

Chỗ bị cắt để lại **locator**, và tool `read_spill` cầm locator đó đọc ngược vào
log — nên cắt là *cất đi*, không phải *huỷ*. Nó còn tìm được (`query`), vì một
kết quả bị giấu có thể dài hàng chục nghìn ký tự và model không có cách nào
đoán ra nên đọc ở offset nào. Cặp đọc + tìm đó không phải phát minh gì: các
harness khác ghi spill ra **file**, nên `read` và `grep` sẵn có của chúng đã
làm đúng việc này. Ở đây kho spill là event log chứ không phải filesystem, nên
cặp công cụ đó phải dựng lại trên nền khác. Kho spill không phải thứ dựng thêm:
nó chính là event log. Điều đó đúng cả bên trong vùng đã nén: bản tóm tắt nói
thẳng với model rằng tool result ở đó vẫn gọi lại được bằng `call_id`. Ngân sách
cũng không đoán suông — `usage` mà API trả về ở response trước được dùng để neo
lại bộ ước lượng.

Một **loại agent** (`general`, `tutor`, …) là *dữ liệu*, không phải class con:
persona + tập tool. Thêm agent mới không sửa dòng nào trong `core/loop.py` hay
`tools/registry.py` — đó là phép thử của các seam phía trên.

### Uỷ quyền: trục thứ hai

Cả ba phép cắt ở trên — kể cả phép thứ tư của Claude Code — đều chữa một log
**đã** phình. Tool `task` đi trục khác: việc giao cho sub-agent chạy trong một
`Session` riêng của nó, và cha chỉ nhận lại đúng đoạn text cuối. Không phải
"cắt bớt cái đã to", mà là "đừng để nó to lên ở chỗ cha".

Đo thật, một câu hỏi buộc `lead` giao việc tra cứu cho `research`: con đi 4
step, đọc 26.435 ký tự tool result, đốt 13.984 token; cha nhận lại 3.113 ký tự
và request cuối của cha được API đo **1.318 token**. Dựng lại phản-thực bằng
chính bộ ước lượng của `Session` — cùng hội thoại đó nhưng cha tự làm lấy —
cửa sổ cha là ~13.770 thay vì ~2.647: **5,2 lần**. Và tỷ lệ đó còn mở ra theo
mỗi step con đi thêm, trong khi cha đứng yên.

Hai thứ dễ gộp nhầm vì cùng đo bằng "token". Cửa sổ ngữ cảnh tính theo **từng
request**, nên con có một ngân sách MỚI chứ không phải một nửa ngân sách của
cha. Còn hạn mức token/phút là tài nguyên chung của cả deployment, và con vẫn
gọi model qua đúng client của cha nên vẫn trừ vào đó. Uỷ quyền nới cái thứ
nhất, không nới cái thứ hai.

Độ sâu chặn bằng **dữ liệu**, không bằng biến đếm lúc chạy: `LEAD_DELEGATES`
nói ai được giao việc, và `app.py` kiểm lúc khởi động rằng không ai trong số
đó cầm `task` — nhìn hai dòng cạnh nhau là biết cây sâu tới đâu. Approver
truyền thẳng xuống con, không nới ra: uỷ quyền không được là đường leo thang
quyền. Và `--replay --agent lead` **dừng ngay** chứ không chạy thử: hội thoại
của con nằm ở log riêng (`run.task-1.jsonl`), còn log đang phát lại chỉ chứa
đúng câu trả lời cuối của nó.

### Chạy

```bash
python3 -m mini_harness --azure                     # chat mode
python3 -m mini_harness --azure "100 * 1.1 = ?"     # một câu rồi thoát
python3 -m mini_harness --azure --session run.jsonl # ghi/resume event log
python3 -m mini_harness --replay run.jsonl          # phát lại log cũ, không cần API key
python3 -m mini_harness --azure --agent tutor       # đổi persona + tập tool
python3 -m mini_harness --azure --agent research    # cần TAVILY_API_KEY
python3 -m mini_harness --azure --agent lead        # giao việc cho sub-agent
python3 -m pytest -q
```

Credential đọc từ `.env` ở gốc repo (không commit):
`AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_ENDPOINT`, `AZURE_API_VERSION`,
`AZURE_OPENAI_DEPLOYMENT` — hoặc `DEEPSEEK_API_KEY` cho `--deepseek`.
`TAVILY_API_KEY` chỉ cần cho agent nào cầm `web_search`; thiếu thì harness
dừng ngay lúc khởi động chứ không lặng lẽ bỏ tool đi.

Trong chat mode: `Ctrl-C` huỷ **turn** đang chạy và giữ session; `Ctrl-C` ở
prompt trống, `Ctrl-D` hoặc `/quit` để thoát. `/compact` nén ngay, không đợi
ngưỡng — nó bỏ qua đúng hai thứ (ngưỡng và cầu dao đếm số lần nén hỏng), còn
vùng giữ nguyên văn thì vẫn giữ. `/context` cho biết còn bao nhiêu chỗ. `/task` liệt kê các lần đã uỷ quyền,
`/task N` in lại transcript của lần thứ N — đó là cách xem SAU khi con chạy
xong; lúc nó đang chạy thì từng tool nó gọi đã được in thụt vào. Gõ sai
tên lệnh thì báo tại chỗ chứ không lặng lẽ gửi cho model — một lệnh gõ nhầm mà
lọt xuống `run_turn` là một lượt API bị tiêu cho câu hỏi không ai định hỏi.
