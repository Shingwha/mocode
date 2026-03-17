"""Base Channel 抽象类"""

from abc import ABC, abstractmethod
from typing import Callable, Awaitable


class BaseChannel(ABC):
    """渠道抽象类

    定义所有渠道（Telegram、飞书、钉钉等）需要实现的接口。
    """

    name: str

    @abstractmethod
    async def start(self) -> None:
        """启动渠道"""
        pass

    @abstractmethod
    async def stop(self) -> None:
        """停止渠道"""
        pass

    @abstractmethod
    async def send_message(self, user_id: str, text: str) -> None:
        """发送消息给用户

        Args:
            user_id: 用户标识
            text: 消息文本
        """
        pass

    @abstractmethod
    async def send_permission_keyboard(
        self,
        user_id: str,
        tool_name: str,
        tool_args: dict,
        callback_id: str,
    ) -> None:
        """发送权限确认键盘

        Args:
            user_id: 用户标识
            tool_name: 工具名称
            tool_args: 工具参数
            callback_id: 回调标识，用于响应按钮点击
        """
        pass

    @abstractmethod
    def on_message(self, handler: Callable[[str, str], Awaitable[None]]) -> None:
        """注册消息处理器

        Args:
            handler: 消息处理函数 (user_id, text) -> None
        """
        pass

    @abstractmethod
    def on_permission_response(
        self, handler: Callable[[str, str, str], Awaitable[None]]
    ) -> None:
        """注册权限响应处理器

        Args:
            handler: 权限响应处理函数 (user_id, callback_id, response) -> None
        """
        pass

    @abstractmethod
    def is_user_allowed(self, user_id: str) -> bool:
        """检查用户是否被允许使用

        Args:
            user_id: 用户标识

        Returns:
            是否允许
        """
        pass
