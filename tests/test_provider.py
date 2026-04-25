"""Provider tests — _normalize_messages"""

from mocode.provider import OpenAIProvider


class TestNormalizeMessages:
    def test_normalize_strips_empty_tool_calls(self):
        messages = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello", "tool_calls": []},
        ]
        result = OpenAIProvider._normalize_messages(messages)
        assert result[1] == {"role": "assistant", "content": "hello"}
        assert "tool_calls" not in result[1]

    def test_normalize_keeps_valid_tool_calls(self):
        tc = {"id": "tc_1", "type": "function", "function": {"name": "echo", "arguments": "{}"}}
        messages = [
            {"role": "assistant", "content": "", "tool_calls": [tc]},
            {"role": "tool", "tool_call_id": "tc_1", "content": "result"},
        ]
        result = OpenAIProvider._normalize_messages(messages)
        assert result[0]["tool_calls"] == [tc]

    def test_normalize_strips_orphaned_tool_calls(self):
        tc1 = {"id": "tc_1", "type": "function", "function": {"name": "a", "arguments": "{}"}}
        tc2 = {"id": "tc_2", "type": "function", "function": {"name": "b", "arguments": "{}"}}
        messages = [
            {"role": "assistant", "content": "", "tool_calls": [tc1, tc2]},
            {"role": "tool", "tool_call_id": "tc_1", "content": "ok"},
        ]
        result = OpenAIProvider._normalize_messages(messages)
        assert len(result[0]["tool_calls"]) == 1
        assert result[0]["tool_calls"][0]["id"] == "tc_1"

    def test_normalize_removes_all_orphaned_tool_calls(self):
        tc = {"id": "tc_1", "type": "function", "function": {"name": "echo", "arguments": "{}"}}
        messages = [
            {"role": "assistant", "content": "thinking", "tool_calls": [tc]},
        ]
        result = OpenAIProvider._normalize_messages(messages)
        assert "tool_calls" not in result[0]

    def test_normalize_ignores_non_assistant(self):
        messages = [
            {"role": "user", "content": "hi", "tool_calls": []},
            {"role": "tool", "tool_call_id": "tc_1", "content": "result"},
        ]
        result = OpenAIProvider._normalize_messages(messages)
        assert result == messages
