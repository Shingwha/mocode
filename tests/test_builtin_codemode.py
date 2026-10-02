"""Tests for the codemode builtin plugin — runtime, api, output, search,
plugin and the end-to-end contract."""

from __future__ import annotations

import asyncio

import pytest

from mocode.host.plugin.builtin.codemode.runtime import (
    RESTRICTED,
    CodemodeError,
    _ScriptExit,
    run_script,
)


# ── T1: runtime ─────────────────────────────────────────────


class TestRunScript:
    async def test_top_level_await_and_return(self):
        async def helper() -> int:
            await asyncio.sleep(0)
            return 42

        env = {"helper": helper}
        assert await run_script("await helper()\nreturn 21 * 2", env) == 42

    async def test_no_return_is_none(self):
        assert await run_script("x = 1 + 1", {}) is None

    async def test_script_sees_injected_env(self):
        assert await run_script("return a + b", {"a": 2, "b": 3}) == 5

    @pytest.mark.parametrize("script", ["", "   \n\t\n  "])
    async def test_empty_script(self, script: str):
        with pytest.raises(CodemodeError, match="script is empty"):
            await run_script(script, {})

    async def test_syntax_error_carries_location(self):
        with pytest.raises(SyntaxError) as exc_info:
            await run_script("def broken(:", {})
        assert "<codemode>" in str(exc_info.value)

    async def test_script_exception_propagates(self):
        # ValueError is on the restricted whitelist; the message and type
        # reach the caller, which renders the script-failed result.
        with pytest.raises(ValueError, match="boom"):
            await run_script("raise ValueError('boom')", {})

    async def test_script_exit_propagates(self):
        # The injected exit() raises _ScriptExit; run_script lets it through
        # and the tool layer turns it into success.
        def exit():
            raise _ScriptExit()

        with pytest.raises(_ScriptExit):
            await run_script("exit()", {"exit": exit})

    async def test_cancellation_propagates(self):
        async def blocker():
            await asyncio.sleep(60)

        task = asyncio.create_task(run_script("await blocker()", {"blocker": blocker}))
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    async def test_restricted_builtins_allow_common_ops(self):
        assert await run_script("return sum([1, 2, 3])", {}) == 6
        assert await run_script("return sorted([3, 1, 2])", {}) == [1, 2, 3]

    @pytest.mark.parametrize("name", ["open", "__import__", "eval", "exec", "input"])
    async def test_restricted_builtins_hide_dangerous_names(self, name: str):
        with pytest.raises(NameError):
            await run_script(f"{name}", {})

    async def test_restricted_has_no_exit(self):
        # Python's own exit/quit are absent; the injected exit() is the only one.
        with pytest.raises(NameError):
            await run_script("exit", {})

    async def test_restricted_is_not_the_real_builtins(self):
        assert RESTRICTED != __builtins__ if isinstance(__builtins__, dict) else True
        assert "open" not in RESTRICTED
        assert "len" in RESTRICTED
