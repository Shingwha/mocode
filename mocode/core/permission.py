"""权限系统 - 工具执行前的权限检查"""

from enum import Enum
from dataclasses import dataclass, field
import fnmatch
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import PermissionConfig


class PermissionAction(Enum):
    """权限动作"""
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


@dataclass
class PermissionConfig:
    """权限配置 - 简化格式"""
    # 工具级别的权限配置: {"*": "ask", "bash": "allow", "edit": "deny"}
    rules: dict[str, str] = field(default_factory=dict)

    def get_action(self, tool_name: str) -> PermissionAction:
        """
        获取工具对应的权限动作

        优先级: 具体工具规则 > 通配符 "*" > 默认 ASK
        """
        # 先匹配具体工具名
        if tool_name in self.rules:
            return PermissionAction(self.rules[tool_name])

        # 再匹配通配符
        if "*" in self.rules:
            return PermissionAction(self.rules["*"])

        # 默认询问
        return PermissionAction.ASK

    def to_dict(self) -> dict[str, str]:
        """序列化为字典"""
        return self.rules.copy()

    @classmethod
    def from_dict(cls, data: dict) -> "PermissionConfig":
        """从字典创建"""
        # 支持简化的扁平格式
        if data and all(isinstance(v, str) for v in data.values()):
            return cls(rules=data.copy())
        return cls()


@dataclass
class ToolPermissionRules:
    """单个工具的详细权限规则（可选的高级配置）"""
    rules: dict[str, str] = field(default_factory=dict)

    def get_action(self, target: str) -> PermissionAction:
        """
        获取目标对应的权限动作

        按优先级匹配：具体规则 > 通配符 "*"
        """
        # 先匹配具体规则
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
        """
        匹配模式

        - bash 工具: 前缀匹配 (如 "git *" 匹配 "git status")
        - 文件工具: glob 模式匹配路径（支持 ** 递归匹配）
        """
        if pattern == "*":
            return True

        # bash 前缀匹配: "git *" 匹配 "git status", "git commit -m ..."
        if pattern.endswith(" *"):
            prefix = pattern[:-2]  # 去掉 " *"
            return target == prefix or target.startswith(prefix + " ")

        # 处理 ** 递归 glob 模式
        if "**" in pattern:
            return self._match_recursive_glob(pattern, target)

        # 普通 glob 模式匹配
        return fnmatch.fnmatch(target, pattern)

    def _match_recursive_glob(self, pattern: str, target: str) -> bool:
        """
        匹配包含 ** 的递归 glob 模式
        """
        # 规范化路径分隔符
        target = target.replace("\\", "/")

        # 转义特殊字符，但保留 * 和 **
        regex_pattern = ""
        i = 0
        while i < len(pattern):
            if pattern[i:i+2] == "**":
                # **/ 匹配零个或多个目录
                if i + 2 < len(pattern) and pattern[i+2] == "/":
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
            return fnmatch.fnmatch(target, pattern)


class PermissionMatcher:
    """权限匹配器"""
    def __init__(self, config: PermissionConfig):
        self.config = config

    def check(self, tool_name: str, args: dict) -> PermissionAction:
        """
        检查工具调用的权限

        Args:
            tool_name: 工具名称
            args: 工具参数

        Returns:
            权限动作
        """
        return self.config.get_action(tool_name)
