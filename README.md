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
           read_spill.py                              # đọc lại khúc đã cắt
  cli/     terminal.py  chat.py              # chỗ duy nhất in ra màn hình
  context.py                                 # giờ, workspace — chạm thế giới thật
  profiles.py                                # general, tutor, research
  app.py                                     # chỗ duy nhất biết tất cả những thứ trên
```

Ý chính: `core/loop.py` chỉ khai báo Protocol cho ba thứ nó cần (LLM, Tools,
Session) rồi lái vòng lặp. Đổi provider hay thêm tool không cần sửa nó. Session
là **event log append-only**; message list gửi cho model là thứ *phái sinh*
(`to_messages()`) — đó là cái làm resume, replay và **compaction** khả thi: cắt
ngữ cảnh cho vừa cửa sổ model là cắt ở phép chiếu, log vẫn nguyên vẹn.

Ba phép cắt, xếp theo mức mất mát **tăng dần** — thứ tự đó là toàn bộ thiết kế:

1. **nén** — khúc đầu hội thoại được thay bằng một bản tóm tắt *do chính model
   viết* (`core/compaction.py`). Mất chi tiết, giữ lại ý. Đây là phép cắt duy
   nhất tốn một lần gọi model, nên nó nổ ở ngưỡng 0.8 ngân sách chứ không đợi
   tràn — còn kịp chỗ cho chính request tóm tắt.
2. **cắt ruột tool result** quá khổ, giữ đầu + đuôi. Gần như không mất gì.
3. **bỏ trọn turn cũ nhất.** Mất hẳn, không có đường về — nên nó là *lưới an
   toàn*, chỉ chạy khi hai bước trên đã làm hết sức mà vẫn chưa vừa.

Chỗ bị cắt để lại **locator**, và tool `read_spill` cầm locator đó đọc ngược vào
log — nên cắt là *cất đi*, không phải *huỷ*. Kho spill không phải thứ dựng thêm:
nó chính là event log. Điều đó đúng cả bên trong vùng đã nén: bản tóm tắt nói
thẳng với model rằng tool result ở đó vẫn gọi lại được bằng `call_id`. Ngân sách
cũng không đoán suông — `usage` mà API trả về ở response trước được dùng để neo
lại bộ ước lượng.

Một **loại agent** (`general`, `tutor`, …) là *dữ liệu*, không phải class con:
persona + tập tool. Thêm agent mới không sửa dòng nào trong `core/loop.py` hay
`tools/registry.py` — đó là phép thử của các seam phía trên.

### Chạy

```bash
python3 -m mini_harness --azure                     # chat mode
python3 -m mini_harness --azure "100 * 1.1 = ?"     # một câu rồi thoát
python3 -m mini_harness --azure --session run.jsonl # ghi/resume event log
python3 -m mini_harness --replay run.jsonl          # phát lại log cũ, không cần API key
python3 -m mini_harness --azure --agent tutor       # đổi persona + tập tool
python3 -m mini_harness --azure --agent research    # cần TAVILY_API_KEY
python3 -m pytest -q
```

Credential đọc từ `.env` ở gốc repo (không commit):
`AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_ENDPOINT`, `AZURE_API_VERSION`,
`AZURE_OPENAI_DEPLOYMENT` — hoặc `DEEPSEEK_API_KEY` cho `--deepseek`.
`TAVILY_API_KEY` chỉ cần cho agent nào cầm `web_search`; thiếu thì harness
dừng ngay lúc khởi động chứ không lặng lẽ bỏ tool đi.

Trong chat mode: `Ctrl-C` huỷ **turn** đang chạy và giữ session; `Ctrl-C` ở
prompt trống, `Ctrl-D` hoặc `/quit` để thoát.
