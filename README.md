# agent-harness-base

Học kiến trúc agent harness bằng cách **đọc một harness thật rồi tự dựng lại bản tối giản**.

Harness được đọc để đối chiếu là [deepseek-ai/deepseek-harness](https://github.com/deepseek-ai/deepseek-harness)
(TypeScript, MIT) — repo đó không nằm trong đây, clone riêng cạnh thư mục này nếu
muốn tra cứu. Comment trong code có dòng "Đối chiếu: …" trỏ tới file tương ứng bên đó.

## Trong repo

| | |
|---|---|
| `how-to-learn.md` | Thứ tự đọc, không đọc repo từ đầu đến cuối |
| `deepseek_harness_cordis_study_notes.md` | Ghi chú kiến trúc: Cordis runtime + core packages |
| `mini-harness/` | Bản Python tối giản, tự viết |

## mini-harness

```
mini_harness/
  core/    types.py  loop.py  session.py     # không import implementation nào
  llm/     stream.py  deepseek.py  azure.py  # chỗ duy nhất biết wire format
  tools/   registry.py  calculator.py  write_file.py
  cli/     terminal.py  chat.py              # chỗ duy nhất in ra màn hình
  app.py                                     # chỗ duy nhất biết tất cả những thứ trên
```

Ý chính: `core/loop.py` chỉ khai báo Protocol cho ba thứ nó cần (LLM, Tools,
Session) rồi lái vòng lặp. Đổi provider hay thêm tool không cần sửa nó. Session
là **event log append-only**; message list gửi cho model là thứ *phái sinh*
(`to_messages()`) — đó là cái làm resume/replay khả thi.

### Chạy

```bash
cd mini-harness
python3 -m mini_harness --azure                     # chat mode
python3 -m mini_harness --azure "100 * 1.1 = ?"     # một câu rồi thoát
python3 -m mini_harness --azure --session run.jsonl # ghi/resume event log
python3 -m pytest -q
```

Credential đọc từ `.env` ở thư mục cha (không commit):
`AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_ENDPOINT`, `AZURE_API_VERSION`,
`AZURE_OPENAI_DEPLOYMENT` — hoặc `DEEPSEEK_API_KEY` cho `--deepseek`.

Trong chat mode: `Ctrl-C` huỷ **turn** đang chạy và giữ session; `Ctrl-C` ở
prompt trống, `Ctrl-D` hoặc `/quit` để thoát.
