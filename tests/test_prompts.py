"""Tests for mocode.app.cli.prompts — interactive CLI prompt wrappers."""

from unittest.mock import AsyncMock, patch

import pytest

from mocode.app.cli.prompts import confirm, multiselect, select, text_input


@pytest.fixture(autouse=True)
def _reset_style_cache():
    """Reset the module-level style cache so tests get a fresh one."""
    import mocode.app.cli.prompts as mod

    original = mod._style
    mod._style = None
    yield
    mod._style = original


class TestSelect:
    @pytest.mark.asyncio
    async def test_returns_chosen_value(self):
        with patch("mocode.app.cli.prompts.questionary.select") as mock_q:
            instance = mock_q.return_value
            instance.ask_async = AsyncMock(return_value="gpt-4.1")

            result = await select("Pick a model:", ["gpt-4.1", "claude-sonnet-4-6"])

            assert result == "gpt-4.1"


class TestMultiselect:
    @pytest.mark.asyncio
    async def test_returns_list(self):
        with patch("mocode.app.cli.prompts.questionary.checkbox") as mock_q:
            instance = mock_q.return_value
            instance.ask_async = AsyncMock(return_value=["read", "write"])

            result = await multiselect("Toggle:", ["read", "write", "bash"])

            assert result == ["read", "write"]


class TestConfirm:
    @pytest.mark.asyncio
    async def test_returns_true(self):
        with patch("mocode.app.cli.prompts.questionary.confirm") as mock_q:
            instance = mock_q.return_value
            instance.ask_async = AsyncMock(return_value=True)

            result = await confirm("Proceed?")

            assert result is True

    @pytest.mark.asyncio
    async def test_returns_false(self):
        with patch("mocode.app.cli.prompts.questionary.confirm") as mock_q:
            instance = mock_q.return_value
            instance.ask_async = AsyncMock(return_value=False)

            result = await confirm("Proceed?")

            assert result is False


class TestTextInput:
    @pytest.mark.asyncio
    async def test_returns_string(self):
        with patch("mocode.app.cli.prompts.questionary.text") as mock_q:
            instance = mock_q.return_value
            instance.ask_async = AsyncMock(return_value="hello")

            result = await text_input("Name:")

            assert result == "hello"
