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
#
# Và persona phải nói hết những gì agent này CẦM, không chỉ việc chính nó làm.
# Đo được bằng chạy thật: persona cũ chỉ mô tả vai trò tra-cứu-rồi-trả-lời mà
# không nhắc `write_file`, nên khi được giao việc ghi file, model trả lời "tôi
# chỉ có quyền truy vấn thông tin mà không có quyền ghi file trực tiếp" — sai,
# tool nằm ngay trong tay nó. Model thấy schema qua tham số `tools` của API,
# nhưng prose trong persona vẫn thắng schema. Một agent tự khai sai năng lực
# của chính nó còn tệ hơn một agent thiếu tool: người dùng không có cách nào
# biết là nó nhầm.
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
        "You can also save a finished write-up with write_file when you are "
        "asked to. Answer in Vietnamese."
    ),
    # `read_spill` chỉ ở ĐÂY, không ở general: nó chỉ có nghĩa khi agent có một
    # tool thật sự sinh ra kết quả quá khổ. calculator và write_file trả vài
    # dòng, không bao giờ chạm trần cắt — cấp read_spill cho general là thêm
    # một schema model không bao giờ dùng vào mọi request.
    tools=("web_search", "write_file", "read_spill"),
)

# Lead là agent đầu tiên có một tool gọi lại chính harness: `task` chạy hẳn một
# agent khác trong một session khác. Persona vì thế phải dạy ĐÚNG LÚC NÀO nên
# uỷ quyền, không phải dạy cách gọi tool — cái sai đắt nhất ở đây không phải gọi
# sai cú pháp mà là uỷ quyền một việc lẽ ra tự trả lời được, hoặc viết câu giao
# việc thiếu ngữ cảnh cho một agent không đọc được hội thoại này.
LEAD = AgentProfile(
    name="lead",
    persona=(
        "You are a lead assistant who decides what to do yourself and what to "
        "hand off. You have sub-agents available through the task tool. A "
        "sub-agent runs in its own context window: it sees nothing of this "
        "conversation, it cannot ask you anything, and it returns one final "
        "answer. So hand off work whose intermediate steps you do not need to "
        "see — searching the web, reading long pages — and write the task as a "
        "standalone brief that names every fact the sub-agent needs. Do not "
        "hand off what you can answer directly: every delegation is a full "
        "model run. When an answer comes back it is the sub-agent's work, not "
        "yours: say where it came from, and say plainly if it does not "
        "actually answer the question. Answer in Vietnamese."
    ),
    tools=("calculator", "task"),
)

# Ai được uỷ quyền. Đây là chỗ ĐỘ SÂU bị chặn, và chặn bằng DỮ LIỆU chứ không
# bằng một biến đếm chạy lúc runtime: profile nào có tên trong đây mà lại cầm
# `task` thì mới có cháu, nên chỉ cần nhìn hai dòng này cạnh nhau là biết cây
# sâu tới đâu. `app.py` kiểm lại điều kiện đó lúc khởi động.
LEAD_DELEGATES = ("research",)

PROFILES = {
    profile.name: profile for profile in (GENERAL, TUTOR, RESEARCH, LEAD)
}
DEFAULT_AGENT = GENERAL.name
