"""Tools 层"""

from .base import Tool, ToolRegistry, tool
from .bash import BashSession, get_session, close_session, register_bash_tools
from .file_tools import register_file_tools
from .search_tools import register_search_tools

__all__ = [
    "Tool",
    "ToolRegistry",
    "tool",
    "BashSession",
    "get_session",
    "close_session",
    "register_file_tools",
    "register_search_tools",
    "register_bash_tools",
]


def register_all_tools():
    """注册所有工具"""
    register_file_tools()
    register_search_tools()
    register_bash_tools()

    # 注册 skill 工具
    from ..skills import register_skill_tools

    register_skill_tools()
