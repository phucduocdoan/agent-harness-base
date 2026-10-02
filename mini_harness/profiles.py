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

# Tool mọi agent mặc định cầm. `task` nằm đây chứ không trong persona của một
# profile riêng, vì uỷ quyền là HẠ TẦNG chứ không phải tính cách: bắt chọn đúng
# một profile-biết-giao-việc lúc khởi động là bắt người dùng trả lời "lát nữa
# tôi có cần giao việc không" TRƯỚC khi hội thoại bắt đầu — mà nhu cầu đó chỉ
# lộ ra giữa chừng.
BASE_TOOLS = ("calculator", "write_file", "task")

GENERAL = AgentProfile(
    name="general",
    persona=(
        "You are a helpful assistant. Use the calculator tool for any "
        "arithmetic instead of computing it yourself. Answer in Vietnamese."
    ),
    tools=BASE_TOOLS,
)

# Tutor KHÔNG có tool nào, và đó chính là nội dung của thiết kế chứ không phải
# thiếu sót: một gia sư cầm calculator sẽ tự bấm ra đáp án, đúng cái việc học
# trò cần tự làm. Ở đây "agent là gì" được quyết định bằng cái nó KHÔNG có,
# nhiều ngang cái nó có.
#
# Tutor cầm `task` còn tệ hơn tutor cầm calculator: calculator chỉ ra đáp số,
# còn `task` giao thẳng CẢ CÂU HỎI cho `general` rồi đọc nguyên văn kết quả về
# — không còn một bước tính nào lộ ra để học trò nhìn theo, vì học trò thậm chí
# không thấy có phép tính nào xảy ra. Vì vậy TUTOR không kế thừa `BASE_TOOLS`:
# đây là một opt-out CÓ CHỦ Ý khỏi tập tool mặc định, không phải một profile bị
# bỏ quên khi `BASE_TOOLS` được thêm vào.
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
    tools=BASE_TOOLS + ("web_search", "read_spill"),
)

# Không còn một profile riêng cho "agent biết giao việc" nữa: giờ `task` nằm
# trong `BASE_TOOLS` nên MỌI agent đều biết giao việc — không còn gì để một
# profile riêng tách ra. Độ sâu của cây uỷ quyền giờ bị chặn bằng CẤU TRÚC ở
# `app.py` (con không bao giờ nhận `task`), không phải bằng một danh sách
# profile được phép làm cha như trước đây.
PROFILES = {
    profile.name: profile for profile in (GENERAL, TUTOR, RESEARCH)
}
DEFAULT_AGENT = GENERAL.name
