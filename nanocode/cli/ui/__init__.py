"""CLI UI 组件"""

from .colors import *
from .components import (
    Block,
    Header,
    InputPrompt,
    Message,
    MessageType,
    # 基础组件
    Separator,
    ToolCall,
    ToolError,
    ToolResult,
    # UI 管理器
    UIManager,
    error,
    info,
    # 工具函数
    render_markdown,
    separator_line,
    success,
    text,
    tool_call,
    tool_result,
    ui,
    warning,
)
from .layout import Layout, SimpleLayout, TerminalLayout
from .messages import get_welcome_message
from .spinner import (
    SPINNER_STYLES,
    ProgressGroup,
    Spinner,
    SpinnerStyle,
    StatusIndicator,
)
from .statusbar import AdaptiveSeparator, StatusBar
from .widgets import SelectMenu

__all__ = [
    # colors
    "RESET",
    "BOLD",
    "DIM",
    "BLUE",
    "CYAN",
    "GREEN",
    "YELLOW",
    "RED",
    # 布局管理器
    "SimpleLayout",
    "TerminalLayout",
    "Layout",
    # 基础组件
    "Separator",
    "Header",
    "Block",
    "Message",
    "MessageType",
    "ToolCall",
    "ToolResult",
    "ToolError",
    "InputPrompt",
    # Spinner
    "Spinner",
    "SpinnerStyle",
    "StatusIndicator",
    "ProgressGroup",
    "SPINNER_STYLES",
    # 工具函数
    "render_markdown",
    "error",
    "success",
    "info",
    "warning",
    "text",
    "tool_call",
    "tool_result",
    "separator_line",
    # UI 管理器
    "UIManager",
    "ui",
    # 其他
    "get_welcome_message",
    "StatusBar",
    "AdaptiveSeparator",
    "SelectMenu",
]
