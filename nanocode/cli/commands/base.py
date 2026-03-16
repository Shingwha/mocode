"""命令基类"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import ClassVar

from ...core import Config, AsyncAgent


@dataclass
class CommandContext:
    """命令执行上下文"""
    config: Config
    agent: AsyncAgent
    args: str  # 命令参数


class Command(ABC):
    """命令基类"""

    name: ClassVar[str] = ""  # 命令名，如 "/help"
    aliases: ClassVar[list[str]] = []  # 别名
    description: ClassVar[str] = ""  # 描述

    @abstractmethod
    def execute(self, ctx: CommandContext) -> bool:
        """执行命令

        Returns:
            True: 继续运行
            False: 退出程序
        """
        pass

    def match(self, cmd: str) -> bool:
        """匹配命令"""
        return cmd == self.name or cmd in self.aliases


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
