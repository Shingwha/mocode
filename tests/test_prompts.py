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

    @pytest.mark.asyncio
    async def test_returns_none_on_cancel(self):
        with patch("mocode.app.cli.prompts.questionary.select") as mock_q:
            instance = mock_q.return_value
            instance.ask_async = AsyncMock(return_value=None)

            result = await select("Pick:", ["a", "b"])

            assert result is None

    @pytest.mark.asyncio
    async def test_passes_default(self):
        with patch("mocode.app.cli.prompts.questionary.select") as mock_q:
            instance = mock_q.return_value
            instance.ask_async = AsyncMock(return_value="b")

            await select("Pick:", ["a", "b"], default="b")

            mock_q.assert_called_once()
            assert mock_q.call_args[1]["default"] == "b"

    @pytest.mark.asyncio
    async def test_uses_custom_instruction(self):
        with patch("mocode.app.cli.prompts.questionary.select") as mock_q:
            instance = mock_q.return_value
            instance.ask_async = AsyncMock(return_value="a")

            await select("Pick:", ["a"], instruction="use arrows")

            assert mock_q.call_args[1]["instruction"] == "use arrows"


class TestMultiselect:
    @pytest.mark.asyncio
    async def test_returns_list(self):
        with patch("mocode.app.cli.prompts.questionary.checkbox") as mock_q:
            instance = mock_q.return_value
            instance.ask_async = AsyncMock(return_value=["read", "write"])

            result = await multiselect("Toggle:", ["read", "write", "bash"])

            assert result == ["read", "write"]

    @pytest.mark.asyncio
    async def test_returns_none_on_cancel(self):
        with patch("mocode.app.cli.prompts.questionary.checkbox") as mock_q:
            instance = mock_q.return_value
            instance.ask_async = AsyncMock(return_value=None)

            result = await multiselect("Toggle:", ["a", "b"])

            assert result is None

    @pytest.mark.asyncio
    async def test_checked_preselects(self):
        with patch("mocode.app.cli.prompts.questionary.checkbox") as mock_q:
            instance = mock_q.return_value
            instance.ask_async = AsyncMock(return_value=["a", "c"])

            await multiselect("Toggle:", ["a", "b", "c"], checked=["a"])

            # The choices passed to checkbox should include pre-checked items
            choices = mock_q.call_args[1]["choices"]
            # First choice "a" should be a Choice with checked=True
            from questionary import Choice

            assert any(isinstance(c, Choice) and c.checked for c in choices)


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

    @pytest.mark.asyncio
    async def test_returns_none_on_cancel(self):
        with patch("mocode.app.cli.prompts.questionary.confirm") as mock_q:
            instance = mock_q.return_value
            instance.ask_async = AsyncMock(return_value=None)

            result = await confirm("Proceed?")

            assert result is None

    @pytest.mark.asyncio
    async def test_default_false(self):
        with patch("mocode.app.cli.prompts.questionary.confirm") as mock_q:
            instance = mock_q.return_value
            instance.ask_async = AsyncMock(return_value=False)

            await confirm("Sure?", default=False)

            assert mock_q.call_args[1]["default"] is False


class TestTextInput:
    @pytest.mark.asyncio
    async def test_returns_string(self):
        with patch("mocode.app.cli.prompts.questionary.text") as mock_q:
            instance = mock_q.return_value
            instance.ask_async = AsyncMock(return_value="hello")

            result = await text_input("Name:")

            assert result == "hello"

    @pytest.mark.asyncio
    async def test_returns_none_on_cancel(self):
        with patch("mocode.app.cli.prompts.questionary.text") as mock_q:
            instance = mock_q.return_value
            instance.ask_async = AsyncMock(return_value=None)

            result = await text_input("Name:")

            assert result is None

    @pytest.mark.asyncio
    async def test_passes_validate(self):
        with patch("mocode.app.cli.prompts.questionary.text") as mock_q:
            instance = mock_q.return_value
            instance.ask_async = AsyncMock(return_value="ok")

            def validator(x):
                return len(x) >= 2 or "too short"

            await text_input("Name:", validate=validator)

            assert mock_q.call_args[1]["validate"] is validator

    @pytest.mark.asyncio
    async def test_passes_multiline(self):
        with patch("mocode.app.cli.prompts.questionary.text") as mock_q:
            instance = mock_q.return_value
            instance.ask_async = AsyncMock(return_value="line1\nline2")

            await text_input("Desc:", multiline=True)

            assert mock_q.call_args[1]["multiline"] is True
