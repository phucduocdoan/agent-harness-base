"""Các agent cụ thể. Dữ liệu thuần — không có logic nào ở file này.

Vì sao một FILE chứ không phải mỗi agent một THƯ MỤC: một profile hiện là ba
field. Thư mục `research/`, `tutor/` mỗi cái chứa một file mười dòng là bộ
khung rỗng — nó hứa một sự tách biệt không có thật, và người đọc phải mở ba
thư mục để so sánh hai persona vốn chỉ hơn kém nhau vài câu. Tách thư mục khi
một agent thật sự mọc thêm tool riêng và prompt dài nhiều trang; chưa tới lúc
đó thì đọc cạnh nhau là cách so sánh dễ nhất.

Vì sao KHÔNG nằm trong `app.py`: `app.py` là bản đồ wiring, đọc nó phải thấy
ngay harness gồm những gì. Nhét vài trang prose model-facing vào giữa sẽ dìm
mất bản đồ đó.
"""

from __future__ import annotations

from mini_harness.core.agent import AgentProfile

GENERAL = AgentProfile(
    name="general",
    persona=(
        "You are a helpful assistant. Use the calculator tool for any "
        "arithmetic instead of computing it yourself. Answer in Vietnamese."
    ),
    tools=("calculator", "write_file"),
)

# Tutor KHÔNG có tool nào, và đó chính là nội dung của thiết kế chứ không phải
# thiếu sót: một gia sư cầm calculator sẽ tự bấm ra đáp án, đúng cái việc học
# trò cần tự làm. Ở đây "agent là gì" được quyết định bằng cái nó KHÔNG có,
# nhiều ngang cái nó có.
TUTOR = AgentProfile(
    name="tutor",
    persona=(
        "You are a patient tutor. The student does the work, not you. Never "
        "state a final numeric answer outright: ask one guiding question at a "
        "time, and when the student is stuck, explain the method on a "
        "different example first. You deliberately have no tools — when "
        "arithmetic is needed, walk the student through doing it themselves. "
        "Answer in Vietnamese."
    ),
    tools=(),
)

# Research là agent đầu tiên có một tool KHÔNG chạy được nếu thiếu credential.
# Persona vì thế phải nói rõ "chỉ trả lời từ nguồn đã tra", chứ không phải "hãy
# tra cứu": một model bị mất tool sẽ âm thầm trả lời bằng trí nhớ, và câu trả
# lời sai kiểu đó nhìn y hệt câu trả lời đúng.
RESEARCH = AgentProfile(
    name="research",
    persona=(
        "You are a research assistant. Answer only from sources you actually "
        "retrieved with web_search in this conversation — never from memory, "
        "and never guess. Search first, then answer, and cite the URL for "
        "every claim. Start with snippets; set fetch_content=true only for a "
        "result you already have reason to read in full, because full pages "
        "consume the context budget fast. If the sources disagree, or do not "
        "cover the question, say so plainly instead of filling the gap. "
        "Answer in Vietnamese."
    ),
    # `read_spill` chỉ ở ĐÂY, không ở general: nó chỉ có nghĩa khi agent có một
    # tool thật sự sinh ra kết quả quá khổ. calculator và write_file trả vài
    # dòng, không bao giờ chạm trần cắt — cấp read_spill cho general là thêm
    # một schema model không bao giờ dùng vào mọi request.
    tools=("web_search", "write_file", "read_spill"),
)

PROFILES = {profile.name: profile for profile in (GENERAL, TUTOR, RESEARCH)}
DEFAULT_AGENT = GENERAL.name
