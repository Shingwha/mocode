"""Tests for /connect command — connect functions with mock CLIApp."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mocode.app.cli.app import CLIApp
from mocode.app.cli.commands import CommandContext, CommandResult
from mocode.app.cli.commands.builtin import _connect, _connect_add, _connect_edit
from mocode.app.config import Config, ProviderEntry, ModelEntry
from mocode.app.session import SessionManager


def _make_app() -> CLIApp:
    """Create a CLIApp with a minimal config — no real provider calls needed."""
    config = Config(
        active_provider="deepseek",
        active_model="deepseek-chat",
        providers={
            "deepseek": ProviderEntry(
                name="DeepSeek",
                api_key="sk-test1234abcd",
                base_url="https://api.deepseek.com",
                models=[
                    ModelEntry(name="deepseek-chat"),
                    ModelEntry(name="deepseek-reasoner"),
                ],
            ),
            "zhipu": ProviderEntry(
                name="智谱",
                api_key="sk-zhipu5678efgh",
                models=[
                    ModelEntry(name="glm-5"),
                    ModelEntry(name="glm-5.1"),
                ],
            ),
        },
    )
    config.save = MagicMock()

    mock_display = MagicMock()
    mock_session_mgr = MagicMock(spec=SessionManager)
    with (
        patch.object(
            CLIApp,
            "_build_agent",
            return_value=MagicMock(messages=[], system_prompt=""),
        ),
        patch.object(CLIApp, "_build_prompt", return_value="test-prompt"),
        patch("mocode.app.cli.app.SessionManager", return_value=mock_session_mgr),
    ):
        app = CLIApp(config=config, display=mock_display)
    app._session_mgr = mock_session_mgr
    return app


def _make_ctx(app: CLIApp, args: str = "") -> CommandContext:
    return CommandContext(app=app, args=args, display=app.display)


class TestConnectAdd:
    @pytest.mark.asyncio
    async def test_happy_path(self):
        app = _make_app()
        with (
            patch(
                "mocode.app.cli.commands.connect.text_input",
                new_callable=AsyncMock,
                side_effect=[
                    "openai",  # key
                    "OpenAI",  # name
                    "https://api.openai.com",  # base_url
                    "sk-openai1234",  # api_key
                    "gpt-4.1, o3",  # models
                ],
            ),
        ):
            await _connect_add(_make_ctx(app))

        assert "openai" in app.config.providers
        entry = app.config.providers["openai"]
        assert entry.api_key == "sk-openai1234"
        assert entry.base_url == "https://api.openai.com"
        assert entry.name == "OpenAI"
        assert entry.model_names() == ["gpt-4.1", "o3"]
        app.config.save.assert_called()


class TestConnectEdit:
    @pytest.mark.asyncio
    async def test_rename_display_name(self):
        app = _make_app()
        with (
            patch(
                "mocode.app.cli.commands.connect.select",
                new_callable=AsyncMock,
                side_effect=["name", "back"],
            ),
            patch(
                "mocode.app.cli.commands.connect.text_input",
                new_callable=AsyncMock,
                return_value="DeepSeek Renamed",
            ),
        ):
            await _connect_edit(_make_ctx(app), "deepseek")

        assert app.config.providers["deepseek"].name == "DeepSeek Renamed"
        app.config.save.assert_called()

    @pytest.mark.asyncio
    async def test_delete_inactive_provider(self):
        app = _make_app()
        with (
            patch(
                "mocode.app.cli.commands.connect.select",
                new_callable=AsyncMock,
                side_effect=["delete"],
            ),
            patch(
                "mocode.app.cli.commands.connect.confirm",
                new_callable=AsyncMock,
                return_value=True,
            ),
        ):
            await _connect_edit(_make_ctx(app), "zhipu")

        assert "zhipu" not in app.config.providers
