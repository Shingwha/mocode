"""Tools 层"""

from .base import Tool, ToolRegistry, tool
from .file_tools import register_file_tools
from .search_tools import register_search_tools
from .shell_tools import register_shell_tools

__all__ = [
    "Tool",
    "ToolRegistry",
    "tool",
    "register_file_tools",
    "register_search_tools",
    "register_shell_tools",
]


def register_all_tools():
    """注册所有工具"""
    register_file_tools()
    register_search_tools()
    register_shell_tools()
