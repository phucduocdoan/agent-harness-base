"""Test cho phần streaming của mini_harness/llm/stream.py.

Không có network ở đây: delta là dữ liệu thuần, nên accumulator test được
hoàn toàn offline. Đó cũng là lý do tách nó ra khỏi client.
"""

from __future__ import annotations

from types import SimpleNamespace

from mini_harness.llm.stream import StreamAccumulator


# ------------------------------------------------------------------- fixtures
# Dựng lại đúng hình dạng chunk của openai SDK, chỉ những field accumulator đọc.


def _chunk(*, text: str | None = None, calls: list[SimpleNamespace] | None = None):
    delta = SimpleNamespace(content=text, tool_calls=calls)
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta)])


def _call(index: int, *, id: str | None = None, name: str | None = None, args: str | None = None):
    return SimpleNamespace(
        index=index, id=id, function=SimpleNamespace(name=name, arguments=args)
    )


def _fold(chunks, on_text=None):
    accumulator = StreamAccumulator(on_text)
    for chunk in chunks:
        accumulator.feed(chunk)
    return accumulator.finish()


# ----------------------------------------------------------------------- text


def test_text_deltas_concatenate_in_order() -> None:
    message = _fold([_chunk(text="100"), _chunk(text=" * 1.1"), _chunk(text=" = 110")])
    assert message.text == "100 * 1.1 = 110"
    assert message.tool_calls == ()


def test_on_text_sees_every_fragment_as_it_arrives() -> None:
    seen: list[str] = []
    _fold([_chunk(text="a"), _chunk(text="b")], on_text=seen.append)
    assert seen == ["a", "b"]


def test_on_text_is_not_called_for_tool_call_only_stream() -> None:
    seen: list[str] = []
    message = _fold([_chunk(calls=[_call(0, id="c1", name="calculator", args="{}")])], on_text=seen.append)
    assert seen == []
    assert message.text == ""


# ----------------------------------------------------------------- tool calls


def test_tool_call_is_assembled_from_fragments() -> None:
    """`id`/`name` chỉ tới ở delta đầu; các delta sau chỉ có mảnh arguments."""
    message = _fold([
        _chunk(calls=[_call(0, id="call_1", name="calculator", args='{"expr')]),
        _chunk(calls=[_call(0, args='ession": "1')]),
        _chunk(calls=[_call(0, args='00*1.1"}')]),
    ])
    assert len(message.tool_calls) == 1
    (call,) = message.tool_calls
    assert (call.id, call.name) == ("call_1", "calculator")
    assert call.arguments_json == '{"expression": "100*1.1"}'


def test_parallel_tool_calls_are_keyed_by_index_not_by_arrival() -> None:
    # Delta của hai call xen kẽ nhau — nếu khoá sai thì arguments trộn lẫn.
    message = _fold([
        _chunk(calls=[_call(0, id="c0", name="a", args='{"x":')]),
        _chunk(calls=[_call(1, id="c1", name="b", args='{"y":')]),
        _chunk(calls=[_call(1, args="2}")]),
        _chunk(calls=[_call(0, args="1}")]),
    ])
    assert [(c.id, c.name, c.arguments_json) for c in message.tool_calls] == [
        ("c0", "a", '{"x":1}'),
        ("c1", "b", '{"y":2}'),
    ]


def test_broken_json_survives_as_a_raw_string() -> None:
    """Accumulator KHÔNG parse. JSON hỏng là lỗi model tự sửa qua registry."""
    message = _fold([_chunk(calls=[_call(0, id="c", name="t", args='{"a": ')])])
    assert message.tool_calls[0].arguments_json == '{"a": '


# --------------------------------------------------------------- chunk lạ


def test_chunk_without_choices_is_ignored() -> None:
    # Azure gửi một chunk đầu chỉ chứa content filter của prompt.
    message = _fold([SimpleNamespace(choices=[]), _chunk(text="ok")])
    assert message.text == "ok"


def test_empty_string_content_does_not_break_accumulation() -> None:
    message = _fold([_chunk(text=""), _chunk(text="x"), _chunk(text=None)])
    assert message.text == "x"
