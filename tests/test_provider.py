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
        ]
        result = OpenAIProvider._normalize_messages(messages)
        assert result[0]["tool_calls"] == [tc]

    def test_normalize_ignores_non_assistant(self):
        messages = [
            {"role": "user", "content": "hi", "tool_calls": []},
            {"role": "tool", "tool_call_id": "tc_1", "content": "result"},
        ]
        result = OpenAIProvider._normalize_messages(messages)
        assert result == messages
