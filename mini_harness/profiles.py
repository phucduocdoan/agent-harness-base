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

PROFILES = {profile.name: profile for profile in (GENERAL, TUTOR)}
DEFAULT_AGENT = GENERAL.name
