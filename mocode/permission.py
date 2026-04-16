"""PermissionChecker + PermissionHandler

基本平移自 v0.1，与 Config 的 current_mode/modes 交互不变。
"""

import fnmatch
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import Config


class PermissionAction(Enum):
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


class CheckOutcome(Enum):
    ALLOW = "allow"
    DENY = "deny"
    USER_INPUT = "user_input"


@dataclass
class CheckResult:
    outcome: CheckOutcome
    reason: str = ""
    user_input: str | None = None


@dataclass
class ToolPermissionRules:
    rules: dict[str, str] = field(default_factory=dict)

    def get_action(self, target: str) -> PermissionAction:
        for pattern, action in self.rules.items():
            if pattern == "*":
                continue
            if self._matches(pattern, target):
                return PermissionAction(action)
        if "*" in self.rules:
            return PermissionAction(self.rules["*"])
        return PermissionAction.ASK

    def _matches(self, pattern: str, target: str) -> bool:
        if pattern == "*":
            return True
        if pattern.endswith(" *"):
            prefix = pattern[:-2]
            return target == prefix or target.startswith(prefix + " ")
        if "**" in pattern or "*" in pattern or "?" in pattern:
            return self._match_glob(pattern, target)
        return pattern == target

    def _match_glob(self, pattern: str, target: str) -> bool:
        target = target.replace("\\", "/")
        if "**" in pattern:
            regex_pattern = ""
            i = 0
            while i < len(pattern):
                if pattern[i:i + 2] == "**":
                    if i + 2 < len(pattern) and pattern[i + 2] == "/":
                        regex_pattern += "(?:.*/))?"
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
        return fnmatch.fnmatch(target, pattern)


@dataclass
class PermissionConfig:
    rules: dict = field(default_factory=dict)

    def get_action(
        self, tool_name: str, tool_args: dict | None = None
    ) -> PermissionAction:
        tool_rules = self._get_tool_rules(tool_name)
        if tool_rules is None:
            return PermissionAction.ASK
        if isinstance(tool_rules, str):
            return PermissionAction(tool_rules)
        if not tool_args:
            return PermissionAction(tool_rules.get("*", "ask"))
        target = self._extract_target(tool_name, tool_args)
        rules = ToolPermissionRules(rules=tool_rules)
        return rules.get_action(target)

    def _get_tool_rules(self, tool_name: str) -> str | dict | None:
        if tool_name in self.rules:
            return self.rules[tool_name]
        if "*" in self.rules:
            global_rule = self.rules["*"]
            if isinstance(global_rule, str):
                return global_rule
            return None
        return None

    def _extract_target(self, tool_name: str, tool_args: dict) -> str:
        if tool_name == "bash" and "command" in tool_args:
            return tool_args["command"]
        if "path" in tool_args:
            return tool_args["path"]
        return " ".join(str(v) for v in tool_args.values())

    def to_dict(self) -> dict:
        return self.rules.copy()

    @classmethod
    def from_dict(cls, data: dict) -> "PermissionConfig":
        if not data:
            return cls()
        return cls(rules=data.copy())


class PermissionHandler(ABC):
    @abstractmethod
    async def ask_permission(self, tool_name: str, tool_args: dict) -> str: ...


class DefaultPermissionHandler(PermissionHandler):
    async def ask_permission(self, tool_name: str, tool_args: dict) -> str:
        return "allow"


class DenyAllPermissionHandler(PermissionHandler):
    async def ask_permission(self, tool_name: str, tool_args: dict) -> str:
        return "deny"


class PermissionChecker:
    """权限检查器 — 合并规则匹配、模式覆盖和用户交互"""

    def __init__(
        self,
        permission_config: PermissionConfig | dict | None = None,
        handler: PermissionHandler | None = None,
        config: "Config | None" = None,
    ):
        if permission_config is not None:
            if isinstance(permission_config, dict):
                self._perm_config = PermissionConfig.from_dict(permission_config)
            else:
                self._perm_config = permission_config
        elif config and hasattr(config, "permission"):
            self._perm_config = config.permission
        else:
            self._perm_config = PermissionConfig()
        self._handler = handler
        self._config = config

    @property
    def _current_mode(self) -> str:
        return self._config.current_mode if self._config else "normal"

    @property
    def _modes(self) -> dict:
        return self._config.modes if self._config else {}

    def _is_dangerous_command(self, tool_name: str, tool_args: dict, mode_config) -> bool:
        if tool_name != "bash":
            return False
        command = tool_args.get("command", "")
        if not command:
            return False
        for pattern in mode_config.dangerous_patterns:
            if command.startswith(pattern):
                return True
        return False

    def _should_auto_approve(self, tool_name: str, tool_args: dict) -> bool:
        if self._current_mode == "normal":
            return False
        mode_config = self._modes.get(self._current_mode)
        if not mode_config or not mode_config.auto_approve:
            return False
        return not self._is_dangerous_command(tool_name, tool_args, mode_config)

    async def check(self, tool_name: str, tool_args: dict) -> CheckResult:
        if self._should_auto_approve(tool_name, tool_args):
            return CheckResult(outcome=CheckOutcome.ALLOW)

        action = self._perm_config.get_action(tool_name, tool_args)

        if action == PermissionAction.DENY:
            return CheckResult(outcome=CheckOutcome.DENY, reason="denied")

        if action == PermissionAction.ASK:
            if not self._handler:
                return CheckResult(outcome=CheckOutcome.DENY, reason=f"No permission handler for tool '{tool_name}'")

            user_response = await self._handler.ask_permission(tool_name, tool_args)

            if user_response == "deny":
                return CheckResult(outcome=CheckOutcome.DENY, reason="denied")
            elif user_response == "interrupt":
                return CheckResult(outcome=CheckOutcome.DENY, reason="interrupted")
            elif user_response == "allow":
                return CheckResult(outcome=CheckOutcome.ALLOW)
            else:
                return CheckResult(outcome=CheckOutcome.USER_INPUT, user_input=user_response)

        return CheckResult(outcome=CheckOutcome.ALLOW)
