"""工具执行上下文

使用 ContextVar 传递配置和依赖，避免修改工具签名。
"""

from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from ..core.config import Config
    from ..core.interrupt import InterruptToken


@dataclass
class ToolContext:
    """工具执行上下文"""

    config: "Config"
    interrupt_token: Optional["InterruptToken"] = None
    working_dir: Optional[str] = None


# 当前上下文
_tool_context: ContextVar[Optional[ToolContext]] = ContextVar("tool_context", default=None)


def set_tool_context(ctx: ToolContext) -> None:
    """设置工具执行上下文"""
    _tool_context.set(ctx)


def get_tool_context() -> Optional[ToolContext]:
    """获取当前工具执行上下文"""
    return _tool_context.get()


# 向后兼容的辅助函数
def get_config() -> Optional["Config"]:
    """获取当前配置（向后兼容）"""
    ctx = get_tool_context()
    return ctx.config if ctx else None


def check_interrupt() -> bool:
    """检查是否被中断

    Returns:
        True 如果应该中断执行
    """
    ctx = get_tool_context()
    return ctx.interrupt_token.is_interrupted() if ctx and ctx.interrupt_token else False
