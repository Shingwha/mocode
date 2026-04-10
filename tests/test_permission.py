"""Permission system tests"""

import pytest

from mocode.core.permission import (
    CheckOutcome,
    CheckResult,
    DenyAllPermissionHandler,
    DefaultPermissionHandler,
    PermissionAction,
    PermissionChecker,
    PermissionConfig,
    ToolPermissionRules,
)
from mocode.core.config import Config


class TestPermissionConfig:
    def test_flat_rule_allow(self):
        pc = PermissionConfig.from_dict({"bash": "allow"})
        assert pc.get_action("bash") == PermissionAction.ALLOW

    def test_flat_rule_deny(self):
        pc = PermissionConfig.from_dict({"write": "deny"})
        assert pc.get_action("write") == PermissionAction.DENY

    def test_wildcard_default(self):
        pc = PermissionConfig.from_dict({"*": "ask"})
        assert pc.get_action("unknown_tool") == PermissionAction.ASK

    def test_exact_match_over_wildcard(self):
        pc = PermissionConfig.from_dict({"*": "ask", "read": "allow"})
        assert pc.get_action("read") == PermissionAction.ALLOW
        assert pc.get_action("other") == PermissionAction.ASK

    def test_nested_bash_rules(self):
        pc = PermissionConfig.from_dict({
            "bash": {"*": "ask", "git *": "allow", "rm *": "deny"}
        })
        assert pc.get_action("bash", {"command": "git status"}) == PermissionAction.ALLOW
        assert pc.get_action("bash", {"command": "rm -rf /"}) == PermissionAction.DENY
        assert pc.get_action("bash", {"command": "ls"}) == PermissionAction.ASK

    def test_no_rules_returns_ask(self):
        pc = PermissionConfig()
        assert pc.get_action("anything") == PermissionAction.ASK

    def test_extract_target_bash(self):
        pc = PermissionConfig.from_dict({})
        target = pc._extract_target("bash", {"command": "git status"})
        assert target == "git status"

    def test_extract_target_file(self):
        pc = PermissionConfig.from_dict({})
        target = pc._extract_target("read", {"path": "/tmp/file.txt"})
        assert target == "/tmp/file.txt"

    def test_to_dict(self):
        rules = {"*": "ask", "bash": "allow"}
        pc = PermissionConfig.from_dict(rules)
        assert pc.to_dict() == rules


class TestToolPermissionRules:
    def test_exact_match_priority(self):
        rules = ToolPermissionRules(rules={"ls": "allow", "*": "ask"})
        assert rules.get_action("ls") == PermissionAction.ALLOW
        assert rules.get_action("other") == PermissionAction.ASK

    def test_prefix_match(self):
        rules = ToolPermissionRules(rules={"git *": "allow", "*": "ask"})
        assert rules.get_action("git status") == PermissionAction.ALLOW
        assert rules.get_action("git") == PermissionAction.ALLOW  # exact prefix

    def test_glob_match(self):
        rules = ToolPermissionRules(rules={"src/**": "allow", "*": "ask"})
        assert rules.get_action("src/main.py") == PermissionAction.ALLOW


class TestPermissionChecker:
    @pytest.fixture
    def normal_config(self):
        return Config.load(data={
            "current": {"provider": "test", "model": "m"},
            "providers": {"test": {"name": "T", "base_url": "https://t.com/v1", "api_key": "k", "models": ["m"]}},
            "permission": {"*": "ask", "bash": "allow", "write": "deny"},
        })

    @pytest.fixture
    def yolo_config(self):
        return Config.load(data={
            "current": {"provider": "test", "model": "m"},
            "providers": {"test": {"name": "T", "base_url": "https://t.com/v1", "api_key": "k", "models": ["m"]}},
            "permission": {"*": "ask"},
        })

    @pytest.mark.asyncio
    async def test_checker_normal_mode_allow(self, normal_config):
        checker = PermissionChecker(config=normal_config)
        result = await checker.check("bash", {"command": "ls"})
        assert result.outcome == CheckOutcome.ALLOW

    @pytest.mark.asyncio
    async def test_checker_normal_mode_deny(self, normal_config):
        checker = PermissionChecker(config=normal_config)
        result = await checker.check("write", {"path": "/tmp/f"})
        assert result.outcome == CheckOutcome.DENY

    @pytest.mark.asyncio
    async def test_checker_yolo_mode_safe(self, yolo_config):
        yolo_config.set_mode("yolo")
        checker = PermissionChecker(config=yolo_config)
        result = await checker.check("bash", {"command": "ls -la"})
        assert result.outcome == CheckOutcome.ALLOW

    @pytest.mark.asyncio
    async def test_checker_yolo_dangerous(self, yolo_config):
        yolo_config.set_mode("yolo")
        checker = PermissionChecker(config=yolo_config)
        result = await checker.check("bash", {"command": "rm -rf /"})
        # Dangerous command in yolo mode should still be checked against rules
        # Since rules have "*": "ask", and no handler => deny
        assert result.outcome == CheckOutcome.DENY

    @pytest.mark.asyncio
    async def test_checker_deny_handler(self):
        checker = PermissionChecker(
            permission_config={"*": "ask"},
            handler=DenyAllPermissionHandler(),
        )
        result = await checker.check("any_tool", {})
        assert result.outcome == CheckOutcome.DENY

    @pytest.mark.asyncio
    async def test_checker_user_input(self):
        class CustomHandler:
            async def ask_permission(self, tool_name, tool_args):
                return "my custom result"

        checker = PermissionChecker(
            permission_config={"*": "ask"},
            handler=CustomHandler(),
        )
        result = await checker.check("any_tool", {})
        assert result.outcome == CheckOutcome.USER_INPUT
        assert result.user_input == "my custom result"

    @pytest.mark.asyncio
    async def test_no_handler_returns_deny(self):
        checker = PermissionChecker(permission_config={"*": "ask"})
        result = await checker.check("tool", {})
        assert result.outcome == CheckOutcome.DENY
        assert "No permission handler" in result.reason

    @pytest.mark.asyncio
    async def test_default_handler_allows(self):
        checker = PermissionChecker(
            permission_config={"*": "ask"},
            handler=DefaultPermissionHandler(),
        )
        result = await checker.check("tool", {})
        assert result.outcome == CheckOutcome.ALLOW
