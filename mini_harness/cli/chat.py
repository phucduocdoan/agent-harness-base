"""Vòng chat và phần hiển thị của nó.

Đây là chỗ sở hữu VIỆC HIỂN THỊ. `core/loop.py` không in gì cả — nó không biết
có màn hình. Cái nối hai thứ lại là `Session.on_event`: UI nghe ở đúng commit
point của session, nên mọi thứ user thấy đều là thứ đã thật sự vào log.
"""

from __future__ import annotations

import asyncio
import json
import re
import signal
from collections.abc import Awaitable, Callable
from typing import Any

from mini_harness.cli.terminal import TerminalStream, read_line as _default_read_line
from mini_harness.core.compaction import maybe_compact
from mini_harness.core.loop import MaxStepsExceeded, run_turn
from mini_harness.core.session import Session

# Một dòng CHỈ GỒM một từ dạng `/chữ` thì chắc chắn là người dùng định gõ lệnh.
_LENH = re.compile(r"/[a-zA-Z][a-zA-Z-]*")

# Ba nghĩa trên cùng một lệnh: `/task` liệt kê, `/task N` xem lại lần thứ N,
# `/task <agent> <việc>` uỷ quyền NGAY. Gộp làm một vì cả ba đều nói về cùng
# một thứ — các lần uỷ quyền — và phân biệt được bằng chính đối số: không có
# gì / toàn chữ số / còn lại. Tách thành `/task` và `/delegate` thì người dùng
# phải nhớ hai tên cho một khái niệm.
_TASK = re.compile(r"/task(?: (.+))?")


def echo_tool_activity(
    display: TerminalStream, prefix: str = "",
) -> Callable[[dict[str, Any]], None]:
    """Listener của Session: cho user thấy tool nào vừa chạy, ngay lúc nó chạy.

    KHÔNG phải debug output. Một step chỉ gọi tool thì `reply.text` rỗng, tức là
    không có gì để stream ra — không có listener này thì màn hình im lặng trong
    khi agent thay bạn làm việc, và đó là điều user cần thấy nhất.

    Args:
        display: stream đang in text của model, để đóng dòng dở trước khi chèn
            một dòng tool vào giữa.
        prefix: chèn trước mỗi dòng in ra, để phân biệt hoạt động của sub-agent
            với của agent chính khi cả hai cùng in ra một màn hình.
    """
    def echo(event: dict[str, Any]) -> None:
        display.close()
        for call in event.get("tool_calls", ()):
            print(f"{prefix}  → {call['name']}({call['arguments']})")
        if event["type"] == "tool_result":
            # Một dòng đầu là đủ để biết chạy được hay không; nội dung đầy đủ
            # nằm trong log. Đây là chỗ duy nhất `is_error` được dùng để
            # HIỂN THỊ — nó vẫn không bao giờ lên wire.
            head = event["content"].splitlines()[0] if event["content"] else ""
            mark = "✗" if event.get("is_error") else "←"
            print(f"{prefix}  {mark} {head[:160]}")
    return echo


def print_log(session: Session) -> None:
    """In event log thô — không phải message list. Hai thứ khác nhau."""
    print("\n--- session.events (log thô) ---")
    for index, event in enumerate(session.events):
        mark = " [ERROR]" if event.get("is_error") else ""
        detail = event.get("content") or event.get("name") or ""
        if event.get("tool_calls"):
            detail = f"{detail} -> " + ", ".join(
                f"{call['name']}({call['arguments']})" for call in event["tool_calls"]
            )
        print(f"{index}. {event['type']}{mark}: {detail}")
    print_context(session)


def print_context(session: Session) -> None:
    """Còn bao nhiêu chỗ: số message, token ĐO được, ngân sách.

    In số API đo chứ không in số ước lượng. Bộ đoán nằm trong Session và là
    private — nó tồn tại để Session tự quyết định lúc nào nén, không phải để
    báo cáo. `prompt_tokens` thì là sự thật, và đặt cạnh ngân sách là đủ trả
    lời câu "sắp nén chưa".

    Cái giá phải nói rõ: số đó là của request TRƯỚC, nên nó chưa tính những gì
    vừa thêm vào log sau đó. Ngay sau một tool result quá khổ, con số này còn
    thấp hơn thực tế khá nhiều.
    """
    measured = [
        event["prompt_tokens"]
        for event in session.events
        if event["type"] == "assistant" and event.get("prompt_tokens")
    ]
    do_duoc = f", request cuối {measured[-1]} token (API đo)" if measured else ""
    ngan_sach = f" / ngân sách {session.max_tokens}" if session.max_tokens else ""
    print(f"\n--- to_messages() gửi model: {len(session.to_messages())} message"
          f"{do_duoc}{ngan_sach} ---")


def print_tasks(runs: list[Session], arg: str | None) -> None:
    """`/task`: liệt kê các lần uỷ quyền, hoặc in lại transcript của một lần.

    Session con là nguồn sự thật duy nhất ở đây: tên agent và câu task được đọc
    NGƯỢC ra từ chính event của nó, không phải từ một sổ ghi song song do lệnh
    này tự giữ. Hai bản ghi cùng mô tả một thứ là hai chỗ để lệch nhau.

    Đây là cách xem SAU KHI con chạy xong. Lúc nó đang chạy thì đã có
    `echo_tool_activity` với `prefix` in từng tool nó gọi — xem trực tiếp giữa
    chừng cần một kênh nhập liệu khác hẳn (raw mode, reader sống song song với
    turn), mà `cli/terminal.py` chưa có và cũng chưa cần có.

    Chưa uỷ quyền lần nào thì nói đúng câu đó, kể cả khi người dùng gõ kèm số:
    `(không có task 3 — hiện có 1..0)` vừa sai ngữ pháp vừa trả lời nhầm câu
    hỏi, vì cái người gõ cần biết là chưa có gì để xem.
    """
    if not runs:
        print("(chưa uỷ quyền cho sub-agent lần nào)")
        return
    if arg is None:
        for thu_tu, sub in enumerate(runs, start=1):
            ten = next((e["name"] for e in sub.events if e["type"] == "agent"), "?")
            cau = next((e["content"] for e in sub.events if e["type"] == "user"), "?")
            print(f'{thu_tu}. {ten} · "{cau}" · {len(sub.events)} event')
        return
    if not arg.isdigit() or not 1 <= int(arg) <= len(runs):
        print(f"(không có task {arg} — hiện có 1..{len(runs)})")
        return
    print_log(runs[int(arg) - 1])


async def _delegate_now(delegate: Any, arg: str) -> None:
    """`/task <agent> <việc>`: chính NGƯỜI DÙNG quyết định uỷ quyền, không phải model.

    Vì sao cần cả hai lối. Model gọi tool được GIỮA turn, đúng lúc nó vừa nhận
    ra việc này nên đưa đi chỗ khác — lệnh gõ tay không làm được điều đó, vì nó
    chỉ gõ được lúc đang đứng ở prompt. Đổi lại, lệnh làm được thứ tool không
    làm được: uỷ quyền mà KHÔNG phải thuyết phục model rằng nên uỷ quyền, và
    uỷ quyền từ một agent không hề cầm `task` (`general`, `tutor`). Hai lối bù
    cho nhau chứ không thay nhau.

    Đi qua `registry.execute(name, json)` chứ không gọi thẳng `execute(args)`
    của tool: đó là cùng CÁI CỬA mà model đi, nên kiểm tra tên agent (`enum`
    trong schema), bọc exception thành kết quả đọc được, và trần step của con
    đều dùng lại nguyên si. Dựng lại chúng ở đây là có hai bản để lệch nhau.

    Kết quả CHỈ IN RA, không ghi vào session cha — nói thẳng cái giá: lượt sau
    model cha không hề biết lần uỷ quyền này đã xảy ra. Đó là có chủ ý. Nhét
    một cặp user/assistant giả vào log cha để "cho nó biết" là bịa ra một đoạn
    hội thoại chưa từng xảy ra, và mọi thứ dựng lại từ log — resume, replay —
    sẽ kể lại đúng đoạn bịa đó. Cần model cha biết thì hỏi nó, bằng một câu
    hỏi thật.
    """
    if delegate is None:
        print("(không uỷ quyền được: phiên này không có model thật — hội thoại "
              "của sub-agent nằm ở log riêng, không nằm trong log đang phát lại)")
        return
    ten, _, viec = arg.partition(" ")
    if not viec.strip():
        # Không đoán hộ. `/task research` thiếu hẳn phần việc, mà một câu giao
        # việc rỗng thì sub-agent chỉ có thể hỏi lại — nó không có ai để hỏi.
        print(f"(/task {ten}: thiếu phần việc — `/task <agent> <việc>` để uỷ "
              f"quyền, `/task N` để xem lại lần thứ N)")
        return

    result = await delegate.execute(
        "task", json.dumps({"agent": ten, "task": viec.strip()})
    )
    if result.is_error:
        # Text này model-facing (tiếng Anh) và ở đây nó lọt ra cho người đọc.
        # Chấp nhận: tên agent sai thì chính câu lỗi của `enum` liệt kê ra các
        # tên hợp lệ, tức là nó đã trả lời đúng câu người dùng đang hỏi. Chép
        # lại danh sách đó ở đây là thêm một chỗ nữa phải nhớ cập nhật.
        print(f"(uỷ quyền hỏng: {result.content})")
        return
    print(f"{ten}> {result.content}")


async def _compact_now(
    *,
    summarizer: Any,
    tools: Any,
    session: Session,
    system: str,
) -> None:
    """`/compact`: nén ngay, không đợi ngưỡng. In ra kết quả.

    Đường tự động nén GIỮA turn, ở mỗi step, vì tool result là nguồn phình
    nhanh nhất. Lệnh gõ tay thì ngược lại: nó chỉ gõ được lúc đang đứng ở
    prompt, tức là không có turn nào đang chạy để phải huỷ trước. Codex phải
    huỷ turn đang chạy rồi mới nén vì ở đó `/compact` gửi được giữa chừng.

    In ra số message trước/sau chứ không chỉ "đã nén": người gõ lệnh này đang
    hỏi "còn bao nhiêu chỗ", và một câu "xong" không trả lời được câu đó.
    """
    if session.max_tokens is None:
        print("(không nén được: session không có ngân sách token, "
              "mà vùng giữ nguyên văn tính theo ngân sách)")
        return

    truoc = len(session.to_messages())
    if await maybe_compact(session=session, llm=summarizer, system=system,
                           tools=tools.schemas(), force=True):
        print(f"(đã nén: {truoc} → {len(session.to_messages())} message)")
        return

    # Hỏng thì lý do đã nằm trong log — đọc ra chứ không đoán lại. Không có
    # event nào mới nghĩa là Session nói "không còn gì để nén", chưa gọi model.
    cuoi = session.events[-1] if session.events else {}
    ly_do = (cuoi["content"] if cuoi.get("type") == "compaction_failed"
             else "cả hội thoại vẫn nằm gọn trong vùng giữ nguyên văn, "
                  "chưa có turn nào đủ cũ để tóm tắt")
    print(f"(không nén được: {ly_do})")


async def chat(
    *,
    llm: Any,
    tools: Any,
    session: Session,
    system: str,
    display: TerminalStream,
    summarizer: Any = None,
    read_line: Callable[[], Awaitable[str]] = _default_read_line,
    sub_runs: list[Session] | None = None,
    delegate: Any = None,
) -> int:
    """Vòng ngoài: một lần lặp = một turn. Đây là vòng LỒNG đầu tiên của harness.

    Nó tồn tại vì có một nhu cầu cụ thể: Ctrl-C phải huỷ TURN mà giữ session.
    Không có vòng này thì huỷ turn = chết process, vì turn là tất cả những gì có.

        chat loop   (turn)   <- chỗ này: sở hữu "huỷ nhưng đừng chết"
          run_turn  (step)
            tool calls

    Cách huỷ ở đây khác `main()` một điểm cốt tử: SIGINT huỷ **task của turn**,
    không huỷ task đang chạy vòng chat. Nhờ vậy `except CancelledError` dưới đây
    là bắt một exception bình thường, không phải nuốt lệnh huỷ của chính mình.

    `read_line` nhận được từ ngoài để test drive được vòng này mà không cần gửi
    signal thật.

    `summarizer` là model dùng cho phép nén — xem `run_turn`. Nó đi qua đây chứ
    không được dựng ở đây, vì `cli/` không biết provider nào tồn tại.

    `sub_runs` là list session con do `app.py` sở hữu và truyền vào — `/task`
    chỉ đọc nó để xem lại các lần uỷ quyền, không tự dựng ra session nào.

    `delegate` là một registry chỉ chứa tool `task`, cho lệnh `/task <agent>
    <việc>`. Nó TÁCH khỏi `tools` ở trên vì hai cái trả lời hai câu hỏi khác
    nhau: `tools` là những gì MODEL được phép làm (và `general` thì không được
    uỷ quyền), còn cái này là những gì NGƯỜI DÙNG gõ tay được. `None` nghĩa là
    phiên này không uỷ quyền được — xem `_delegate_now`.
    """
    loop = asyncio.get_running_loop()
    summarizer = summarizer if summarizer is not None else llm
    current: asyncio.Task[Any] | None = None

    def on_sigint() -> None:
        # Đang chạy turn -> huỷ turn. Đang đứng ở prompt -> huỷ luôn cái đọc
        # stdin, và vòng dưới hiểu đó là thoát.
        if current is not None:
            current.cancel()

    loop.add_signal_handler(signal.SIGINT, on_sigint)
    try:
        while True:
            print("\nbạn> ", end="", flush=True)
            current = asyncio.create_task(read_line())
            try:
                line = await current
            except asyncio.CancelledError:
                # Ctrl-C ở prompt: 130 như mọi chương trình bị SIGINT.
                print()
                return 130
            except EOFError:
                # Ctrl-D: thoát bình thường.
                print()
                return 0
            finally:
                current = None

            question = line.strip()
            if question in {"/quit", "/exit"}:
                return 0
            if not question:
                continue
            if question == "/context":
                print_context(session)
                continue
            if question == "/compact":
                # Dùng lại y nguyên cơ chế huỷ của turn: /compact cũng gọi
                # model, nên nó cũng phải Ctrl-C được.
                current = asyncio.create_task(_compact_now(
                    summarizer=summarizer, tools=tools,
                    session=session, system=system,
                ))
                try:
                    await current
                except asyncio.CancelledError:
                    print("(đã huỷ /compact — session vẫn giữ)")
                finally:
                    current = None
                continue

            task_match = _TASK.fullmatch(question)
            if task_match:
                # Phải đứng TRƯỚC `_LENH` bên dưới: `_LENH` khớp bất kỳ
                # `/chữ` nào, kể cả `/task`, nên đặt sau thì nhánh này không
                # bao giờ tới lượt chạy — `/task` bị nuốt thành "lệnh lạ".
                arg = task_match.group(1)
                if arg is None or arg.isdigit():
                    print_tasks(sub_runs or [], arg)
                    continue
                # Còn lại là uỷ quyền, và nó GỌI MODEL — nên dùng lại đúng cơ
                # chế huỷ của turn, như `/compact`. Một lượt sub-agent có thể
                # đi mươi step; không Ctrl-C được thì người gõ chỉ còn cách
                # giết cả process, mất luôn hội thoại của cha.
                current = asyncio.create_task(_delegate_now(delegate, arg))
                try:
                    await current
                except asyncio.CancelledError:
                    # `run_turn` của CON đã tự vá log con xong trước khi nhả
                    # exception. Log cha thì không có gì để vá: lệnh này chưa
                    # bao giờ ghi vào đó.
                    print("(đã huỷ /task — hội thoại của cha không đổi)")
                finally:
                    current = None
                continue

            if _LENH.fullmatch(question):
                # Gõ nhầm tên lệnh thì PHẢI báo, không được lặng lẽ gửi cho
                # model. Đo thật: `/context` lúc chưa có lệnh này đi thẳng vào
                # `run_turn`, model không hiểu nên đi gọi tool rồi trả lời lan
                # man — mất hai lượt API, tốn tiền, và không một dòng nào báo
                # là đã gõ sai.
                #
                # Chỉ bắt dòng CHỈ GỒM một từ dạng `/chữ`: một câu bắt đầu
                # bằng đường dẫn (`/etc/hosts là gì`) vẫn là câu hỏi thật, và
                # chặn nhầm nó thì phiền hơn là im lặng.
                print(f"(không có lệnh {question} — "
                      "chỉ có /context, /compact, /task, /quit)")
                continue

            current = asyncio.create_task(run_turn(
                llm=llm, tools=tools, session=session,
                system=system, user_input=question, summarizer=summarizer,
            ))
            try:
                # Không giữ kết quả: text đã in dần qua `display` lúc stream.
                await current
            except asyncio.CancelledError:
                # run_turn đã vá log XONG trước khi nhả exception, nên session
                # vẫn hợp lệ để đi tiếp. `continue` chứ không `return`: đó là
                # toàn bộ khác biệt giữa "huỷ turn" và "thoát".
                display.close()
                print("(đã huỷ turn — session vẫn giữ)")
                continue
            except MaxStepsExceeded as error:
                # Hết step là hết của MỘT turn, không phải hết của session.
                print(f"(dừng: {error})")
                continue
            except Exception as error:
                # Bắt RỘNG, có chủ ý. Hẹp lại thì file này phải import openai
                # và httpx để gọi tên exception — tức là phá đúng cái seam
                # đang giữ: `cli/` không được biết provider nào tồn tại. Mà
                # hẹp cũng không đủ: lỗi đứt stream là `httpx.RemoteProtocol
                # Error`, không nằm trong cây `openai.APIError`.
                # Đổi lại, in kèm TÊN LỚP để không có lỗi nào chết im lặng.
                # `asyncio.CancelledError` kế thừa BaseException nên không rơi
                # vào đây — Ctrl-C vẫn đi đúng nhánh của nó ở trên.
                display.close()
                print(f"(lỗi {type(error).__name__}: {error} — turn bỏ dở, "
                      f"session vẫn giữ)")
                continue
            finally:
                current = None

            # Text đã in dần trong lúc stream, chỉ cần đóng dòng.
            display.close()
    finally:
        loop.remove_signal_handler(signal.SIGINT)
