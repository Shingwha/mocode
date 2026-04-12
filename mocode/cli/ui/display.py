"""Unified display manager for terminal output."""

import asyncio
import json
import shutil

from .components import Spinner
from .styles import (
    RESET, BOLD, DIM, BLUE, CYAN, GREEN, RED, WHITE,
    BG_BLUE,
    MessagePreset, format_preset,
)
from .textwrap import display_width


class SpacingManager:
    """Manages blank lines between display blocks.

    Default: always print a blank line between blocks.
    Exception: tool_call → tool_result has no space.
    """

    # The only pair that should NOT have a blank line between them
    NO_SPACE_PAIRS = frozenset({("tool_call", "tool_result")})

    def __init__(self):
        self._last: str | None = None

    def before(self, block_type: str) -> None:
        """Print newline before block unless explicitly excluded."""
        if self._last is not None:
            if (self._last, block_type) not in self.NO_SPACE_PAIRS:
                print()
        self._last = block_type

    def reset(self) -> None:
        self._last = None

    def set_last(self, block_type: str) -> None:
        """Set last block type without printing space."""
        self._last = block_type


class Display:
    """Centralized terminal output with proper spacing."""

    def __init__(self):
        self._spacing = SpacingManager()
        self._spinner: Spinner | None = None
        self._is_thinking: bool = False

    # --- Lifecycle ---

    def initialize(self) -> None:
        self._spacing.reset()

    def cleanup(self) -> None:
        if self._spinner:
            self._spinner.stop()
            self._spinner = None

    # --- Output ---

    def welcome(self, name: str, model: str, cwd: str) -> None:
        print(f"{BOLD}{name}{RESET}{DIM}v0.1.0{RESET} | {CYAN}{model}{RESET} | {DIM}{cwd}{RESET}")
        self._spacing.set_last("welcome")

    def user_message(self, content: str) -> None:
        """Display user message with blue highlight (for history)."""
        self._spacing.before("user_history")
        line, _ = self._highlight_line(content)
        print(line)

    def assistant_message(self, content: str) -> None:
        self._spacing.before("assistant")
        lines = content.split("\n")
        if len(lines) == 1:
            print(f"{CYAN}*{RESET} {content}")
        else:
            print(f"{CYAN}*{RESET} {lines[0]}")
            for line in lines[1:]:
                print(f"  {line}")

    def tool_call(self, name: str, args_preview: str = "") -> None:
        self._spacing.before("tool_call")
        args_str = f"{DIM}({args_preview}){RESET}" if args_preview else ""
        print(f"{CYAN}●{RESET} {GREEN}{name}{RESET}{args_str}")

    def tool_result(self, preview: str, max_length: int = 60) -> None:
        self._spacing.before("tool_result")
        text = preview[:max_length - 3] + "..." if len(preview) > max_length else preview
        print(f"  {DIM}⎿ {text}{RESET}")

    def tool_error(self, error: str) -> None:
        self._spacing.before("error")
        print(f"  {RED}x {error}{RESET}")

    def command_output(self, text: str) -> None:
        """Display raw command output text."""
        self._spacing.before("command")
        print(text)

    def error(self, text: str) -> None:
        """Display styled error message."""
        self._spacing.before("error")
        print(format_preset(text, MessagePreset.ERROR))

    def info(self, text: str) -> None:
        """Display styled info message."""
        self._spacing.before("command")
        print(format_preset(text, MessagePreset.INFO))

    def success(self, text: str) -> None:
        """Display styled success message."""
        self._spacing.before("command")
        print(format_preset(text, MessagePreset.SUCCESS))

    def warn(self, text: str) -> None:
        """Display styled warning message."""
        self._spacing.before("command")
        print(format_preset(text, MessagePreset.WARN))

    def permission_title(self, tool_name: str, preview: str = "") -> None:
        self._spacing.before("permission")
        print(f"{BOLD}{CYAN}?{RESET} Permission required for {GREEN}{tool_name}{RESET}{preview}")

    def exit_message(self, text: str) -> None:
        self._spacing.before("exit")
        print(f"{DIM}{text}{RESET}")

    def session_saved(self, session_id: str, message_count: int) -> None:
        """Display session save confirmation on exit."""
        self._spacing.before("exit")
        print(f"{DIM}Session saved: {session_id} ({message_count} messages){RESET}")

    # --- Thinking spinner ---

    def set_thinking(self, thinking: bool, text: str = "Thinking") -> None:
        if thinking == self._is_thinking:
            return
        self._is_thinking = thinking

        if thinking:
            self._spinner = Spinner(text, clear_on_complete=True)
            asyncio.create_task(self._spinner.start())
        else:
            if self._spinner:
                self._spinner.stop()
                self._spinner = None
            self._is_thinking = False

    # --- Input ---

    async def get_input(self) -> str:
        """Get user input with blue highlight."""
        # Stop spinner
        if self._spinner:
            self._spinner.stop()
            self._spinner = None
        self._is_thinking = False

        self._spacing.before("user_input")
        prompt = f"{BOLD}{BLUE}>{RESET} "

        try:
            user_input = await asyncio.to_thread(input, prompt)

            if user_input.strip():
                display_line, num_lines = self._highlight_line(user_input)
                print(f"\033[{num_lines}A\r\033[K{display_line}")

            return user_input
        except EOFError:
            return ""

    # --- History ---

    def render_history(self, messages: list[dict]) -> None:
        """Render session history messages."""
        if not messages:
            return

        self._spacing.reset()

        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")
            tool_calls = msg.get("tool_calls", [])

            if role == "system":
                continue
            elif role == "user":
                self.user_message(content)
            elif role == "assistant":
                if content:
                    self.assistant_message(content)
                for tc in tool_calls:
                    func = tc.get("function", {})
                    name = func.get("name", "unknown")
                    args = func.get("arguments", "{}")
                    try:
                        args_dict = json.loads(args)
                        preview = str(list(args_dict.values())[0])[:50] if args_dict else ""
                    except (json.JSONDecodeError, IndexError):
                        preview = ""
                    self.tool_call(name, preview)
            # Skip role == "tool" results in history

    # --- Spacing ---

    def reset_spacing(self) -> None:
        self._spacing.reset()

    def print_space_if_needed(self, block_type: str) -> None:
        self._spacing.before(block_type)

    # --- Internal ---

    def _highlight_line(self, content: str, prefix: str = ">") -> tuple[str, int]:
        """Create highlighted line with blue background on text only."""
        width = shutil.get_terminal_size().columns
        prefix_width = len(prefix) + 1  # "> "
        text_width = display_width(content)
        num_lines = max(1, (prefix_width + text_width + width - 1) // width)

        line = f"{BG_BLUE}{BOLD}{WHITE}{prefix} {content}{RESET}"
        return line, num_lines
