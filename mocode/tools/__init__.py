"""Built-in tools for MoCode.

Usage:
    from mocode.tools import ReadTool, EditTool, BashTool, FetchTool

    agent = (Agent()
        .provider(my_provider)
        .prompt("...")
        .tools([ReadTool, EditTool, BashTool(), FetchTool()])
        .build())
"""

from __future__ import annotations

__all__ = [
    "ReadTool", "WriteTool", "EditTool", "GlobTool", "GrepTool",
    "BashTool", "FetchTool", "SkillTool", "SubAgent", "SubAgentConfig",
    "SubAgentTool", "PlanTool",
]

_LAZY_IMPORTS: dict[str, tuple[str, str]] = {
    "ReadTool": (".file", "ReadTool"),
    "WriteTool": (".file", "WriteTool"),
    "EditTool": (".file", "EditTool"),
    "GlobTool": (".glob", "GlobTool"),
    "GrepTool": (".grep", "GrepTool"),
    "BashTool": (".bash", "BashTool"),
    "FetchTool": (".fetch", "FetchTool"),
    "SkillTool": (".skill", "SkillTool"),
    "SubAgent": ("..core", "SubAgent"),
    "SubAgentConfig": ("..core", "SubAgentConfig"),
    "SubAgentTool": (".subagent", "SubAgentTool"),
    "PlanTool": (".plan", "PlanTool"),
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
