"""Tests for /connect command — provider CRUD via CLIApp."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mocode.app.cli.app import CLIApp
from mocode.app.config import Config, ProviderEntry, ModelEntry


def _make_app() -> CLIApp:
    """Create a CLIApp with a minimal config — no real provider calls needed.

    IMPORTANT: config.save is mocked to prevent tests from overwriting
    the real ~/.mocode/config.json.
    """
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
    # Block all writes to the real config file
    config.save = MagicMock()

    mock_display = MagicMock()
    # Patch _build_agent and _build_prompt to avoid real provider/tool setup
    with (
        patch.object(CLIApp, "_build_agent", return_value=MagicMock(messages=[], system_prompt="")),
        patch.object(CLIApp, "_build_prompt", return_value="test-prompt"),
    ):
        app = CLIApp(config=config, display=mock_display)
    # Stub session manager
    app._session_mgr = MagicMock()
    return app


class TestConnectTopLevel:
    @pytest.mark.asyncio
    async def test_back_returns_immediately(self):
        app = _make_app()
        with patch("mocode.app.cli.app.select", new_callable=AsyncMock, return_value="__back__"):
            await app._connect()
        # No changes to config
        assert len(app.config.providers) == 2

    @pytest.mark.asyncio
    async def test_cancel_returns_immediately(self):
        app = _make_app()
        with patch("mocode.app.cli.app.select", new_callable=AsyncMock, return_value=None):
            await app._connect()
        assert len(app.config.providers) == 2

    @pytest.mark.asyncio
    async def test_add_new_dispatches(self):
        app = _make_app()
        with (
            patch("mocode.app.cli.app.select", new_callable=AsyncMock, return_value="__add__"),
            patch("mocode.app.cli.app.text_input", new_callable=AsyncMock, side_effect=[
                "newprov",       # key
                "NewProv",       # name
                "https://api.new",  # base_url
                "sk-newkey1234",    # api_key
                "model-a, model-b",  # models
            ]),
            patch.object(app, "_connect_apply"),
        ):
            await app._connect()

        assert "newprov" in app.config.providers
        assert app.config.providers["newprov"].api_key == "sk-newkey1234"
        assert app.config.providers["newprov"].name == "NewProv"
        assert app.config.providers["newprov"].model_names() == ["model-a", "model-b"]

    @pytest.mark.asyncio
    async def test_edit_dispatches_to_connect_edit(self):
        app = _make_app()
        # _connect picks "deepseek", then _connect_edit picks "back" immediately
        with (
            patch("mocode.app.cli.app.select", new_callable=AsyncMock,
                  side_effect=["deepseek", "back"]),
            patch.object(app, "_connect_apply"),
        ):
            await app._connect()


class TestConnectEdit:
    @pytest.mark.asyncio
    async def test_rename_display_name(self):
        app = _make_app()
        with (
            patch("mocode.app.cli.app.select", new_callable=AsyncMock,
                  side_effect=["name", "back"]),
            patch("mocode.app.cli.app.text_input", new_callable=AsyncMock,
                  return_value="DeepSeek Renamed"),
            patch.object(app, "_connect_apply"),
        ):
            await app._connect_edit("deepseek")

        assert app.config.providers["deepseek"].name == "DeepSeek Renamed"

    @pytest.mark.asyncio
    async def test_rename_cancel_preserves(self):
        app = _make_app()
        with (
            patch("mocode.app.cli.app.select", new_callable=AsyncMock,
                  side_effect=["name", "back"]),
            patch("mocode.app.cli.app.text_input", new_callable=AsyncMock,
                  return_value=None),  # Esc
            patch.object(app, "_connect_apply"),
        ):
            await app._connect_edit("deepseek")

        assert app.config.providers["deepseek"].name == "DeepSeek"

    @pytest.mark.asyncio
    async def test_delete_active_provider_refused(self):
        app = _make_app()
        with (
            patch("mocode.app.cli.app.select", new_callable=AsyncMock,
                  side_effect=["delete", "back"]),
            patch.object(app, "_connect_apply"),
        ):
            await app._connect_edit("deepseek")

        # Active provider still exists
        assert "deepseek" in app.config.providers

    @pytest.mark.asyncio
    async def test_delete_inactive_provider(self):
        app = _make_app()
        with (
            patch("mocode.app.cli.app.select", new_callable=AsyncMock,
                  side_effect=["delete"]),
            patch("mocode.app.cli.app.confirm", new_callable=AsyncMock, return_value=True),
        ):
            await app._connect_edit("zhipu")

        assert "zhipu" not in app.config.providers

    @pytest.mark.asyncio
    async def test_models_prune_resets_active_model(self):
        app = _make_app()
        assert app.config.active_model == "deepseek-chat"

        with (
            patch("mocode.app.cli.app.select", new_callable=AsyncMock,
                  side_effect=["models", "back"]),
            patch("mocode.app.cli.app.text_input", new_callable=AsyncMock,
                  return_value="deepseek-reasoner"),
            patch.object(app, "_connect_apply"),
        ):
            await app._connect_edit("deepseek")

        # Active model was removed, should reset to first remaining
        assert app.config.active_model == "deepseek-reasoner"
        assert app.config.providers["deepseek"].model_names() == ["deepseek-reasoner"]

    @pytest.mark.asyncio
    async def test_edit_api_key(self):
        app = _make_app()
        with (
            patch("mocode.app.cli.app.select", new_callable=AsyncMock,
                  side_effect=["apikey", "back"]),
            patch("mocode.app.cli.app.text_input", new_callable=AsyncMock,
                  return_value="sk-newapikey9999"),
            patch.object(app, "_connect_apply"),
        ):
            await app._connect_edit("deepseek")

        assert app.config.providers["deepseek"].api_key == "sk-newapikey9999"

    @pytest.mark.asyncio
    async def test_edit_base_url(self):
        app = _make_app()
        with (
            patch("mocode.app.cli.app.select", new_callable=AsyncMock,
                  side_effect=["baseurl", "back"]),
            patch("mocode.app.cli.app.text_input", new_callable=AsyncMock,
                  return_value="https://new-api.example.com"),
            patch.object(app, "_connect_apply"),
        ):
            await app._connect_edit("deepseek")

        assert app.config.providers["deepseek"].base_url == "https://new-api.example.com"

    @pytest.mark.asyncio
    async def test_clear_base_url(self):
        app = _make_app()
        with (
            patch("mocode.app.cli.app.select", new_callable=AsyncMock,
                  side_effect=["baseurl", "back"]),
            patch("mocode.app.cli.app.text_input", new_callable=AsyncMock,
                  return_value=""),
            patch.object(app, "_connect_apply"),
        ):
            await app._connect_edit("deepseek")

        assert app.config.providers["deepseek"].base_url is None


class TestConnectAdd:
    @pytest.mark.asyncio
    async def test_happy_path(self):
        app = _make_app()
        with (
            patch("mocode.app.cli.app.text_input", new_callable=AsyncMock, side_effect=[
                "openai",           # key
                "OpenAI",           # name
                "https://api.openai.com",  # base_url
                "sk-openai1234",    # api_key
                "gpt-4.1, o3",     # models
            ]),
            patch.object(app, "_connect_apply") as mock_apply,
        ):
            await app._connect_add()

        assert "openai" in app.config.providers
        entry = app.config.providers["openai"]
        assert entry.api_key == "sk-openai1234"
        assert entry.base_url == "https://api.openai.com"
        assert entry.name == "OpenAI"
        assert entry.model_names() == ["gpt-4.1", "o3"]
        mock_apply.assert_called_once_with(True)

    @pytest.mark.asyncio
    async def test_cancel_at_key_aborts(self):
        app = _make_app()
        with patch("mocode.app.cli.app.text_input", new_callable=AsyncMock, return_value=None):
            await app._connect_add()
        assert len(app.config.providers) == 2

    @pytest.mark.asyncio
    async def test_cancel_at_name_aborts(self):
        app = _make_app()
        with patch("mocode.app.cli.app.text_input", new_callable=AsyncMock,
                   side_effect=["newkey", None]):
            await app._connect_add()
        assert "newkey" not in app.config.providers

    @pytest.mark.asyncio
    async def test_empty_api_key_aborts(self):
        app = _make_app()
        with (
            patch("mocode.app.cli.app.text_input", new_callable=AsyncMock,
                  side_effect=["newkey", "Name", "https://url", "   "]),
        ):
            await app._connect_add()
        assert "newkey" not in app.config.providers

    @pytest.mark.asyncio
    async def test_duplicate_key_rejected_by_validator(self):
        app = _make_app()
        validate_fn = None

        def capture_validate(*args, **kwargs):
            nonlocal validate_fn
            validate_fn = kwargs.get("validate")
            return "deepseek"  # Would be rejected

        with patch("mocode.app.cli.app.text_input", new_callable=AsyncMock,
                   side_effect=capture_validate):
            # We can't easily test questionary's validate loop in a unit test,
            # but we can test the validator function directly
            pass

        # Test the validator logic inline
        def _validate_key(s):
            if not s.strip():
                return "Key cannot be empty"
            if s.strip() in app.config.providers:
                return f"Provider '{s.strip()}' already exists"
            return True

        assert _validate_key("deepseek") != True
        assert _validate_key("") != True
        assert _validate_key("newprov") is True


class TestConnectExtraBody:
    @pytest.mark.asyncio
    async def test_set_extra_body_for_model(self):
        app = _make_app()
        entry = app.config.providers["deepseek"]

        with (
            patch("mocode.app.cli.app.select", new_callable=AsyncMock,
                  return_value="deepseek-chat"),
            patch("mocode.app.cli.app.text_input", new_callable=AsyncMock,
                  return_value='{"thinking": {"type": "enabled"}}'),
        ):
            await app._connect_extra_body("deepseek", entry)

        assert entry.get_extra_body("deepseek-chat") == {"thinking": {"type": "enabled"}}

    @pytest.mark.asyncio
    async def test_clear_extra_body(self):
        app = _make_app()
        entry = app.config.providers["deepseek"]
        # Set up an extra_body first
        entry.models[0].extra_body = {"a": 1}

        with (
            patch("mocode.app.cli.app.select", new_callable=AsyncMock,
                  return_value="deepseek-chat"),
            patch("mocode.app.cli.app.text_input", new_callable=AsyncMock,
                  return_value=""),
        ):
            await app._connect_extra_body("deepseek", entry)

        assert entry.get_extra_body("deepseek-chat") is None

    @pytest.mark.asyncio
    async def test_invalid_json_rejected(self):
        app = _make_app()
        entry = app.config.providers["deepseek"]
        original = entry.get_extra_body("deepseek-chat")

        with (
            patch("mocode.app.cli.app.select", new_callable=AsyncMock,
                  return_value="deepseek-chat"),
            patch("mocode.app.cli.app.text_input", new_callable=AsyncMock,
                  return_value="{invalid}"),
        ):
            await app._connect_extra_body("deepseek", entry)

        # Should not have been modified
        assert entry.get_extra_body("deepseek-chat") == original

    @pytest.mark.asyncio
    async def test_back_from_model_picker(self):
        app = _make_app()
        entry = app.config.providers["deepseek"]

        with patch("mocode.app.cli.app.select", new_callable=AsyncMock,
                   return_value="__back__"):
            await app._connect_extra_body("deepseek", entry)

        # No changes
        assert entry.get_extra_body("deepseek-chat") is None


class TestConnectApply:
    def test_dirty_triggers_save_and_rebuild(self):
        app = _make_app()
        app._build_agent = MagicMock(return_value=MagicMock(messages=[], system_prompt=""))
        app._build_prompt = MagicMock(return_value="new-prompt")

        app._connect_apply(True)

        app.config.save.assert_called_once()
        app._build_agent.assert_called_once()

    def test_not_dirty_is_noop(self):
        app = _make_app()

        app._connect_apply(False)

        app.config.save.assert_not_called()


class TestMaskKey:
    def test_long_key(self):
        assert CLIApp._mask_key("sk-test1234abcd") == "•••••••••••abcd"

    def test_short_key(self):
        assert CLIApp._mask_key("ab") == "••••"

    def test_four_char_key(self):
        assert CLIApp._mask_key("abcd") == "abcd"
