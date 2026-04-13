"""上下文压缩 - 自动压缩旧对话以节省 token"""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import CompactConfig
    from .events import EventBus
    from ..providers.openai import AsyncOpenAIProvider

logger = logging.getLogger(__name__)

# 默认上下文窗口大小（token 数）
DEFAULT_CONTEXT_WINDOW = 128_000

SUMMARY_SYSTEM_PROMPT = """\
You are a conversation compression assistant. Compress the following coding assistant conversation history into a structured summary.

## Compression Principles
- Preserve all factual information; discard pleasantries and repetition
- Specific over vague: keep file paths, function names, variable names, error messages rather than vague descriptions
- Each entry should clearly state "what was done" and "why" in one sentence

## Output Format (strictly follow this structure)

[User Requirements]
What the user asked for — a brief description of the original requirements.

[Completed Work]
List each completed item from the conversation:
- What was done specifically (which files, functions, modules were involved) + key decision rationale
- Errors encountered and their solutions
- Technical preferences explicitly stated by the user

[Current State]
- What was being worked on last, and progress so far
- Key state of the code/project (modified files, architectural changes, uncommitted changes, etc.)

[Pending Items]
- Work not yet started or not yet completed"""


class CompactManager:
    """管理上下文压缩"""

    def __init__(
        self,
        compact_config: "CompactConfig",
        provider: "AsyncOpenAIProvider",
        event_bus: "EventBus | None" = None,
    ):
        self._config = compact_config
        self._provider = provider
        self._event_bus = event_bus
        self._last_prompt_tokens: int = 0

    # ---- Token tracking ----

    def update_usage(self, prompt_tokens: int) -> None:
        """更新 token 使用量"""
        self._last_prompt_tokens = prompt_tokens

    def should_compact(self, model: str) -> bool:
        """判断是否需要压缩"""
        if not self._config.enabled:
            return False
        if self._last_prompt_tokens == 0:
            return False
        context_window = self.get_context_window(model)
        threshold = context_window * self._config.threshold
        return self._last_prompt_tokens > threshold

    def get_context_window(self, model: str) -> int:
        """获取模型的上下文窗口大小"""
        return self._config.context_windows.get(model, DEFAULT_CONTEXT_WINDOW)

    @property
    def last_prompt_tokens(self) -> int:
        return self._last_prompt_tokens

    @staticmethod
    def _find_turn_starts(messages: list[dict]) -> list[int]:
        """找到每个 turn 的起始索引（user 消息的位置）"""
        return [i for i, msg in enumerate(messages) if msg.get("role") == "user"]

    @staticmethod
    def _strip_tool_messages(messages: list[dict]) -> list[dict]:
        """去除 tool 消息和带 tool_calls 的 assistant 消息"""
        return [
            msg for msg in messages
            if msg.get("role") != "tool"
            and not (msg.get("role") == "assistant" and msg.get("tool_calls"))
        ]

    # ---- Formatting ----

    @staticmethod
    def _format_messages_for_summary(messages: list[dict]) -> str:
        """将消息列表格式化为可读文本用于摘要"""
        parts = []
        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")

            if role == "user":
                if isinstance(content, list):
                    # 多模态内容
                    text_parts = []
                    for part in content:
                        if isinstance(part, dict):
                            if part.get("type") == "text":
                                text_parts.append(part.get("text", ""))
                            elif part.get("type") == "image_url":
                                text_parts.append("[image attached]")
                            else:
                                text_parts.append("[attachment]")
                        else:
                            text_parts.append(str(part))
                    content = " ".join(text_parts)
                parts.append(f"[User] {content}")

            elif role == "assistant":
                text = content or ""
                tool_calls = msg.get("tool_calls", [])
                if tool_calls:
                    for tc in tool_calls:
                        if isinstance(tc, dict):
                            fn = tc.get("function", {})
                            name = fn.get("name", "unknown")
                            args = fn.get("arguments", "")
                            text += f"\n[Tool Call: {name}({args})]"
                parts.append(f"[Assistant] {text}")

            elif role == "tool":
                parts.append(f"[Tool] {content}")

        return "\n\n".join(parts)

    # ---- Summary generation ----

    async def _generate_summary(self, messages_text: str) -> str:
        """调用 LLM 生成摘要"""
        try:
            response = await self._provider.call(
                messages=[
                    {
                        "role": "user",
                        "content": (
                            "Compress the following coding assistant conversation history into a structured summary.\n"
                            "Focus: preserve specific facts (file paths, function names, error messages, decision rationale), "
                            "discard pleasantries and repetition. Do not omit any feature points mentioned in the user's requirements.\n\n"
                            f"{messages_text}"
                        ),
                    }
                ],
                system=SUMMARY_SYSTEM_PROMPT,
                tools=[],
                max_tokens=4000,
            )
            return response.choices[0].message.content or ""
        except Exception as e:
            logger.warning(f"Summary generation failed: {e}")
            return ""

    @staticmethod
    def _build_fallback_summary(messages: list[dict]) -> str:
        """构建备用摘要（当 LLM 调用失败时）"""
        first_user = ""
        last_user = ""
        for msg in messages:
            if msg.get("role") == "user":
                content = msg.get("content", "")
                if isinstance(content, list):
                    content = " ".join(
                        p.get("text", "") if isinstance(p, dict) else str(p)
                        for p in content
                    )
                if not first_user:
                    first_user = content[:300]
                last_user = content[:300]

        return (
            f"[Conversation summary ({len(messages)} messages compressed)]\n"
            f"User's first message: {first_user}\n"
            f"Last discussed: {last_user}"
        )

    # ---- Main operation ----

    def _persist_summary_for_dream(self, summary: str, old_messages: list[dict]) -> str:
        """Persist summary for Dream system consumption. Returns summary_id."""
        try:
            from ..paths import DREAM_DIR
            from .events import EventType

            summaries_dir = DREAM_DIR / "summaries"
            summaries_dir.mkdir(parents=True, exist_ok=True)

            summary_id = f"summary_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
            data = {
                "id": summary_id,
                "created_at": datetime.now().isoformat(),
                "workdir": str(Path.cwd()),
                "summary": summary,
                "message_count": len(old_messages),
            }

            path = summaries_dir / f"{summary_id}.json"
            path.write_text(
                json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            logger.debug(f"Persisted dream summary: {summary_id}")

            # Notify Dream system and gateway listeners
            if self._event_bus:
                self._event_bus.emit(
                    EventType.DREAM_SUMMARY_AVAILABLE,
                    {"summary_id": summary_id, "message_count": len(old_messages)},
                )

            return summary_id
        except Exception as e:
            logger.warning(f"Failed to persist dream summary: {e}")
            return ""

    async def compact(self, messages: list[dict], model: str) -> list[dict]:
        """压缩消息列表

        按轮次（turn）分割：保留最近 N 个 user 开头的轮次，
        将旧消息压缩为摘要。保留轮次中的 tool 调用链会被清除（语义已在摘要中）。
        """
        # 1. 找到轮次边界
        turn_starts = self._find_turn_starts(messages)
        keep = self._config.keep_recent_turns

        # turn 不够保留，不压缩
        if len(turn_starts) <= keep:
            return messages

        # 2. 按轮次分割
        if keep == 0:
            old_messages = messages
            recent_messages = []
        else:
            split_point = turn_starts[-keep]
            old_messages = messages[:split_point]
            recent_messages = messages[split_point:]

        # 3. 摘要从完整消息列表生成（包含 recent 中的 tool 调用信息）
        formatted = self._format_messages_for_summary(messages)
        summary = await self._generate_summary(formatted)
        if not summary:
            summary = self._build_fallback_summary(messages)

        # 4. 持久化摘要供 Dream 系统使用（会发射 DREAM_SUMMARY_AVAILABLE 事件）
        self._persist_summary_for_dream(summary, old_messages)

        # 5. 清理保留轮次中的 tool 调用链（摘要已包含这些信息）
        recent_cleaned = self._strip_tool_messages(recent_messages)

        # 6. 构建新消息列表
        new_messages = [
            {
                "role": "user",
                "content": f"[Context Summary]\n{summary}\n[End of summary]",
            },
            {
                "role": "assistant",
                "content": "Understood, I will continue based on the summary.",
            },
            *recent_cleaned,
        ]

        # 发送事件
        if self._event_bus:
            from .events import EventType

            self._event_bus.emit(
                EventType.CONTEXT_COMPACT,
                {
                    "old_count": len(messages),
                    "new_count": len(new_messages),
                    "compressed_count": len(messages) - len(new_messages),
                },
            )

        self._last_prompt_tokens = 0
        logger.info(
            f"Compacted: {len(messages)} -> {len(new_messages)} messages "
            f"(compressed {len(messages) - len(new_messages)} messages)"
        )
        return new_messages
