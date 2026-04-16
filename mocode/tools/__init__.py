"""Tools 层 — 工厂函数注册模式

v0.2 关键改进：
- register_all_tools(registry, config) 接收 ToolRegistry 实例和 Config
- 工具通过闭包捕获 config，不需要 ContextVar
"""

from .utils import truncate_result


def register_all_tools(registry, config) -> None:
    """注册所有工具"""
    from .file import register_file_tools
    from .search import register_search_tools
    from .bash import register_bash_tools
    from .fetch import register_fetch_tools

    register_file_tools(registry, config)
    register_search_tools(registry)
    register_bash_tools(registry, config)
    register_fetch_tools(registry)
