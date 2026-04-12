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
你是一个对话压缩助手。将以下编码助手对话历史压缩为结构化摘要。

必须保留：
1. **文件路径** — 助手读取、写入或讨论的每个文件
2. **关键决策** — 为什么选择某些方案而非其他方案
3. **遇到的错误** — 错误信息和解决方法
4. **当前工作状态** — 最后在做什么
5. **用户偏好** — 对话中表达的技术偏好
6. **重要代码上下文** — 函数签名、变量名、架构选择

输出格式：
[完成的决策]
- ...

[当前代码状态]
- ...

[待办事项]
- ..."""


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
    def _ensure_no_partial_tool_sequence(
        messages: list[dict], split_point: int
    ) -> int:
        """确保不在 tool 序列中间切割

        三种情况：
        - Case A：split_point 前一条是带 tool_calls 的 assistant，但 split_point 之后
          还有属于该 assistant 的 tool 结果消息。此时将 split_point 后移，包含完整的
          assistant + tool 序列。
        - Case B：split_point 本身落在 tool 消息中间（即前面的 assistant 已被划入
          old 区间，但部分 tool 结果落在了 recent 区间）。此时向前回退到该 tool 序列
          对应的 assistant 之前的 user 消息，确保 assistant + tool 不被拆散。
        - 默认：split_point 不在 tool 序列附近，无需调整。
        """
        if split_point <= 0:
            return split_point

        # Case A: prev 是 assistant+tool_calls，split_point 之后还有孤立的 tool 结果
        prev_msg = messages[split_point - 1]
        if prev_msg.get("role") == "assistant" and prev_msg.get("tool_calls"):
            j = split_point
            while j < len(messages) and messages[j].get("role") == "tool":
                j += 1
            if j > split_point:
                return j

        # Case B: split_point 落在 tool 消息中间，回退到对应 user 消息
        if split_point < len(messages) and messages[split_point].get("role") == "tool":
            j = split_point
            while j > 0 and messages[j].get("role") == "tool":
                j -= 1
            while j > 0 and messages[j].get("role") != "user":
                j -= 1
            return j

        return split_point

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
                            if len(args) > 200:
                                args = args[:200] + "..."
                            text += f"\n[Tool Call: {name}({args})]"
                parts.append(f"[Assistant] {text}")

            elif role == "tool":
                if isinstance(content, str):
                    if len(content) > 2000:
                        content = content[:1500] + "...[truncated]"
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
                        "content": f"请压缩以下对话：\n\n{messages_text}",
                    }
                ],
                system=SUMMARY_SYSTEM_PROMPT,
                tools=[],
                max_tokens=2000,
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

        将旧消息压缩为摘要，保留最近 N 条消息（绝对数量，包括所有角色）。
        """
        if len(messages) < 4:
            return messages

        keep = self._config.keep_recent_turns  # 改为按绝对消息数保留

        if len(messages) <= keep:
            return messages

        # 初始分割点：保留最后 keep 条，压缩前面的
        split_point = len(messages) - keep

        # 保护：确保不在 tool 序列中间切割
        split_point = self._ensure_no_partial_tool_sequence(messages, split_point)

        old_messages = messages[:split_point]
        recent_messages = messages[split_point:]

        if not old_messages:
            return messages

        # 生成摘要
        formatted = self._format_messages_for_summary(old_messages)
        summary = await self._generate_summary(formatted)
        if not summary:
            summary = self._build_fallback_summary(old_messages)

        # 持久化摘要供 Dream 系统使用（会发射 DREAM_SUMMARY_AVAILABLE 事件）
        self._persist_summary_for_dream(summary, old_messages)

        # 构建新消息列表
        new_messages = [
            {
                "role": "user",
                "content": f"[Context Summary]\n{summary}\n[End of summary]",
            },
            {
                "role": "assistant",
                "content": "Understood, I will continue based on the summary.",
            },
            *recent_messages,
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
