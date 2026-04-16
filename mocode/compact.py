"""CompactManager — 使用 Response DTO

v0.2 关键改进：不再访问 response.choices[0].message.content，使用 resp.content。
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from .paths import DREAM_DIR

if TYPE_CHECKING:
    from .config import CompactConfig
    from .event import EventBus
    from .provider import Provider

logger = logging.getLogger(__name__)

DEFAULT_CONTEXT_WINDOW = 128_000

SUMMARY_SYSTEM_PROMPT = """\
You are a conversation compression assistant. Your task is to compress the following coding assistant conversation history into a detailed, information-dense summary that preserves maximum useful context for continuing the work.

## Core Principle
**Maximize information density.** The summary must contain enough specific detail that a developer reading only the summary can resume work without loss of critical context. When in doubt, include rather than exclude.

## What to PRESERVE (high value)
- File paths, function/class/variable names, line numbers
- Error messages and their full text, stack traces, root causes
- Code snippets that represent key decisions or non-obvious logic
- User's explicit preferences, constraints, and rejected alternatives
- Tool call results that revealed important facts (file contents, search results, command output)
- Architectural decisions and the reasoning behind them
- Dependencies added/removed, config changes
- Git state: branches, uncommitted changes, PR numbers

## What to DISCARD (low value)
- Pleasantries, acknowledgments, filler phrases
- Redundant repetitions of the same fact
- Intermediate exploratory steps that led nowhere (unless they ruled out important alternatives)
- Verbose file listings or search results that were not acted upon

## Output Format

[User Requirements]
Complete list of what the user asked for. Preserve exact feature requirements, constraints, and preferences stated.

[Completed Work]
Chronological list of completed actions. Each entry must include:
- **What**: Specific files, functions, modules modified/created/deleted
- **Why**: The reason for the change or the decision rationale
- **How**: Brief description of the approach taken (especially for non-obvious solutions)
Separate subsections for distinct features or phases if the conversation covers multiple topics.

[Errors & Resolutions]
List every error encountered and how it was resolved. Include the error message or symptom and the fix applied.

[Technical Decisions & Context]
- Architectural choices made and alternatives rejected (with reasons)
- User preferences explicitly stated
- Non-obvious constraints or dependencies discovered during work

[Current State]
- What was actively being worked on when the conversation ended
- Modified files and their current state (uncommitted changes, etc.)
- Project state: build status, test status, any known issues

[Pending Items]
- Work mentioned but not yet started
- Partially completed work that needs continuation"""

COMPACT_USER_PROMPT = """\
Compress the following conversation into a detailed summary following the structured format above.

Critical requirements:
1. Preserve ALL specific technical details — file paths, function names, error messages, code snippets
2. Include every feature point from the user's requirements, even if not yet implemented
3. Keep tool call results that contain important facts (file contents, command outputs, search results)
4. Do NOT summarize away specifics into vagueness — "changed authenticate() in auth.py" is better than "modified authentication"
5. If the user expressed a preference or rejected an approach, record it

Conversation to compress:

{messages_text}"""


class CompactManager:
    """管理上下文压缩"""

    def __init__(
        self,
        compact_config: "CompactConfig",
        provider: "Provider",
        event_bus: "EventBus | None" = None,
    ):
        self._config = compact_config
        self._provider = provider
        self._event_bus = event_bus
        self._last_prompt_tokens: int = 0

    def update_usage(self, prompt_tokens: int) -> None:
        self._last_prompt_tokens = prompt_tokens

    def update_provider(self, provider: "Provider") -> None:
        self._provider = provider

    def should_compact(self, model: str) -> bool:
        if not self._config.enabled:
            return False
        if self._last_prompt_tokens == 0:
            return False
        context_window = self.get_context_window(model)
        threshold = context_window * self._config.threshold
        return self._last_prompt_tokens > threshold

    def get_context_window(self, model: str) -> int:
        return self._config.context_windows.get(model, DEFAULT_CONTEXT_WINDOW)

    @property
    def last_prompt_tokens(self) -> int:
        return self._last_prompt_tokens

    @staticmethod
    def _find_turn_starts(messages: list[dict]) -> list[int]:
        return [i for i, msg in enumerate(messages) if msg.get("role") == "user"]

    @staticmethod
    def _strip_tool_messages(messages: list[dict]) -> list[dict]:
        return [
            msg for msg in messages
            if msg.get("role") != "tool"
            and not (msg.get("role") == "assistant" and msg.get("tool_calls"))
        ]

    @staticmethod
    def _format_messages_for_summary(messages: list[dict]) -> str:
        parts = []
        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")

            if role == "user":
                if isinstance(content, list):
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

    async def _generate_summary(self, messages_text: str) -> str:
        """调用 LLM 生成摘要 — 使用 Response DTO"""
        try:
            resp = await self._provider.call(
                messages=[
                    {
                        "role": "user",
                        "content": COMPACT_USER_PROMPT.format(
                            messages_text=messages_text
                        ),
                    }
                ],
                system=SUMMARY_SYSTEM_PROMPT,
                tools=[],
                max_tokens=8000,
            )
            return resp.content or ""
        except Exception as e:
            logger.warning(f"Summary generation failed: {e}")
            return ""

    @staticmethod
    def _build_fallback_summary(messages: list[dict]) -> str:
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

    def _persist_summary_for_dream(self, summary: str, old_messages: list[dict]) -> str:
        try:
            from .event import EventType

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
        turn_starts = self._find_turn_starts(messages)
        keep = self._config.keep_recent_turns

        if len(turn_starts) <= keep:
            return messages

        if keep == 0:
            old_messages = messages
            recent_messages = []
        else:
            split_point = turn_starts[-keep]
            old_messages = messages[:split_point]
            recent_messages = messages[split_point:]

        formatted = self._format_messages_for_summary(messages)
        summary = await self._generate_summary(formatted)
        if not summary:
            summary = self._build_fallback_summary(messages)

        self._persist_summary_for_dream(summary, old_messages)

        recent_cleaned = self._strip_tool_messages(recent_messages)

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

        if self._event_bus:
            from .event import EventType

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
