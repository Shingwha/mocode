"""权限处理器抽象 - 支持不同前端实现"""

from abc import ABC, abstractmethod


class PermissionHandler(ABC):
    """权限处理器抽象类

    用于处理工具执行前的权限询问，不同前端可以实现不同的交互方式：
    - CLI: 通过终端提示用户选择
    - Web: 通过 WebSocket 推送询问，等待响应
    - Headless: 自动允许或拒绝
    """

    @abstractmethod
    async def ask_permission(self, tool_name: str, tool_args: dict) -> str:
        """询问用户权限

        Args:
            tool_name: 工具名称
            tool_args: 工具参数

        Returns:
            用户响应:
            - "allow": 允许执行
            - "deny": 拒绝执行
            - 其他字符串: 作为工具结果直接返回（替代执行）
        """
        pass


class DefaultPermissionHandler(PermissionHandler):
    """默认权限处理器 - 自动允许"""

    async def ask_permission(self, tool_name: str, tool_args: dict) -> str:
        """默认允许所有工具执行"""
        return "allow"


class DenyAllPermissionHandler(PermissionHandler):
    """拒绝所有权限处理器 - 自动拒绝"""

    async def ask_permission(self, tool_name: str, tool_args: dict) -> str:
        """默认拒绝所有工具执行"""
        return "deny"
