"""CLI display — prompt_toolkit input + ANSI output."""

import asyncio
import json
import random
import re
from contextlib import asynccontextmanager

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys

from .theme import (
    RST, BOLD, DIM, CYAN, MAGENTA, BG_USER, Spinner, Theme, COMMANDS, _PRESETS, _s,
)

# Paste marker thresholds
_PASTE_LINE_THRESHOLD = 3
_PASTE_CHAR_THRESHOLD = 100
_PASTE_MARKER_RE = re.compile(r"\[Pasted text #(\d+) \+\d+ (?:lines|chars)\]")


# ── Tool display helpers ────────────────────────────────


_TOOL_KEY = {
    "read": "path", "write": "path", "append": "path", "edit": "path",
    "bash": "command", "glob": "pat", "grep": "pat",
    "fetch": "url", "sub_agent": "task", "skill": "name",
    "goal": "action", "image": "prompt",
}


def _tool_summary(name, args):
    key = _TOOL_KEY.get(name)
    if not key:
        return ""
    val = str(args.get(key, ""))
    return val[:60] + ("..." if len(val) > 60 else "")


def _parse_tool_call(tc: dict) -> tuple[str, dict]:
    """Extract (name, args) from a raw tool_call message dict."""
    fn = tc.get("function", {})
    args = fn.get("arguments", "{}")
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except Exception:
            args = {}
    return fn.get("name", "?"), args


# ── Prompt-toolkit wiring ───────────────────────────────


def _build_bindings(paste_handler=None):
    """Enter accepts completion if menu is open, otherwise submits."""
    bindings = KeyBindings()

    @bindings.add("enter")
    def _(event):
        buf = event.current_buffer
        if buf.complete_state and buf.complete_state.current_completion is not None:
            buf.apply_completion(buf.complete_state.current_completion)
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


class _SlashCompleter(Completer):
    """Prefix-match /commands only on the first word."""

    def __init__(self, commands):
        self._commands = commands

    def get_completions(self, document, complete_event):
        text = document.text
        if not text.startswith("/") or " " in text:
            return
        for cmd in self._commands:
            if cmd.startswith(text):
                yield Completion(cmd, start_position=-len(text))


# ── Display ─────────────────────────────────────────────


class Display:
    """CLI display — handles all user-facing I/O."""

    def __init__(self, theme: Theme | None = None):
        self.theme = theme or Theme()
        self._spinner_active = False
        self._spinner_text = ""
        self._spinner_len = 0
        self._paste_store: dict[int, str] = {}
        self._paste_counter: int = 0
        self._session = PromptSession(
            completer=_SlashCompleter(COMMANDS),
            complete_while_typing=True,
            key_bindings=_build_bindings(self._handle_paste),
        )

    # ── Output core ───────────────────────────────────────

    def _print(self, *args, **kwargs):
        if self._spinner_active:
            self._clear_spinner()
        print(*args, **kwargs)

    def _styled(self, icon: str, text: str, color: str, icon_color: str = ""):
        """Format a line with icon + text + color."""
        ic = icon_color or color
        self._print(f"{_s(icon, ic)} {_s(text, color)}")

    # ── Input ─────────────────────────────────────────────

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

    async def prompt(self) -> str:
        self._paste_store.clear()
        self._paste_counter = 0
        raw = await self._session.prompt_async(f"{self.theme.icon_input} ")
        self._clear_input_lines(raw)
        return self._resolve_paste_markers(raw).strip()

    # ── Spinner ───────────────────────────────────────────

    def resolve_spinner(self, style: str | Spinner | None = None) -> Spinner:
        """Resolve a spinner style name or object to a Spinner instance."""
        if style is None:
            return random.choice(list(_PRESETS.values()))
        if isinstance(style, Spinner):
            return style
        if style in _PRESETS:
            return _PRESETS[style]
        raise ValueError(f"Unknown spinner style: {style!r}. Available: {list(_PRESETS)}")

    def set_spinner_text(self, text: str):
        """Dynamically update spinner text while it's running."""
        self._spinner_text = text

    @asynccontextmanager
    async def spinner(self, text: str = "Thinking", style: str | Spinner | None = None):
        """Shows a spinner while waiting."""
        spinner = self.resolve_spinner(style)
        self._spinner_active = True
        self._spinner_text = text
        stop = asyncio.Event()
        idx = 0

        def _get_suffix():
            return f" {self._spinner_text}..." if spinner.show_text else ""

        self._spinner_len = max(len(f) for f in spinner.frames) + len(text) + 5

        async def _spin():
            nonlocal idx
            while not stop.is_set():
                frame = spinner.frames[idx % len(spinner.frames)]
                suffix = _get_suffix()
                print(f"\r{DIM}{frame}{suffix}{RST}", end="", flush=True)
                idx += 1
                await asyncio.sleep(spinner.speed)

        task = asyncio.ensure_future(_spin())
        try:
            yield
        finally:
            stop.set()
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            self._clear_spinner()
            self._spinner_active = False

    def _clear_spinner(self):
        print(f"\r{' ' * self._spinner_len}\r", end="", flush=True)

    # ── Output: user message ──────────────────────────────

    def user_message(self, content: str):
        """Render a user message with dark background."""
        t = self.theme
        self._print(_s(f"{t.icon_input} {content.strip()}", BG_USER, *t.color_user_fg))
        self._print()

    def _clear_input_lines(self, raw_text: str):
        """Clear the prompt_toolkit input lines from the terminal."""
        lines = raw_text.count('\n') + 1
        for _ in range(lines):
            print("\033[A\033[2K", end="", flush=True)

    # ── Output: tool lifecycle ────────────────────────────

    def tool_start(self, name: str, summary: str):
        t = self.theme
        self._print(f"{_s(t.icon_tool, DIM)} {_s(name, t.color_tool)}{_s(f'({summary})', DIM)}")

    def tool_error(self, msg: str):
        self._styled(self.theme.icon_error, msg, self.theme.color_error)

    def tool_timeout(self, seconds: int):
        self._styled(self.theme.icon_error, f"timeout: {seconds}s", self.theme.color_error)

    # ── Output: model response ────────────────────────────

    def reasoning(self, content: str):
        t = self.theme
        for line in content.splitlines():
            self._styled(t.icon_reasoning, line, t.color_reasoning)

    def text_response(self, content: str):
        t = self.theme
        for line in content.strip().splitlines():
            self._print(f"{_s(f'{t.icon_text} {line}', *t.color_text)}")

    def response(self, text: str):
        self._print(f"\n{text}\n")

    # ── Output: status ────────────────────────────────────

    def usage(self, prompt_tokens: int, completion_tokens: int):
        self._styled(self.theme.icon_usage, f"↑{prompt_tokens:,} ↓{completion_tokens:,}", self.theme.color_usage)

    def compact(self, old: int, new: int):
        self._styled(self.theme.icon_compact, f"Compacted: {old} → {new} msgs", self.theme.color_compact)

    def info(self, text: str):
        self._styled("", text, self.theme.color_info)

    def warn(self, text: str):
        self._styled("", text, self.theme.color_warn)

    def error(self, text: str):
        self._styled("", text, self.theme.color_error)

    # ── Resume rendering ──────────────────────────────────

    def clear_screen(self):
        import os
        os.system('cls' if os.name == 'nt' else 'clear')

    def render_messages(self, messages: list[dict]):
        """Re-render a message history as if it were live output."""
        for msg in messages:
            role = msg.get("role")
            if role == "user":
                content = msg.get("content", "")
                if isinstance(content, list):
                    content = " ".join(
                        p.get("text", "[image]") for p in content if isinstance(p, dict)
                    )
                self.user_message(content)
            elif role == "assistant":
                if msg.get("reasoning_content"):
                    self.reasoning(msg["reasoning_content"])
                if msg.get("content") and not msg.get("tool_calls"):
                    self.response(msg["content"])
                elif msg.get("content") and msg.get("tool_calls"):
                    self.text_response(msg["content"])
                for tc in msg.get("tool_calls", []):
                    name, args = _parse_tool_call(tc)
                    self.tool_start(name, _tool_summary(name, args))
            elif role == "tool":
                content = msg.get("content", "")
                if content.startswith("error:") or content.startswith("timeout:"):
                    self.tool_error(content[:80])

    def session_list(self, sessions: list, active_id: str | None = None):
        """Render a numbered session list for the current directory."""
        if not sessions:
            self.info("No sessions found for this directory.")
            return
        self.info("Sessions for this directory:")
        for i, s in enumerate(sessions, 1):
            title = (s.title or "Untitled")[:40]
            date = s.updated_at[:10]
            n_msgs = len(s.messages)
            marker = " *" if s.id == active_id else ""
            self._print(
                f"  {_s(f'{i}.', DIM)} {_s(s.id, CYAN)} "
                f"{_s(date, DIM)} {_s(title, '')} "
                f"{_s(f'({n_msgs} msgs)', DIM)}{marker}"
            )
