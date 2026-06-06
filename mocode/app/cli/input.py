"""Input layer — PromptSession, paste handling, keybindings, completer."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from .textutils import count_visual_lines

if TYPE_CHECKING:
    from .commands import CommandRegistry

# Paste marker thresholds
_PASTE_LINE_THRESHOLD = 3
_PASTE_CHAR_THRESHOLD = 100
_PASTE_MARKER_RE = re.compile(r"\[Pasted text #(\d+) \+\d+ (?:lines|chars)\]")


# ── Completion helpers ──────────────────────────────────


def _apply_best_completion(buf) -> None:
    """Apply the current or first available completion from the menu."""
    completion = (
        buf.complete_state.current_completion or buf.complete_state.completions[0]
    )
    buf.apply_completion(completion)


class SlashCompleter:
    """Prefix-match /commands from a CommandRegistry.

    Uses duck-typing (``get_completions`` method) to satisfy
    ``prompt_toolkit`` without importing it at module level.
    """

    def __init__(self, registry: CommandRegistry):
        self._registry = registry

    async def get_completions_async(self, document, complete_event):
        text = document.text
        if not text.startswith("/") or " " in text:
            return
        from prompt_toolkit.completion import Completion

        for cmd in self._registry.all():
            if cmd.name.startswith(text) and cmd.name != text:
                yield Completion(
                    cmd.name,
                    start_position=-len(text),
                    display_meta=cmd.description,
                )


# ── Keybindings ─────────────────────────────────────────


def build_keybindings(paste_handler=None):
    """Tab and Enter accept completion when menu is visible; Enter submits otherwise."""
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.keys import Keys

    bindings = KeyBindings()

    @bindings.add("tab")
    def _(event):
        buf = event.current_buffer
        if buf.complete_state:
            _apply_best_completion(buf)
        else:
            buf.start_completion(select_first=True)

    @bindings.add("enter")
    def _(event):
        buf = event.current_buffer
        if buf.complete_state and buf.complete_state.completions:
            _apply_best_completion(buf)
        else:
            buf.validate_and_handle()

    @bindings.add("escape", "enter")
    def _(event):
        event.current_buffer.insert_text("\n")

    @bindings.add("c-j")
    def _(event):
        event.current_buffer.insert_text("\n")

    if paste_handler:

        @bindings.add(Keys.BracketedPaste)
        def _(event):
            paste_handler(event)

    return bindings


# ── Input ───────────────────────────────────────────────


class Input:
    """Manages the PromptSession, paste handling, and command completion."""

    def __init__(self, registry: CommandRegistry, ps1: str = "❯"):
        self._ps1 = ps1
        self._paste_store: dict[int, str] = {}
        self._paste_counter: int = 0
        self._registry = registry
        self._session = None

    def _ensure_session(self):
        if self._session is None:
            from prompt_toolkit import PromptSession

            self._session = PromptSession(
                completer=SlashCompleter(self._registry),
                complete_while_typing=True,
                key_bindings=build_keybindings(self._handle_paste),
            )

    def _handle_paste(self, event):
        data = event.data.replace("\r\n", "\n").replace("\r", "\n")
        n_lines = data.count("\n") + 1
        n_chars = len(data)

        if n_lines < _PASTE_LINE_THRESHOLD and n_chars < _PASTE_CHAR_THRESHOLD:
            event.current_buffer.insert_text(data)
            return

        self._paste_counter += 1
        pid = self._paste_counter
        self._paste_store[pid] = data
        unit = "lines" if n_lines > 1 else "chars"
        count = n_lines if n_lines > 1 else n_chars
        marker = f"[Pasted text #{pid} +{count} {unit}]"
        event.current_buffer.insert_text(marker)

    def _resolve_paste_markers(self, text: str) -> str:
        def _replace(m):
            pid = int(m.group(1))
            return self._paste_store.get(pid, m.group(0))

        return _PASTE_MARKER_RE.sub(_replace, text)

    async def prompt(self, default: str = "") -> str:
        self._ensure_session()
        self._paste_store.clear()
        self._paste_counter = 0
        raw = await self._session.prompt_async(f"{self._ps1} ", default=default)
        # Clear the prompt_toolkit input lines from the terminal
        lines = count_visual_lines(raw, len(self._ps1) + 1)  # +1 for trailing space
        for _ in range(lines):
            print("\033[A\033[2K", end="", flush=True)
        text = self._resolve_paste_markers(raw).strip()
        # Sanitize surrogates from prompt_toolkit on Windows
        return text.encode("utf-16-le", errors="surrogatepass").decode(
            "utf-16-le", errors="replace"
        )
