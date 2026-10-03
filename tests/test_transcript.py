"""The transcript helpers read and write the one message shape.

History, export, replay and the provider protocol all speak these message
dicts — the shape itself is a contract, and a reader that crashes on a
malformed history breaks every presentation at once.
"""

from __future__ import annotations

from mocode.core.transcript import (
    IMAGE_PLACEHOLDER,
    answered_call_id,
    assistant_message,
    content_parts,
    reasoning_of,
    text_of,
    tool_call_args,
    tool_call_by_id,
    tool_call_dicts,
    tool_call_id,
    tool_call_name,
    tool_calls_of,
    tool_result,
)
from mocode.core.provider import ToolCall


class TestConstruction:
    """The constructors: empty fields stay absent, not empty."""

    def test_a_bare_assistant_message_is_two_fields(self):
        assert assistant_message("hi") == {"role": "assistant", "content": "hi"}

    def test_reasoning_and_calls_are_attached_only_when_present(self):
        msg = assistant_message(
            "hi", reasoning="thinking", tool_calls=tool_call_dicts(
                [ToolCall(id="c1", name="bash", arguments="{}")]
            ),
        )
        assert msg["reasoning_content"] == "thinking"
        assert msg["tool_calls"] == [
            {
                "id": "c1",
                "type": "function",
                "function": {"name": "bash", "arguments": "{}"},
            }
        ]
        assert "reasoning_content" not in assistant_message("hi")

    def test_a_tool_result_is_keyed_by_the_call_it_answers(self):
        assert tool_result("c1", "out") == {
            "role": "tool",
            "tool_call_id": "c1",
            "content": "out",
        }


class TestContent:
    """content_parts / text_of / reasoning_of on plain and multimodal content."""

    def test_a_plain_string_is_one_piece(self):
        assert content_parts("hello") == ["hello"]

    def test_text_parts_and_images_read_as_their_placeholders(self):
        content = [
            {"type": "text", "text": "first"},
            {"type": "image_url", "image_url": {"url": "http://x/y.png"}},
            "second",
            {"type": "file", "file": {"id": "x"}},
        ]
        assert content_parts(content) == ["first", IMAGE_PLACEHOLDER, "second"]

    def test_a_part_a_reader_does_not_understand_is_dropped(self):
        assert content_parts([{"type": "file"}, 42, None]) == []

    def test_content_none_or_a_number_contributes_nothing_strange(self):
        assert content_parts(None) == []
        assert content_parts(7) == ["7"]

    def test_text_of_joins_with_the_separator_the_caller_chose(self):
        msg = {"role": "user", "content": ["a", IMAGE_PLACEHOLDER, "b"]}
        assert text_of(msg) == f"a {IMAGE_PLACEHOLDER} b"
        assert text_of(msg, sep="\n") == f"a\n{IMAGE_PLACEHOLDER}\nb"

    def test_reasoning_is_the_reasoning_content_or_empty(self):
        assert reasoning_of({"role": "assistant", "content": ""}) == ""
        assert reasoning_of(
            {"role": "assistant", "content": "", "reasoning_content": "why"}
        ) == "why"


class TestToolCallArgs:
    """tool_call_args must never crash a reader on a malformed history."""

    def test_a_json_string_is_parsed_and_an_object_is_kept(self):
        call = {"id": "c1", "function": {"name": "bash", "arguments": '{"a": 1}'}}
        assert tool_call_args(call) == {"a": 1}
        kept = {"id": "c1", "function": {"name": "bash", "arguments": {"a": 1}}}
        assert tool_call_args(kept) == {"a": 1}

    def test_unparseable_or_non_object_arguments_mean_no_arguments(self):
        bad = {"id": "c1", "function": {"name": "bash", "arguments": "{not json"}}
        assert tool_call_args(bad) == {}
        not_object = {"id": "c1", "function": {"name": "bash", "arguments": "[1, 2]"}}
        assert tool_call_args(not_object) == {}

    def test_missing_arguments_mean_no_arguments(self):
        assert tool_call_args({"id": "c1", "function": {"name": "bash"}}) == {}


class TestLookups:
    """answered_call_id and tool_call_by_id — the matching semantics."""

    def test_an_orphaned_result_names_no_call(self):
        assert answered_call_id({"role": "tool", "content": "x"}) == ""

    def test_a_result_names_the_call_it_answers(self):
        assert answered_call_id(tool_result("c7", "out")) == "c7"

    def test_a_call_is_found_anywhere_in_the_history(self):
        messages = [
            {"role": "user", "content": "go"},
            assistant_message("", tool_calls=tool_call_dicts(
                [ToolCall(id="c1", name="bash", arguments="{}")]
            )),
            tool_result("c1", "done"),
        ]
        found = tool_call_by_id(messages, "c1")
        assert found is not None
        assert tool_call_name(found) == "bash"
        assert tool_call_name({"function": {}}, "unnamed") == "unnamed"
        assert tool_call_id(found) == "c1"
        assert tool_call_by_id([assistant_message("plain")], "c1") is None
        assert tool_call_by_id([tool_result("c1", "x")], "c1") is None


    def test_calls_are_listed_oldest_first_only_for_an_assistant(self):
        calls = [{"id": "a", "function": {"name": "x", "arguments": "{}"}}]
        assert tool_calls_of(assistant_message("", tool_calls=calls)) == calls
        assert tool_calls_of({"role": "user", "content": "go"}) == []
