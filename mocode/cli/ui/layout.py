"""Terminal layout manager using component coordinator."""

import asyncio
import json
import shutil
from typing import Any

from .base import Component
from .colors import BG_BLUE, BLUE, BOLD, CYAN, DIM, GREEN, RED, RESET, WHITE
from .coordinator import ComponentCoordinator
from .textwrap import display_width
from .components import Message, Input, Select, Animated, MessagePreset


class Layout:
    """Terminal layout manager.

    Uses ComponentCoordinator for managing UI elements.
    Provides high-level methods for common operations.
    """

    def __init__(self):
        """Initialize layout with coordinator."""
        self._coordinator = ComponentCoordinator()
        self._spinner: Animated | None = None
        self._is_thinking: bool = False

    def initialize(self) -> None:
        """Initialize layout state."""
        self._coordinator.reset_spacing()

    def cleanup(self) -> None:
        """Cleanup resources."""
        if self._spinner:
            self._spinner.stop()
            self._spinner = None

    def show_welcome(self, name: str, model: str, cwd: str) -> None:
        """Display welcome message.

        Args:
            name: Application name
            model: Current model name
            cwd: Current working directory
        """
        print(f"{BOLD}{name}{RESET}{DIM}v0.1.0{RESET} | {CYAN}{model}{RESET} | {DIM}{cwd}{RESET}")

    # Component factory methods

    def message(self, text: str, preset: MessagePreset = MessagePreset.INFO) -> Message:
        """Create and show a message.

        Args:
            text: Message text
            preset: Message preset style

        Returns:
            Message component
        """
        msg = self._coordinator.message(text, preset)
        msg.show()
        return msg

    def input(self, message: str = "", **kwargs) -> str | None:
        """Create and show an input prompt.

        Args:
            message: Prompt message
            **kwargs: Additional Input arguments

        Returns:
            User input or None if cancelled
        """
        self._coordinator.print_space_if_needed("user_input")
        inp = self._coordinator.input(message, **kwargs)
        return inp.show()

    def select(self, title: str, choices: list, **kwargs) -> Any:
        """Create and show a selection menu.

        Args:
            title: Menu title
            choices: List of (value, label) tuples
            **kwargs: Additional Select arguments

        Returns:
            Selected value or None if cancelled
        """
        self._coordinator.print_space_if_needed("select")
        sel = self._coordinator.select(title, choices, **kwargs)
        return sel.show()

    # High-level message methods (backward compatible)

    def add_user_message(self, content: str) -> None:
        """Add user message for history display.

        Args:
            content: Message content
        """
        self._coordinator.print_space_if_needed("user_history")
        display_line, _ = self._highlight_line(content)
        print(display_line)

    def add_assistant_message(self, content: str) -> None:
        """Add assistant message.

        Args:
            content: Message content
        """
        self._coordinator.print_space_if_needed("assistant")
        lines = content.split("\n")
        if len(lines) == 1:
            print(f"{CYAN}*{RESET} {content}")
        else:
            print(f"{CYAN}*{RESET} {lines[0]}")
            for line in lines[1:]:
                print(f"  {line}")

    def add_tool_call(self, name: str, args_preview: str = "") -> None:
        """Add tool call display.

        Args:
            name: Tool name
            args_preview: Arguments preview
        """
        self._coordinator.print_space_if_needed("tool_call")
        args_str = f"{DIM}({args_preview}){RESET}" if args_preview else ""
        print(f"{CYAN}●{RESET} {GREEN}{name}{RESET}{args_str}")

    def add_tool_result(self, preview: str, max_length: int = 60) -> None:
        """Add tool result display.

        Args:
            preview: Result preview text
            max_length: Maximum preview length
        """
        self._coordinator.print_space_if_needed("tool_result")
        text = preview
        if len(text) > max_length:
            text = text[:max_length - 3] + "..."
        print(f"  {DIM}⎿ {text}{RESET}")

    def add_tool_error(self, error: str) -> None:
        """Add tool error display.

        Args:
            error: Error message
        """
        self._coordinator.print_space_if_needed("error")
        print(f"  {RED}x {error}{RESET}")

    def add_command_output(self, text: str) -> None:
        """Add command output.

        Args:
            text: Output text
        """
        self._coordinator.print_space_if_needed("command")
        print(text)

    def add_error_message(self, text: str) -> None:
        """Add error message.

        Args:
            text: Error text
        """
        self._coordinator.print_space_if_needed("error")
        print(f"{RED}Error: {text}{RESET}")

    def add_permission_ask_title(self, tool_name: str, preview: str = "") -> None:
        """Add permission ask title.

        Args:
            tool_name: Tool name
            preview: Permission preview
        """
        self._coordinator.print_space_if_needed("permission")
        print(f"{BOLD}{CYAN}?{RESET} Permission required for {GREEN}{tool_name}{RESET}{preview}")

    def add_exit_message(self, text: str) -> None:
        """Add exit message.

        Args:
            text: Exit message
        """
        self._coordinator.print_space_if_needed("exit")
        print(f"{DIM}{text}{RESET}")

    # Thinking/spinner management

    def set_thinking(self, thinking: bool, text: str = "Thinking") -> None:
        """Set thinking state with spinner.

        Args:
            thinking: Whether to show thinking spinner
            text: Spinner text
        """
        if thinking == self._is_thinking:
            return

        self._is_thinking = thinking

        if thinking:
            self._start_spinner(text)
        else:
            self._stop_spinner()

    def _start_spinner(self, text: str) -> None:
        """Start spinner animation.

        Args:
            text: Animation text
        """
        self._spinner = self._coordinator.animated(text, clear_on_complete=True)
        asyncio.create_task(self._spinner.start())

    def _stop_spinner(self) -> None:
        """Stop spinner animation."""
        if self._spinner:
            self._spinner.stop()
            self._spinner = None
        self._is_thinking = False

    # Spacing management

    def reset_spacing(self) -> None:
        """Reset spacing state."""
        self._coordinator.reset_spacing()

    def print_space_if_needed(self, current_type: str) -> None:
        """Print space if needed for type.

        Args:
            current_type: Current component type
        """
        self._coordinator.print_space_if_needed(current_type)

    # History rendering

    def render_session_history(self, messages: list[dict]) -> None:
        """Render session history.

        Args:
            messages: OpenAI-format message list
        """
        if not messages:
            return

        self._coordinator.reset_spacing()

        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")
            tool_calls = msg.get("tool_calls", [])

            if role == "system":
                continue

            if role == "user":
                self.add_user_message(content)
            elif role == "assistant":
                if content:
                    self.add_assistant_message(content)
                for tool_call in tool_calls:
                    func = tool_call.get("function", {})
                    name = func.get("name", "unknown")
                    args = func.get("arguments", "{}")
                    try:
                        args_dict = json.loads(args)
                        preview = str(list(args_dict.values())[0])[:50] if args_dict else ""
                    except:
                        preview = ""
                    self.add_tool_call(name, preview)
            elif role == "tool":
                pass  # Skip tool results in history

    # Input handling

    async def get_input(self) -> str:
        """Get user input with highlight.

        Returns:
            User input string
        """
        # Stop spinner if running
        self._stop_spinner()

        # Print leading space
        self._coordinator.print_space_if_needed("user_input")

        # Show input prompt
        prompt = f"{BOLD}{BLUE}>{RESET} "

        try:
            user_input = await asyncio.to_thread(input, prompt)

            # Highlight non-empty input
            if user_input.strip():
                display_line, num_lines = self._highlight_line(user_input)
                print(f"\033[{num_lines}A\r\033[K{display_line}")

            return user_input
        except EOFError:
            return ""

    def _highlight_line(self, content: str, prefix: str = ">") -> tuple[str, int]:
        """Create highlighted line with background.

        Args:
            content: Text content
            prefix: Prefix character

        Returns:
            Tuple of (highlighted_line, num_lines)
        """
        width = shutil.get_terminal_size().columns
        prefix_width = len(prefix) + 1

        text_width = display_width(content)
        total_width = prefix_width + text_width
        num_lines = (total_width + width - 1) // width

        display_line = f"{BG_BLUE}{BOLD}{WHITE}{prefix}{RESET}{BG_BLUE}{BOLD} {content} {RESET}"
        padding_width = width * num_lines - text_width - prefix_width
        padding = " " * max(0, padding_width)

        return f"{display_line}{padding}{RESET}", num_lines
