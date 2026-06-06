"""Tests for BashSession — env var injection and basic functionality."""

from __future__ import annotations

import pytest

from mocode.tools.bash import BashSession


class TestBashSession:
    """BashSession core behavior."""

    def test_basic_command(self):
        session = BashSession()
        result = session.execute("echo hello")
        assert result == "hello"

    def test_cd_changes_directory(self, tmp_path):
        session = BashSession()
        result = session.execute(f"cd {tmp_path}")
        assert str(tmp_path) in result
        assert session.cwd == str(tmp_path)

    def test_env_var_persists_across_commands(self):
        session = BashSession()
        session.execute("export MY_TEST_VAR=world")
        result = session.execute("echo $MY_TEST_VAR")
        assert result == "world"

    def test_restart_clears_state(self):
        session = BashSession()
        session.execute("export MY_TEST_VAR=hello")
        session.restart()
        result = session.execute("echo $MY_TEST_VAR")
        assert result in ("", "(empty)")

    # ---- Security: env var injection regression ----

    def test_env_var_value_not_executed_as_shell_code(self):
        """Regression test for shell injection via env var values.

        If the value were interpolated into a shell string without escaping,
        bash would execute $(echo INJECTED) as a subshell.
        After the fix (env= parameter), the value is passed as-is and never
        parsed as shell code.
        """
        session = BashSession()
        # Store a value that looks like a command substitution
        session.execute("export EVIL='$(echo INJECTED)'")
        result = session.execute("echo $EVIL")
        # The literal string $(echo INJECTED) should appear, NOT "INJECTED"
        assert result == "$(echo INJECTED)"

    def test_env_var_backtick_not_executed(self):
        """Backtick command substitution in env var values must not run."""
        session = BashSession()
        session.execute("export EVIL='`echo INJECTED`'")
        result = session.execute("echo $EVIL")
        assert result == "`echo INJECTED`"

    def test_env_var_with_quotes_and_special_chars(self):
        """Env var values with quotes and spaces are preserved literally."""
        session = BashSession()
        session.execute('export MSG="hello world"')
        result = session.execute("echo $MSG")
        assert result == "hello world"

    def test_multiple_env_vars_all_safe(self):
        """Multiple stored env vars are all passed safely."""
        session = BashSession()
        session.execute("export A=alpha")
        session.execute("export B='$(whoami)'")
        result = session.execute("echo $A $B")
        assert "alpha" in result
        assert "$(whoami)" in result
