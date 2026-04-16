"""Tools 层 — 工厂函数注册模式

v0.2 关键改进：
- register_all_tools(registry, config) 接收 ToolRegistry 实例和 Config
- 工具通过闭包捕获 config，不需要 ContextVar
- 系统工具（compact/dream/sub_agent）通过 register_system_tools 独立注册
"""

from .utils import truncate_result


def register_basic_tools(registry, config) -> None:
    """注册基础工具（file, search, bash, fetch）"""
    from .file import register_file_tools
    from .search import register_search_tools
    from .bash import register_bash_tools
    from .fetch import register_fetch_tools

    register_file_tools(registry, config)
    register_search_tools(registry)
    register_bash_tools(registry, config)
    register_fetch_tools(registry)


def register_system_tools(registry, config, *, provider=None, compact=None, dream=None,
                          event_bus=None, cancel_token=None) -> None:
    """注册系统级工具（compact, dream, sub_agent）"""
    from .compact import register_compact_tools
    from .dream import register_dream_tools
    from .subagent import register_subagent_tools

    register_compact_tools(registry, compact)
    register_dream_tools(registry, dream)
    if provider is not None:
        register_subagent_tools(registry, config, provider)
