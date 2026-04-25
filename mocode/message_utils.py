"""消息流修复工具 — 处理 interrupt 导致的消息不一致"""

from __future__ import annotations


def sanitize_messages(messages: list[dict]) -> list[dict]:
    """移除没有对应 tool result 的 orphaned tool_calls。

    返回新列表，不修改原列表。
    """
    # 收集所有有 result 的 tool_call_id
    result_ids = {
        m["tool_call_id"]
        for m in messages
        if m.get("role") == "tool" and m.get("tool_call_id")
    }

    result = []
    for msg in messages:
        if msg.get("role") == "assistant" and "tool_calls" in msg:
            tcs = msg["tool_calls"]
            if not tcs:
                result.append({k: v for k, v in msg.items() if k != "tool_calls"})
                continue
            valid = [tc for tc in tcs if tc.get("id") in result_ids]
            if not valid:
                result.append({k: v for k, v in msg.items() if k != "tool_calls"})
            elif len(valid) < len(tcs):
                cleaned = dict(msg)
                cleaned["tool_calls"] = valid
                result.append(cleaned)
            else:
                result.append(msg)
        else:
            result.append(msg)
    return result


def repair_interrupted_state(
    tool_calls: list[dict],
    collected_results: list[dict],
    interrupted_tc_id: str | None,
) -> tuple[list[dict], list[dict]]:
    """修复中断后的消息状态。

    返回 (trimmed_tool_calls, synthetic_results):
    - trimmed: 只保留有 result（真实或合成）的 tool_calls
    - synthetic: 为被中断的 tool 合成的 result 消息
    """
    collected_ids = {r["tool_call_id"] for r in collected_results}

    trimmed_calls = []
    synthetic_results = []

    for tc in tool_calls:
        tc_id = tc.get("id", "")
        if tc_id in collected_ids:
            trimmed_calls.append(tc)
        elif tc_id == interrupted_tc_id:
            trimmed_calls.append(tc)
            synthetic_results.append({
                "role": "tool",
                "tool_call_id": tc_id,
                "content": "[interrupted]",
            })

    return trimmed_calls, synthetic_results
