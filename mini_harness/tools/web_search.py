"""Tool web_search: tool đầu tiên gọi ra Internet.

Ba thứ `calculator` và `write_file` chưa từng chạm, tool này chạm cả ba:

  1. CREDENTIAL — không có key thì tool không chạy được. Quyết định "thiếu key
     thì sao" nằm ở `app.py` (nơi biết agent nào đang chạy), không ở đây.
  2. LỖI CỦA NGƯỜI KHÁC — mạng rớt, quota hết, 5xx phía Tavily. Khác hẳn lỗi
     của `calculator` (biểu thức sai, lỗi của model) ở chỗ gọi lại y hệt vẫn
     có thể hỏng, nên message phải nói rõ đây là lỗi tạm thời hay vĩnh viễn.
  3. KÍCH THƯỚC OUTPUT — đây mới là phần đáng học. Một trang web thật đo được
     ~25k ký tự raw. Năm kết quả là ~125k ký tự ≈ 30k token, nhét vào MỘT tool
     result, trong khi ngân sách cả phiên ở `app.py` là 60k. Tức là một lần gọi
     tool sai cách đủ ăn hết nửa cửa sổ.

     Nhưng tool KHÔNG tự cắt: nó trả nguyên văn, và `Session.to_messages()` mới
     là chỗ cắt (`max_tool_result_chars`). Hai lý do. Một, cắt ở đây là cắt
     TRƯỚC khi vào log, tức là huỷ vĩnh viễn bản gốc — mất luôn khả năng đọc
     lại và replay. Hai, trần phụ thuộc ngân sách của cả phiên và số tool đang
     tranh chỗ, mà tool thì không biết cả hai. Cùng một luật đã áp cho
     compaction: cắt ở phép chiếu, log giữ nguyên.

     Cái tool VẪN sở hữu là `fetch_content` — quyền chọn gọi to hay gọi nhỏ.
     Trần là lưới an toàn, không phải cơ chế chính.

Đối chiếu: research_agent/tools/research.py của Research-Agents (LangChain).
CỐ TÌNH không bê theo `BaseToolkit` của repo đó — đó là kế thừa, đúng cái pattern
`core/agent.py` đã từ chối; ở đây một tool vẫn chỉ là một hàm trả ToolDefinition.
"""

from __future__ import annotations

from typing import Any

import httpx

from mini_harness.tools.registry import ToolDefinition, define_tool

_ENDPOINT = "https://api.tavily.com/search"

# Timeout của cả request. Không có nó thì một lần mạng treo = treo luôn cả turn,
# và user chỉ còn cách Ctrl-C.
_TIMEOUT_SECONDS = 25.0


def _format(query: str, results: list[dict[str, Any]], fetch_content: bool) -> str:
    """Đổi response JSON thành text cho model đọc.

    Hàm thuần, tách riêng khỏi phần gọi mạng: đây là chỗ chứa toàn bộ quyết định
    "gửi gì cho model", nên nó phải test được mà không cần API key.

    Không tìm thấy gì KHÔNG phải lỗi — là một kết quả hợp lệ, và nói thẳng ra thì
    model biết đổi từ khoá thay vì thử lại y hệt.
    """
    if not results:
        return f'No results for "{query}". Try different or broader search terms.'

    blocks = []
    for result in results:
        if fetch_content:
            # `raw_content` có thể là null khi Tavily không tải được trang —
            # rơi về snippet còn hơn trả một khối rỗng không giải thích gì.
            body = result.get("raw_content") or result.get("content") or ""
        else:
            body = result.get("content") or ""
        blocks.append(
            f"## {result.get('title') or '(no title)'}\n"
            f"URL: {result.get('url') or '(no url)'}\n\n"
            f"{body or '(no content)'}"
        )
    return f'{len(blocks)} result(s) for "{query}":\n\n' + "\n\n---\n\n".join(blocks)


def web_search_tool(api_key: str) -> ToolDefinition:
    """Khai báo tool, với API key nhận TỪ NGOÀI.

    Cùng lý do với `sandbox` của `write_file_tool`: tool không tự đi đọc
    `os.environ`. Chỗ biết credential lấy từ đâu là wiring (`app.py`) — giữ như
    vậy thì test dựng được tool bằng key giả, và không file tool nào trở thành
    chỗ thứ hai biết về biến môi trường.
    """

    async def search(args: dict[str, Any]) -> str:
        fetch_content = bool(args.get("fetch_content", False))
        payload = {
            "query": args["query"],
            "max_results": args.get("max_results", 5),
            "include_raw_content": fetch_content,
        }
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
                # Key đi ở HEADER, không ở body. Tavily nhận cả hai, nhưng body
                # là thứ hay bị log/echo lại trong thông báo lỗi nhất.
                response = await client.post(
                    _ENDPOINT,
                    headers={"Authorization": f"Bearer {api_key}"},
                    json=payload,
                )
        except httpx.TimeoutException as error:
            raise ValueError(
                f"search timed out after {_TIMEOUT_SECONDS:.0f}s; "
                "try again, or narrow the query"
            ) from error
        except httpx.HTTPError as error:
            raise ValueError(f"could not reach the search service: {error}") from error

        # Phân biệt hai lớp lỗi vì model phải xử lý khác nhau: 401/403/429 là
        # chuyện của người vận hành, thử lại vô ích; còn lại thì có thể tạm thời.
        if response.status_code in (401, 403):
            raise ValueError(
                "the search service rejected our credentials; "
                "web search is unavailable this session, do not retry"
            )
        if response.status_code == 429:
            raise ValueError("search quota exceeded; do not retry this session")
        if response.status_code >= 400:
            raise ValueError(
                f"the search service returned HTTP {response.status_code}; "
                "this may be temporary"
            )

        return _format(args["query"], response.json().get("results", []), fetch_content)

    return define_tool(
        name="web_search",
        description=(
            "Search the web and return result snippets, or full page text. "
            "Use this for anything you are not certain about from memory."
        ),
        parameters={
            "query": {
                "type": "string",
                "required": True,
                "description": "What to search for, as a short natural-language query.",
            },
            "max_results": {
                "type": "integer",
                "description": "How many results to return. Defaults to 5.",
            },
            # Cờ này là một quyết định NGÂN SÁCH được phơi ra cho model tự chọn,
            # và description phải nói đúng cái giá của nó — đặt tên suông thì
            # model sẽ bật nó mọi lúc. Ý tưởng mượn từ `fetch_content` của
            # Research-Agents, chỗ đó thiết kế đúng.
            "fetch_content": {
                "type": "boolean",
                "description": (
                    "If true, return the full text of each page instead of a "
                    "short snippet. This costs far more context, so leave it "
                    "false for a first look and only set it true for a result "
                    "you already have reason to read in full."
                ),
            },
        },
        execute=search,
    )
