"""MoCode 0.3 — Core package."""

from __future__ import annotations

__all__ = [
    "Agent",
    "AgentLoop",
    "AgentConfig",
    "LoopResult",
    "SubAgent",
    "SubAgentConfig",
    "Provider",
    "Response",
    "ToolCall",
    "Usage",
    "Tool",
    "ToolRegistry",
    "ToolError",
    "AgentHook",
    "AgentHookContext",
    "HookRunner",
    "ToolTimingTracker",
    "Prompt",
    "Section",
    "Skill",
    "SkillMetadata",
    "SkillManager",
    "make_builtin_skill",
    "VirtualFS",
]

_LAZY_IMPORTS: dict[str, tuple[str, str]] = {
    "Agent": (".builder", "Agent"),
    "AgentLoop": (".agent", "AgentLoop"),
    "AgentConfig": (".agent", "AgentConfig"),
    "LoopResult": (".agent", "LoopResult"),
    "SubAgent": (".subagent", "SubAgent"),
    "SubAgentConfig": (".subagent", "SubAgentConfig"),
    "Provider": (".provider", "Provider"),
    "Response": (".provider", "Response"),
    "ToolCall": (".provider", "ToolCall"),
    "Usage": (".provider", "Usage"),
    "Tool": (".tool", "Tool"),
    "ToolRegistry": (".tool", "ToolRegistry"),
    "ToolError": (".tool", "ToolError"),
    "AgentHook": (".hook", "AgentHook"),
    "AgentHookContext": (".hook", "AgentHookContext"),
    "HookRunner": (".hook", "HookRunner"),
    "ToolTimingTracker": (".hook", "ToolTimingTracker"),
    "Prompt": (".prompt", "Prompt"),
    "Section": (".prompt", "Section"),
    "Skill": (".skill", "Skill"),
    "SkillMetadata": (".skill", "SkillMetadata"),
    "SkillManager": (".skill", "SkillManager"),
    "make_builtin_skill": (".skill", "make_builtin_skill"),
    "VirtualFS": (".virtualfs", "VirtualFS"),
}


def __getattr__(name: str):
    if name in _LAZY_IMPORTS:
        import importlib
        module_path, attr = _LAZY_IMPORTS[name]
        module = importlib.import_module(module_path, __name__)
        value = getattr(module, attr)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
