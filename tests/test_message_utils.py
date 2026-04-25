"""Tests for message_utils — sanitize and repair"""

from mocode.message_utils import sanitize_messages, repair_interrupted_state


class TestSanitizeMessages:
    def test_preserves_valid_messages(self):
        messages = [
            {"role": "user", "content": "go"},
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "tc_1", "type": "function", "function": {"name": "a", "arguments": "{}"}},
            ]},
            {"role": "tool", "tool_call_id": "tc_1", "content": "result"},
            {"role": "assistant", "content": "done"},
        ]
        result = sanitize_messages(messages)
        assert result == messages

    def test_removes_orphaned_tool_calls(self):
        messages = [
            {"role": "user", "content": "go"},
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "tc_1", "type": "function", "function": {"name": "a", "arguments": "{}"}},
                {"id": "tc_2", "type": "function", "function": {"name": "b", "arguments": "{}"}},
                {"id": "tc_3", "type": "function", "function": {"name": "c", "arguments": "{}"}},
            ]},
            {"role": "tool", "tool_call_id": "tc_1", "content": "result"},
        ]
        result = sanitize_messages(messages)
        assistant = [m for m in result if m["role"] == "assistant" and "tool_calls" in m][0]
        assert len(assistant["tool_calls"]) == 1
        assert assistant["tool_calls"][0]["id"] == "tc_1"

    def test_removes_empty_tool_calls_key(self):
        messages = [
            {"role": "user", "content": "go"},
            {"role": "assistant", "content": "thinking", "tool_calls": [
                {"id": "tc_1", "type": "function", "function": {"name": "a", "arguments": "{}"}},
            ]},
            # No tool result at all
        ]
        result = sanitize_messages(messages)
        assistant = [m for m in result if m["role"] == "assistant"][0]
        assert "tool_calls" not in assistant
        assert assistant["content"] == "thinking"

    def test_strips_empty_tool_calls_list(self):
        messages = [
            {"role": "assistant", "content": "hi", "tool_calls": []},
        ]
        result = sanitize_messages(messages)
        assert "tool_calls" not in result[0]

    def test_does_not_mutate_input(self):
        messages = [
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "tc_1", "type": "function", "function": {"name": "a", "arguments": "{}"}},
            ]},
        ]
        result = sanitize_messages(messages)
        assert len(messages[0]["tool_calls"]) == 1
        assert "tool_calls" not in result[0]


class TestRepairInterruptedState:
    def test_no_interrupt(self):
        tool_calls = [
            {"id": "tc_1", "type": "function", "function": {"name": "a", "arguments": "{}"}},
        ]
        results = [{"role": "tool", "tool_call_id": "tc_1", "content": "ok"}]
        trimmed, synthetic = repair_interrupted_state(tool_calls, results, None)
        assert trimmed == tool_calls
        assert synthetic == []

    def test_interrupted_tool_gets_synthetic_result(self):
        tool_calls = [
            {"id": "tc_1", "type": "function", "function": {"name": "a", "arguments": "{}"}},
            {"id": "tc_2", "type": "function", "function": {"name": "b", "arguments": "{}"}},
            {"id": "tc_3", "type": "function", "function": {"name": "c", "arguments": "{}"}},
        ]
        results = [{"role": "tool", "tool_call_id": "tc_1", "content": "ok"}]
        trimmed, synthetic = repair_interrupted_state(tool_calls, results, "tc_2")
        # tc_1 has result, tc_2 is interrupted → both kept; tc_3 never started → dropped
        assert len(trimmed) == 2
        assert trimmed[0]["id"] == "tc_1"
        assert trimmed[1]["id"] == "tc_2"
        assert len(synthetic) == 1
        assert synthetic[0]["tool_call_id"] == "tc_2"
        assert synthetic[0]["content"] == "[interrupted]"

    def test_interrupt_before_any_tool(self):
        tool_calls = [
            {"id": "tc_1", "type": "function", "function": {"name": "a", "arguments": "{}"}},
        ]
        trimmed, synthetic = repair_interrupted_state(tool_calls, [], None)
        assert trimmed == []
        assert synthetic == []
