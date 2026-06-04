"""Prompts — centralized prompt definitions using Section/Builder syntax.

Usage:
    from mocode.prompts import summary_system_prompt, subagent_system_prompt
    from mocode.prompts import COMPACT_USER_TEMPLATE, build_system_prompt

    # For compact: build the system prompt string
    system_str = summary_system_prompt.build(fmt="xml")

    # For subagent: build the system prompt string
    system_str = subagent_system_prompt.build(fmt="xml")

    # For app: build with context
    system_str = build_system_prompt(tools=registry, cwd="/path")
"""

from .compact import summary_system_prompt, COMPACT_USER_TEMPLATE
from .subagent import subagent_system_prompt
from .app import build_system_prompt

__all__ = [
    "summary_system_prompt",
    "COMPACT_USER_TEMPLATE",
    "subagent_system_prompt",
    "build_system_prompt",
]
