"""MoCode 0.3 — Core package."""

from __future__ import annotations

from .builder import Agent
from .agent import AgentLoop, AgentConfig, LoopResult
from .provider import Provider, Response, ToolCall, Usage
from .tool import Tool, ToolRegistry, ToolError
from .hook import (
    Hooks,
    PRE_LOOP, MESSAGE_ADDED, TOOL_START, TOOL_COMPLETE,
    TEXT_COMPLETE, USAGE_UPDATE, CONTEXT_COMPACT, ERROR,
)
from .prompt import Prompt, Section
from .skill import Skill, SkillMetadata, SkillManager

__all__ = [
    "Agent",
    "AgentLoop", "AgentConfig", "LoopResult",
    "Provider", "Response", "ToolCall", "Usage",
    "Tool", "ToolRegistry", "ToolError",
    "Hooks", "PRE_LOOP", "MESSAGE_ADDED", "TOOL_START", "TOOL_COMPLETE",
    "TEXT_COMPLETE", "USAGE_UPDATE", "CONTEXT_COMPACT", "ERROR",
    "Prompt", "Section",
    "Skill", "SkillMetadata", "SkillManager",
]
