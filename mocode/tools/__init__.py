"""Tools 层"""

from .base import Tool, ToolError, ToolRegistry, tool
from .bash import BashSession, close_session, get_session, register_bash_tools
from .context import ToolContext, get_config, get_tool_context, set_tool_context
from .fetch import register_fetch_tools
from .file_tools import register_file_tools
from .search_tools import register_search_tools
from .utils import truncate_result

__all__ = [
    "Tool",
    "ToolError",
    "ToolRegistry",
    "tool",
    "ToolContext",
    "set_tool_context",
    "get_tool_context",
    "get_config",
    "BashSession",
    "get_session",
    "close_session",
    "register_file_tools",
    "register_search_tools",
    "register_bash_tools",
    "register_fetch_tools",
    "truncate_result",
]


def register_all_tools():
    """注册所有工具"""
    register_file_tools()
    register_search_tools()
    register_bash_tools()
    register_fetch_tools()
    # skill 工具由 SkillManager 自动注册
