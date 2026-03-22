"""Multi-select component for selecting multiple items."""

import shutil
from typing import Generic, TypeVar

from ..base import Component, ComponentState
from ..colors import RESET, CYAN, GREEN, DIM, BOLD
from ..keyboard import getch, esc_paused
from ..textwrap import truncate_text
from .styles import SelectStyle, DEFAULT_SELECT_STYLE

T = TypeVar("T")


class MultiSelect(Generic[T], Component):
    """Interactive multi-select menu component.

    Features:
    - UP/DOWN navigation
    - Space to toggle selection
    - 'a' to toggle select all
    - Enter to confirm
    - ESC to cancel
    """

    def __init__(
        self,
        title: str,
        choices: list[tuple[T, str]],
        pre_selected: list[T] | None = None,
        max_width: int | None = None,
        page_size: int = 8,
        style: SelectStyle | None = None,
        min_selections: int = 1,
        max_selections: int | None = None,
    ):
        """Initialize MultiSelect component.

        Args:
            title: Menu title
            choices: List of (value, label) tuples
            pre_selected: Initially selected values
            max_width: Maximum text width for truncation
            page_size: Number of items to show per page
            style: Custom style configuration
            min_selections: Minimum required selections
            max_selections: Maximum allowed selections
        """
        super().__init__(type_hint="select")
        self.title = title
        self.choices = choices
        self.max_width = max_width
        self.page_size = page_size
        self._style = style or DEFAULT_SELECT_STYLE
        self.min_selections = min_selections
        self.max_selections = max_selections

        self.selected = 0
        self.scroll_offset = 0

        # Track selected values
        self._selected_values: set[T] = set(pre_selected) if pre_selected else set()

    def _get_effective_width(self) -> int:
        """Get effective width for text truncation."""
        terminal_width = shutil.get_terminal_size().columns
        # Account for "  [x] " prefix (6 chars)
        if self.max_width is None:
            return max(20, terminal_width - 6)
        return max(20, min(self.max_width, terminal_width) - 6)

    def _get_effective_page_size(self) -> int:
        """Calculate effective page size based on terminal height."""
        terminal_height = shutil.get_terminal_size().lines
        # Reserve: title(1) + hint(1) + bottom space(2)
        available = max(3, terminal_height - 4)
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

    def show(self) -> list[T] | None:
        """Show menu and return selected values.

        Returns:
            List of selected values or None if cancelled
        """
        if not self.choices:
            return []

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
                elif key == " ":  # Space: toggle selection
                    self._toggle_current()
                    self._render_update()
                elif key.lower() == "a":  # 'a': toggle all
                    self._toggle_all()
                    self._render_update()
                elif key in ("\r", "\n"):  # Enter: confirm
                    if len(self._selected_values) >= self.min_selections:
                        result = list(self._selected_values)
                        self._complete(result)
                        return result
                    # Show min selection hint
                    self._show_hint(f"Select at least {self.min_selections} item(s)")
                elif key == "ESC":
                    self._cancel()
                    return None

    def _toggle_current(self) -> None:
        """Toggle selection for current item."""
        value = self.choices[self.selected][0]

        if value in self._selected_values:
            # Check if we can deselect (min selections)
            if len(self._selected_values) > self.min_selections:
                self._selected_values.discard(value)
        else:
            # Check if we can select (max selections)
            if self.max_selections is None or len(self._selected_values) < self.max_selections:
                self._selected_values.add(value)

    def _toggle_all(self) -> None:
        """Toggle select all/none."""
        all_values = [c[0] for c in self.choices]

        if self._selected_values == set(all_values):
            # Deselect all (but respect min_selections)
            if self.min_selections == 0:
                self._selected_values.clear()
        else:
            # Select all (but respect max_selections)
            if self.max_selections is None:
                self._selected_values = set(all_values)
            else:
                self._selected_values = set(all_values[:self.max_selections])

    def _render_menu(self) -> None:
        """Render menu to stdout."""
        start, end = self._get_visible_range()

        lines_count = 0

        # Title
        if self.title:
            print(self._format_title())
            lines_count += 1

        # Choices
        for i in range(start, end):
            print(self._format_choice(i, self.choices[i][0], self.choices[i][1]))
            lines_count += 1

        # Hint
        print(self._format_hint())
        lines_count += 1

        self._rendered_lines = lines_count
        self.set_state(ComponentState.ACTIVE)

    def _render_update(self) -> None:
        """Update render after change."""
        self.print_clear()
        self._render_menu()

    def _format_title(self) -> str:
        """Format title with selection count."""
        style = self._style
        terminal_width = shutil.get_terminal_size().columns

        symbol = f"{style.title_style}{style.title_color}{style.title_symbol}{RESET}"
        count = f"({len(self._selected_values)} selected)"

        max_title_width = terminal_width - len(count) - 4
        title = truncate_text(self.title, max_title_width)

        return f"{symbol} {title} {DIM}{count}{RESET}"

    def _format_choice(self, index: int, value: T, text: str) -> str:
        """Format a single choice line."""
        style = self._style
        width = self._get_effective_width()
        text = truncate_text(text, width)

        is_selected = value in self._selected_values
        is_focused = index == self.selected

        # Checkbox indicator
        checkbox = f"{GREEN}[x]{RESET}" if is_selected else f"{DIM}[ ]{RESET}"

        if is_focused:
            return f"  {style.selected_color}{style.selected_indicator}{RESET} {checkbox} {style.selected_style}{text}{RESET}"
        else:
            return f"    {checkbox} {style.normal_style}{text}{RESET}"

    def _format_hint(self) -> str:
        """Format hint line."""
        return f"{DIM}  [space] toggle  [a] all  [enter] confirm  [esc] cancel{RESET}"

    def render(self) -> str:
        """Render menu (for display, not interaction)."""
        lines = []
        start, end = self._get_visible_range()

        if self.title:
            lines.append(self._format_title())

        for i in range(start, end):
            lines.append(self._format_choice(i, self.choices[i][0], self.choices[i][1]))

        lines.append(self._format_hint())
        return "\n".join(lines)

    def _show_hint(self, message: str) -> None:
        """Show temporary hint message."""
        # Clear hint line and show message
        print(f"\r{message}", end="", flush=True)

    def _complete(self, result: list[T]) -> None:
        """Complete selection."""
        self.print_clear()
        self._result = result
        self.set_state(ComponentState.COMPLETED)

    def _cancel(self) -> None:
        """Handle cancellation."""
        self.print_clear()
        self.set_state(ComponentState.COMPLETED)
