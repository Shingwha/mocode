"""权限系统 - 工具执行前的权限检查"""

import fnmatch
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum


class PermissionAction(Enum):
    """权限动作"""

    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


@dataclass
class ToolPermissionRules:
    """单个工具的详细权限规则"""

    rules: dict[str, str] = field(default_factory=dict)

    def get_action(self, target: str) -> PermissionAction:
        """根据目标（命令或路径）获取权限动作"""
        # 先匹配具体规则（排除通配符）
        for pattern, action in self.rules.items():
            if pattern == "*":
                continue
            if self._matches(pattern, target):
                return PermissionAction(action)

        # 再匹配通配符
        if "*" in self.rules:
            return PermissionAction(self.rules["*"])

        # 默认询问
        return PermissionAction.ASK

    def _matches(self, pattern: str, target: str) -> bool:
        """匹配模式"""
        if pattern == "*":
            return True

        # 前缀匹配: "git *" 匹配 "git status"
        if pattern.endswith(" *"):
            prefix = pattern[:-2]
            return target == prefix or target.startswith(prefix + " ")

        # 递归 glob 匹配（用于文件路径）
        if "**" in pattern or "*" in pattern or "?" in pattern:
            return self._match_glob(pattern, target)

        # 精确匹配
        return pattern == target

    def _match_glob(self, pattern: str, target: str) -> bool:
        """glob 模式匹配"""
        target = target.replace("\\", "/")

        # **/ 匹配零个或多个目录
        if "**" in pattern:
            regex_pattern = ""
            i = 0
            while i < len(pattern):
                if pattern[i : i + 2] == "**":
                    if i + 2 < len(pattern) and pattern[i + 2] == "/":
                        regex_pattern += "(?:.*/)?"
                        i += 3
                    else:
                        regex_pattern += ".*"
                        i += 2
                elif pattern[i] == "*":
                    regex_pattern += "[^/]*"
                    i += 1
                elif pattern[i] in ".[]{}()^$+?|\\":
                    regex_pattern += "\\" + pattern[i]
                    i += 1
                else:
                    regex_pattern += pattern[i]
                    i += 1
            regex_pattern = "^" + regex_pattern + "$"
            try:
                return bool(re.match(regex_pattern, target))
            except re.error:
                pass

        # 普通 glob
        return fnmatch.fnmatch(target, pattern)


@dataclass
class PermissionConfig:
    """权限配置 - 支持扁平格式和嵌套格式"""

    # 扁平格式: {"*": "ask", "bash": "allow"}
    # 嵌套格式: {"bash": {"*": "ask", "git *": "allow"}}
    rules: dict = field(default_factory=dict)

    def get_action(
        self, tool_name: str, tool_args: dict | None = None
    ) -> PermissionAction:
        """
        获取工具对应的权限动作

        Args:
            tool_name: 工具名称
            tool_args: 工具参数，用于细粒度匹配
        """
        tool_rules = self._get_tool_rules(tool_name)

        if tool_rules is None:
            # 没有找到该工具的规则，返回默认
            return PermissionAction.ASK

        if isinstance(tool_rules, str):
            # 扁平格式: "allow" | "ask" | "deny"
            return PermissionAction(tool_rules)

        # 嵌套格式: 需要根据参数匹配
        if not tool_args:
            # 没有参数，使用通配符规则
            return PermissionAction(tool_rules.get("*", "ask"))

        # 从参数中提取目标（命令或路径）
        target = self._extract_target(tool_name, tool_args)
        rules = ToolPermissionRules(rules=tool_rules)
        return rules.get_action(target)

    def _get_tool_rules(self, tool_name: str) -> str | dict | None:
        """获取指定工具的规则"""
        # 先匹配具体工具
        if tool_name in self.rules:
            return self.rules[tool_name]

        # 再匹配通配符
        if "*" in self.rules:
            global_rule = self.rules["*"]
            # 如果全局规则是字符串，直接返回
            if isinstance(global_rule, str):
                return global_rule
            # 如果全局规则是 dict，返回 None（让具体工具自己决定）
            return None

        return None

    def _extract_target(self, tool_name: str, tool_args: dict) -> str:
        """从工具参数中提取匹配目标"""
        # bash 工具：提取 command
        if tool_name == "bash" and "command" in tool_args:
            return tool_args["command"]

        # 文件类工具：提取 path
        if "path" in tool_args:
            return tool_args["path"]

        # 其他：尝试组合所有参数
        return " ".join(str(v) for v in tool_args.values())

    def to_dict(self) -> dict:
        """序列化为字典"""
        return self.rules.copy()

    @classmethod
    def from_dict(cls, data: dict) -> "PermissionConfig":
        """从字典创建，支持扁平格式和嵌套格式"""
        if not data:
            return cls()
        return cls(rules=data.copy())


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
        return "ask"


class DenyAllPermissionHandler(PermissionHandler):
    """拒绝所有权限处理器 - 自动拒绝"""

    async def ask_permission(self, tool_name: str, tool_args: dict) -> str:
        """默认拒绝所有工具执行"""
        return "deny"


class PermissionMatcher:
    """权限匹配器（保持兼容，直接使用 PermissionConfig）"""

    def __init__(self, config: PermissionConfig):
        self.config = config

    def check(self, tool_name: str, args: dict | None = None) -> PermissionAction:
        """
        检查工具调用的权限

        Args:
            tool_name: 工具名称
            args: 工具参数

        Returns:
            权限动作
        """
        return self.config.get_action(tool_name, args)
