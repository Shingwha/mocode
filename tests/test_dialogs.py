"""Tests for mocode.app.cli.dialogs — the questionary wrappers."""

from unittest.mock import AsyncMock, patch

import pytest

from mocode.app.cli.dialogs import select


@pytest.fixture(autouse=True)
def _reset_style_cache():
    """Reset the module-level style cache so tests get a fresh one."""
    import mocode.app.cli.dialogs as mod

    original = mod._style
    mod._style = None
    yield
    mod._style = original


class TestSelect:
    @pytest.mark.asyncio
    async def test_returns_chosen_value(self):
        with patch("questionary.select") as mock_q:
            mock_q.return_value.ask_async = AsyncMock(return_value="gpt-4.1")

            result = await select("Pick a model:", ["gpt-4.1", "claude-sonnet-4-6"])

            assert result == "gpt-4.1"

    @pytest.mark.asyncio
    async def test_returns_none_when_cancelled(self):
        with patch("questionary.select") as mock_q:
            mock_q.return_value.ask_async = AsyncMock(return_value=None)

            assert await select("Pick:", ["a"]) is None


class TestLazyChoice:
    def test_choice_is_resolved_on_demand(self):
        import mocode.app.cli.dialogs as mod

        assert "Choice" not in vars(mod)  # nothing imported until asked for
        assert mod.Choice is not None
        assert "Choice" in vars(mod)  # cached after first access

    def test_unknown_attribute_raises(self):
        import mocode.app.cli.dialogs as mod

        with pytest.raises(AttributeError):
            mod.nonexistent
