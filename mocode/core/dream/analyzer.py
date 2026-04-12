"""Dream Analyzer - Phase 1: analyze summaries and produce edit directives"""

import json
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .prompts import PHASE1_SYSTEM_PROMPT, build_phase1_user_prompt

if TYPE_CHECKING:
    from ...providers.openai import AsyncOpenAIProvider

logger = logging.getLogger(__name__)


@dataclass
class EditDirective:
    """A single edit directive produced by Phase 1."""

    target: str  # "SOUL.md" | "USER.md" | "MEMORY.md"
    action: str  # "add" | "remove"
    content: str  # content to add or remove
    reasoning: str  # why this change


class DreamAnalyzer:
    """Phase 1: Analyze summaries and produce edit directives via single LLM call."""

    def __init__(self, provider: "AsyncOpenAIProvider"):
        self._provider = provider

    async def analyze(
        self,
        summaries: list[str],
        soul: str,
        user: str,
        memory: str,
    ) -> list[EditDirective]:
        """Run Phase 1 analysis. Returns edit directives (empty if no changes needed)."""
        user_prompt = build_phase1_user_prompt(summaries, soul, user, memory)

        try:
            response = await self._provider.call(
                messages=[{"role": "user", "content": user_prompt}],
                system=PHASE1_SYSTEM_PROMPT,
                tools=[],
                max_tokens=2000,
            )
            content = response.choices[0].message.content or ""
            return self._parse_directives(content)
        except Exception as e:
            logger.error(f"Dream Phase 1 failed: {e}")
            return []

    @staticmethod
    def _parse_directives(content: str) -> list[EditDirective]:
        """Parse LLM output into EditDirective list."""
        # Strip markdown code fences if present
        text = content.strip()
        if text.startswith("```"):
            # Remove opening fence
            first_newline = text.index("\n") if "\n" in text else len(text)
            text = text[first_newline + 1:]
            # Remove closing fence
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            logger.warning(f"Failed to parse Phase 1 output as JSON: {content[:200]}")
            return []

        if not isinstance(data, list):
            return []

        directives = []
        valid_targets = {"SOUL.md", "USER.md", "MEMORY.md"}
        valid_actions = {"add", "remove"}

        for item in data:
            if not isinstance(item, dict):
                continue
            target = item.get("target", "")
            action = item.get("action", "")
            content_val = item.get("content", "")
            reasoning = item.get("reasoning", "")

            if target in valid_targets and action in valid_actions and content_val:
                directives.append(EditDirective(
                    target=target,
                    action=action,
                    content=content_val,
                    reasoning=reasoning,
                ))

        return directives
