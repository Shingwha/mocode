"""工具执行上下文

使用 ContextVar 传递配置，避免修改工具签名。
"""

from contextvars import ContextVar
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from ..core.config import Config

# 当前配置上下文
_current_config: ContextVar[Optional["Config"]] = ContextVar("current_config", default=None)


def set_tool_context(config: "Config") -> None:
    """设置工具执行上下文

    Args:
        config: 当前配置实例
    """
    _current_config.set(config)


def get_tool_context() -> Optional["Config"]:
    """获取当前工具执行上下文

    Returns:
        当前配置实例，如果未设置则返回 None
    """
    return _current_config.get()
