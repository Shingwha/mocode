"""Select component for interactive selection menus."""

import shutil
from typing import Generic, TypeVar

from ..base import Component, ComponentState
from ..colors import RESET
from ..keyboard import getch, esc_paused
from ..textwrap import truncate_text
from .styles import SelectStyle, DEFAULT_SELECT_STYLE

T = TypeVar("T")


class Select(Generic[T], Component):
    """Interactive selection menu component.

    Features:
    - Keyboard navigation (UP/DOWN)
    - Pagination for long lists
    - Auto-clear on selection
    - Optional current value indicator
    - ESC/LEFT to cancel
    """

    def __init__(
        self,
        title: str,
        choices: list[tuple[T, str]],
        current: T | None = None,
        max_width: int | None = None,
        page_size: int = 8,
        style: SelectStyle | None = None,
        type_hint: str = "select"
    ):
        """Initialize Select component.

        Args:
            title: Menu title
            choices: List of (value, label) tuples
            current: Currently selected value (for indicator)
            max_width: Maximum text width for truncation
            page_size: Number of items to show per page
            style: Custom style configuration
            type_hint: Type hint for spacing coordinator
        """
        super().__init__(type_hint=type_hint)
        self.title = title
        self.choices = choices
        self.current = current
        self.max_width = max_width
        self.page_size = page_size
        self._style = style or DEFAULT_SELECT_STYLE

        self.selected = 0
        self.scroll_offset = 0

        # Find current index
        if current is not None:
            for i, (key, _) in enumerate(choices):
                if key == current:
                    self.selected = i
                    break

    def _get_effective_width(self) -> int:
        """Get effective width for text truncation."""
        terminal_width = shutil.get_terminal_size().columns
        # Account for "  > " prefix (4 chars)
        if self.max_width is None:
            return max(20, terminal_width - 4)
        return max(20, min(self.max_width, terminal_width) - 4)

    def _get_effective_page_size(self) -> int:
        """Calculate effective page size based on terminal height."""
        terminal_height = shutil.get_terminal_size().lines
        # Reserve: title(1) + bottom space for errors/prompts(2)
        available = max(3, terminal_height - 3)
        return min(self.page_size, available)

    def _get_visible_range(self) -> tuple[int, int]:
        """Calculate current visible option index range."""
        total = len(self.choices)
        page_size = self._get_effective_page_size()

        if total <= page_size:
            return 0, total

        # Ensure selected is within visible area
        if self.selected < self.scroll_offset:
            self.scroll_offset = self.selected
        elif self.selected >= self.scroll_offset + page_size:
            self.scroll_offset = self.selected - page_size + 1

        # Boundary check
        self.scroll_offset = max(0, min(self.scroll_offset, total - page_size))
        return self.scroll_offset, self.scroll_offset + page_size

    def render(self) -> str:
        """Render menu (for display, not interaction).

        Returns:
            Rendered menu string
        """
        lines = []
        start, end = self._get_visible_range()

        if self.title:
            lines.append(self._format_title())

        for i in range(start, end):
            lines.append(self._format_choice(i, self.choices[i][0], self.choices[i][1]))

        return "\n".join(lines)

    def show(self) -> T | None:
        """Show menu and return selection.

        Returns:
            Selected value or None if cancelled
        """
        with esc_paused():
            self._render_menu()

            while True:
                key = getch(with_arrows=True)
                if key == "UP":
                    self.selected = (self.selected - 1) % len(self.choices)
                    self._render_update()
                elif key == "DOWN":
                    self.selected = (self.selected + 1) % len(self.choices)
                    self._render_update()
                elif key in ("\r", "\n", "RIGHT"):
                    result = self.choices[self.selected][0]
                    self._complete(result)
                    return result
                elif key == "LEFT" or key == "ESC":
                    self._cancel()
                    return None

    def _render_menu(self) -> None:
        """Render menu to stdout."""
        start, end = self._get_visible_range()

        lines_count = 0
        if self.title:
            print(self._format_title())
            lines_count += 1

        for i in range(start, end):
            print(self._format_choice(i, self.choices[i][0], self.choices[i][1]))
            lines_count += 1

        self._rendered_lines = lines_count
        self.set_state(ComponentState.ACTIVE)

    def _render_update(self) -> None:
        """Update render after selection change."""
        self.print_clear()
        self._render_menu()

    def _format_title(self) -> str:
        """Format title with position indicator."""
        style = self._style
        terminal_width = shutil.get_terminal_size().columns
        page_size = self._get_effective_page_size()

        symbol = f"{style.title_style}{style.title_color}{style.title_symbol}{RESET}"

        if len(self.choices) <= page_size:
            max_title_width = terminal_width - 3  # Reserve 3 for "? " prefix
            title = truncate_text(self.title, max_title_width)
            return f"{symbol} {title}"

        pos = self.selected + 1
        total = len(self.choices)
        pos_text = f" ({pos}/{total})"
        max_title_width = terminal_width - 3 - len(pos_text)
        title = truncate_text(self.title, max_title_width)
        return f"{symbol} {title} {style.position_style}{pos_text}{RESET}"

    def _format_choice(self, index: int, key: T, text: str) -> str:
        """Format a single choice line."""
        style = self._style
        width = self._get_effective_width()
        text = truncate_text(text, width)

        if index == self.selected:
            return f"  {style.selected_color}{style.selected_indicator}{RESET} {style.selected_style}{text}{RESET}"
        elif key == self.current:
            return f"  {style.current_color}{style.current_indicator}{RESET} {text}"
        else:
            return f"  {style.normal_style} {RESET} {style.normal_style}{text}{RESET}"

    def _complete(self, result: T) -> None:
        """Complete selection.

        Args:
            result: Selected value
        """
        # Clear menu
        self.print_clear()

        # Show final selection (optional - could be shown by caller)
        # For now, just clear and complete
        self._result = result
        self.set_state(ComponentState.COMPLETED)

    def _cancel(self) -> None:
        """Handle cancellation."""
        self.print_clear()
        self.set_state(ComponentState.COMPLETED)
