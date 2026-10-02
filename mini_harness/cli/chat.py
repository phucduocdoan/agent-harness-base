"""Vòng chat và phần hiển thị của nó.

Đây là chỗ sở hữu VIỆC HIỂN THỊ. `core/loop.py` không in gì cả — nó không biết
có màn hình. Cái nối hai thứ lại là `Session.on_event`: UI nghe ở đúng commit
point của session, nên mọi thứ user thấy đều là thứ đã thật sự vào log.
"""

from __future__ import annotations

import asyncio
import signal
from collections.abc import Awaitable, Callable
from typing import Any

from mini_harness.cli.terminal import TerminalStream, read_line as _default_read_line
from mini_harness.core.compaction import maybe_compact
from mini_harness.core.loop import MaxStepsExceeded, run_turn
from mini_harness.core.session import Session


def echo_tool_activity(display: TerminalStream) -> Callable[[dict[str, Any]], None]:
    """Listener của Session: cho user thấy tool nào vừa chạy, ngay lúc nó chạy.

    KHÔNG phải debug output. Một step chỉ gọi tool thì `reply.text` rỗng, tức là
    không có gì để stream ra — không có listener này thì màn hình im lặng trong
    khi agent thay bạn làm việc, và đó là điều user cần thấy nhất.

    Args:
        display: stream đang in text của model, để đóng dòng dở trước khi chèn
            một dòng tool vào giữa.
    """
    def echo(event: dict[str, Any]) -> None:
        display.close()
        for call in event.get("tool_calls", ()):
            print(f"  → {call['name']}({call['arguments']})")
        if event["type"] == "tool_result":
            # Một dòng đầu là đủ để biết chạy được hay không; nội dung đầy đủ
            # nằm trong log. Đây là chỗ duy nhất `is_error` được dùng để
            # HIỂN THỊ — nó vẫn không bao giờ lên wire.
            head = event["content"].splitlines()[0] if event["content"] else ""
            mark = "✗" if event.get("is_error") else "←"
            print(f"  {mark} {head[:160]}")
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
    # In cả số API ĐO được bên cạnh số message: đó là cách duy nhất nhìn ra bộ
    # đoán trong session lệch bao nhiêu so với thực tế.
    measured = [
        event["prompt_tokens"]
        for event in session.events
        if event["type"] == "assistant" and event.get("prompt_tokens")
    ]
    do_duoc = f", request cuối {measured[-1]} token (API đo)" if measured else ""
    print(f"\n--- to_messages() gửi model: {len(session.to_messages())} message{do_duoc} ---")


async def _compact_now(
    *,
    llm: Any,
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
    if await maybe_compact(session=session, llm=llm, system=system,
                           tools=tools.schemas(), force=True):
        print(f"(đã nén: {truoc} → {len(session.to_messages())} message)")
        return

    # Hỏng thì lý do đã nằm trong log — đọc ra chứ không đoán lại. Không có
    # event nào mới nghĩa là Session nói "không còn gì để nén", chưa gọi model.
    cuoi = session.events[-1] if session.events else {}
    ly_do = (cuoi["content"] if cuoi.get("type") == "compaction_failed"
             else "không còn turn cũ nào để nén")
    print(f"(không nén được: {ly_do})")


async def chat(
    *,
    llm: Any,
    tools: Any,
    session: Session,
    system: str,
    display: TerminalStream,
    read_line: Callable[[], Awaitable[str]] = _default_read_line,
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
    """
    loop = asyncio.get_running_loop()
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
            if question == "/compact":
                # Dùng lại y nguyên cơ chế huỷ của turn: /compact cũng gọi
                # model, nên nó cũng phải Ctrl-C được.
                current = asyncio.create_task(_compact_now(
                    llm=llm, tools=tools, session=session, system=system,
                ))
                try:
                    await current
                except asyncio.CancelledError:
                    print("(đã huỷ /compact — session vẫn giữ)")
                finally:
                    current = None
                continue

            current = asyncio.create_task(run_turn(
                llm=llm, tools=tools, session=session,
                system=system, user_input=question,
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
