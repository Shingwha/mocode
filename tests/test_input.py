"""Tests for Input — SlashCompleter and keybindings."""

from __future__ import annotations

import asyncio

import pytest
from unittest.mock import MagicMock

from prompt_toolkit.completion import Completion
from prompt_toolkit.document import Document
from prompt_toolkit.keys import Keys

from mocode.app.cli.commands import CommandRegistry
from mocode.app.cli.input import PasteStore, SlashCompleter, build_keybindings


async def _collect(completer, doc, event):
    """Collect completions from the async generator."""
    return [c async for c in completer.get_completions_async(doc, event)]

# prompt_toolkit normalises key names: "enter" → Keys.ControlM, "tab" → Keys.ControlI
_KEY_ALIASES: dict[str, tuple] = {
    "enter": (Keys.ControlM,),
    "tab": (Keys.ControlI,),
    "escape+enter": (Keys.Escape, Keys.ControlM),
    "c-j": (Keys.ControlJ,),
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_command(name: str, description: str = ""):
    cmd = MagicMock()
    cmd.name = name
    cmd.description = description
    cmd.aliases = ()
    return cmd


def _make_registry(*cmds) -> CommandRegistry:
    """Build a CommandRegistry from mock commands."""
    reg = CommandRegistry()
    for cmd in cmds:
        reg.register(cmd)
    return reg


def _find_handler(bindings, key):
    """Find the handler for a given key in the bindings."""
    target = _KEY_ALIASES.get(key, (key,))
    for binding in bindings.bindings:
        if binding.keys == target:
            return binding.handler
    raise KeyError(f"No handler for {key!r}")


def _make_event(buffer=None):
    """Create a minimal mock event for keybinding handlers."""
    event = MagicMock()
    event.current_buffer = buffer
    return event


def _make_buffer(text="", complete_state=None):
    """Create a MagicMock buffer with the given state."""
    buf = MagicMock()
    buf.text = text
    buf.complete_state = complete_state
    return buf


def _make_complete_state(completions, current_completion=None):
    """Create a mock CompletionState."""
    state = MagicMock()
    state.completions = completions
    state.current_completion = current_completion
    return state


# ---------------------------------------------------------------------------
# SlashCompleter tests
# ---------------------------------------------------------------------------

class TestSlashCompleter:
    def test_matches_slash_command(self):
        reg = _make_registry(
            _make_command("/help", "Show help"),
            _make_command("/quit", "Exit"),
        )
        completer = SlashCompleter(reg)
        doc = Document("/he")
        completions = asyncio.run(_collect(completer, doc, MagicMock()))
        assert len(completions) == 1
        assert completions[0].text == "/help"
        assert completions[0].display_meta[0][1] == "Show help"

    def test_multiple_matches(self):
        reg = _make_registry(
            _make_command("/export"),
            _make_command("/exit"),
            _make_command("/help"),
        )
        completer = SlashCompleter(reg)
        doc = Document("/e")
        completions = asyncio.run(_collect(completer, doc, MagicMock()))
        names = [c.text for c in completions]
        assert "/export" in names
        assert "/exit" in names
        assert "/help" not in names

    def test_exact_match_yields_self_and_children(self):
        """Exact match yields itself plus subcommands (Tab-triggered)."""
        reg = _make_registry(
            _make_command("/plan", "Create plans"),
            _make_command("/plan:start", "Start plan"),
            _make_command("/plan:clear", "Clear plan"),
        )
        completer = SlashCompleter(reg)
        doc = Document("/plan")
        completions = asyncio.run(_collect(completer, doc, MagicMock()))
        names = [c.text for c in completions]
        assert "/plan" in names
        assert "/plan:start" in names
        assert "/plan:clear" in names


# ---------------------------------------------------------------------------
# Keybinding tests — Enter
# ---------------------------------------------------------------------------

class TestEnterKeybinding:
    """Enter should apply completion when menu is visible, submit otherwise."""

    @pytest.fixture(autouse=True)
    def setup_bindings(self):
        self.bindings = build_keybindings()

    def test_enter_submits_when_no_completion_menu(self):
        handler = _find_handler(self.bindings, "enter")
        buf = _make_buffer("hello", complete_state=None)
        event = _make_event(buf)
        handler(event)
        buf.validate_and_handle.assert_called_once()

    def test_enter_applies_current_completion_when_selected(self):
        c1 = Completion("/help", start_position=-5)
        c2 = Completion("/history", start_position=-5)
        cs = _make_complete_state([c1, c2], current_completion=c1)
        handler = _find_handler(self.bindings, "enter")
        buf = _make_buffer("/hel", complete_state=cs)
        event = _make_event(buf)
        handler(event)
        buf.apply_completion.assert_called_once_with(c1)

    def test_enter_never_calls_cancel_completion(self):
        """Verify we no longer cancel+submit when the menu is open."""
        c1 = Completion("/help", start_position=-5)
        cs = _make_complete_state([c1], current_completion=None)
        handler = _find_handler(self.bindings, "enter")
        buf = _make_buffer("/h", complete_state=cs)
        event = _make_event(buf)
        handler(event)
        buf.cancel_completion.assert_not_called()
        buf.validate_and_handle.assert_not_called()


# ---------------------------------------------------------------------------
# Keybinding tests — Tab
# ---------------------------------------------------------------------------

class TestTabKeybinding:
    """Tab should apply current/first completion or start completion."""

    @pytest.fixture(autouse=True)
    def setup_bindings(self):
        self.bindings = build_keybindings()

    def test_tab_starts_completion_when_no_menu(self):
        handler = _find_handler(self.bindings, "tab")
        buf = _make_buffer("/h", complete_state=None)
        event = _make_event(buf)
        handler(event)
        buf.start_completion.assert_called_once_with(select_first=True)

    def test_tab_applies_current_completion(self):
        c1 = Completion("/help", start_position=-5)
        cs = _make_complete_state([c1], current_completion=c1)
        handler = _find_handler(self.bindings, "tab")
        buf = _make_buffer("/he", complete_state=cs)
        event = _make_event(buf)
        handler(event)
        buf.apply_completion.assert_called_once_with(c1)

    def test_tab_applies_first_completion_when_no_selection(self):
        c1 = Completion("/help", start_position=-5)
        c2 = Completion("/history", start_position=-5)
        cs = _make_complete_state([c1, c2], current_completion=None)
        handler = _find_handler(self.bindings, "tab")
        buf = _make_buffer("/h", complete_state=cs)
        event = _make_event(buf)
        handler(event)
        buf.apply_completion.assert_called_once_with(c1)


# ---------------------------------------------------------------------------
# Keybinding tests — newline inserters
# ---------------------------------------------------------------------------

class TestNewlineKeybinding:
    """Escape+Enter and Ctrl+J should always insert a newline."""

    @pytest.fixture(autouse=True)
    def setup_bindings(self):
        self.bindings = build_keybindings()

    def test_escape_enter_inserts_newline(self):
        handler = _find_handler(self.bindings, "escape+enter")
        buf = _make_buffer("hello")
        event = _make_event(buf)
        handler(event)
        buf.insert_text.assert_called_once_with("\n")


# ---------------------------------------------------------------------------
# PasteStore tests
# ---------------------------------------------------------------------------


class TestPasteStore:
    """PasteStore manages indexed paste content with marker resolution."""

    def test_put_returns_marker(self):
        ps = PasteStore()
        marker = ps.put("hello world")
        assert marker == "[paste:1]"

    def test_resolve_single_marker(self):
        ps = PasteStore()
        marker = ps.put("replaced")
        result = ps.resolve(f"before {marker} after")
        assert result == "before replaced after"

    def test_clear_resets_store(self):
        ps = PasteStore()
        ps.put("data")
        ps.clear()
        result = ps.resolve("[paste:1]")
        assert result == "[paste:1]"  # marker stays — store is empty
