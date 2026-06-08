"""Prompts — centralized prompt definitions using Section/Builder syntax.

Usage:
    from mocode.prompts import summary_system_prompt, build_subagent_prompt
    from mocode.prompts import COMPACT_USER_TEMPLATE, build_system_prompt

    # For compact: build the system prompt string
    system_str = summary_system_prompt.build(fmt="xml")

    # For subagent: build prompt layered on parent
    system_str = build_subagent_prompt(parent_prompt)

    # For app: build with context
    system_str = build_system_prompt(tools=registry, cwd="/path")
"""

from .compact import summary_system_prompt, COMPACT_USER_TEMPLATE
from .subagent import build_subagent_prompt
from .app import build_system_prompt

__all__ = [
    "summary_system_prompt",
    "COMPACT_USER_TEMPLATE",
    "build_subagent_prompt",
    "build_system_prompt",
]
