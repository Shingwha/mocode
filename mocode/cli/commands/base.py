"""Command base class"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import ClassVar, TYPE_CHECKING

if TYPE_CHECKING:
    from ..ui.layout import Layout
    from ..ui.menu import MenuAction
    from ...sdk import MocodeClient


@dataclass
class CommandContext:
    """Command execution context"""
    client: "MocodeClient"
    args: str  # Command arguments
    layout: "Layout | None" = None
    pending_message: str | None = None  # Message to send to agent after command

    @property
    def config(self):
        """Get config"""
        return self.client.config

    @property
    def agent(self):
        """Get Agent"""
        return self.client.agent


class Command(ABC):
    """Command base class"""

    name: ClassVar[str] = ""  # Command name, e.g., "/help"
    aliases: ClassVar[list[str]] = []  # Aliases
    description: ClassVar[str] = ""  # Description

    @abstractmethod
    def execute(self, ctx: CommandContext) -> bool:
        """Execute command

        Returns:
            True: Continue running
            False: Exit program
        """
        pass

    def match(self, cmd: str) -> bool:
        """Match command"""
        return cmd == self.name or cmd in self.aliases

    def confirm_delete(self, item_name: str) -> bool:
        """Standard delete confirmation dialog."""
        from ..ui import confirm_dialog, YELLOW, RESET
        return confirm_dialog(f"{YELLOW}Delete '{item_name}'?{RESET}")


class CommandRegistry:
    """命令注册表"""

    _instance = None
    _commands: dict[str, Command] = {}

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._commands = {}
        return cls._instance

    def register(self, cmd: Command):
        """注册命令"""
        self._commands[cmd.name] = cmd
        for alias in cmd.aliases:
            self._commands[alias] = cmd

    def unregister(self, name: str) -> bool:
        """注销命令

        Args:
            name: 命令名（如 "/rtk"）

        Returns:
            True 如果命令存在并被注销
        """
        cmd = self._commands.get(name)
        if cmd is None:
            return False

        # 删除命令名和所有别名
        keys_to_remove = [name] + cmd.aliases
        for key in keys_to_remove:
            self._commands.pop(key, None)
        return True

    def get(self, name: str) -> Command | None:
        """获取命令"""
        return self._commands.get(name)

    def all(self) -> list[Command]:
        """获取所有唯一命令"""
        seen = set()
        result = []
        for cmd in self._commands.values():
            if cmd.name not in seen:
                seen.add(cmd.name)
                result.append(cmd)
        return sorted(result, key=lambda c: c.name)

    def execute(self, ctx: CommandContext) -> bool:
        """执行命令"""
        cmd = self.get(ctx.args.split()[0] if ctx.args else "")
        if cmd:
            # 去掉命令名，传递剩余参数
            parts = ctx.args.split(maxsplit=1)
            ctx.args = parts[1] if len(parts) > 1 else ""
            return cmd.execute(ctx)
        return True  # 未匹配命令，继续处理


def command(name: str, *aliases, description: str = ""):
    """命令装饰器"""
    def decorator(cls):
        cls.name = name
        cls.aliases = list(aliases)
        cls.description = description
        return cls
    return decorator
