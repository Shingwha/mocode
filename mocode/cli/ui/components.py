"""Interactive UI components: Input, Select, MultiSelect, Spinner."""

import asyncio
import shutil
from typing import Callable, Generic, TypeVar

from .keyboard import getch, esc_paused
from .styles import (
    RESET, BOLD, DIM, WHITE, YELLOW, GREEN,
    BG_BLUE,
    InputStyle, DEFAULT_INPUT_STYLE,
    SelectStyle, DEFAULT_SELECT_STYLE,
    AnimatedStyle, DEFAULT_ANIMATED_STYLE,
)
from .textwrap import display_width, truncate_text

T = TypeVar("T")


# --- Shared pagination utilities ---

def _effective_page_size(requested: int, reserve: int = 3) -> int:
    """Get page size adjusted for terminal height."""
    terminal_height = shutil.get_terminal_size().lines
    available = max(3, terminal_height - reserve)
    return min(requested, available)


def _visible_range(
    selected: int, scroll_offset: int, total: int, page_size: int
) -> tuple[int, int]:
    """Calculate visible range for paginated menus."""
    if total <= page_size:
        return 0, total

    if selected < scroll_offset:
        scroll_offset = selected
    elif selected >= scroll_offset + page_size:
        scroll_offset = selected - page_size + 1

    scroll_offset = max(0, min(scroll_offset, total - page_size))
    return scroll_offset, scroll_offset + page_size


def _effective_width(max_width: int | None, prefix_len: int = 4) -> int:
    """Get effective width for text truncation."""
    terminal_width = shutil.get_terminal_size().columns
    if max_width is None:
        return max(20, terminal_width - prefix_len)
    return max(20, min(max_width, terminal_width) - prefix_len)


# --- Spinner ---

class Spinner:
    """Async spinner animation."""

    def __init__(
        self,
        text: str = "Thinking",
        style: AnimatedStyle | None = None,
        clear_on_complete: bool = True,
    ):
        self.text = text
        self._style = style or DEFAULT_ANIMATED_STYLE
        self._clear_on_complete = clear_on_complete
        self._is_running = False
        self._frame_index = 0

    @property
    def is_running(self) -> bool:
        return self._is_running

    def render(self) -> str:
        frames = self._style.frames
        frame = frames[self._frame_index % len(frames)]
        return f"{self._style.color}{frame}{RESET} {self._style.text_style}{self.text}{RESET}"

    async def start(self) -> None:
        if self._is_running:
            return
        self._is_running = True
        try:
            while self._is_running:
                print(f"\r{self.render()}", end="", flush=True)
                self._frame_index += 1
                await asyncio.sleep(self._style.interval)
        except asyncio.CancelledError:
            pass

    def stop(self, clear: bool | None = None) -> None:
        self._is_running = False
        should_clear = clear if clear is not None else self._clear_on_complete
        if should_clear:
            width = shutil.get_terminal_size().columns
            print(f"\r{' ' * width}\r", end="")
        else:
            print()

    def update_text(self, text: str) -> None:
        self.text = text


# --- Input ---

class Input:
    """Text input with ESC support, validation, and highlight on completion."""

    def __init__(
        self,
        message: str = "",
        *,
        hint: str | None = None,
        default: str | None = None,
        required: bool = False,
        validator: Callable[[str], bool | str] | None = None,
        style: InputStyle | None = None,
    ):
        self.message = message
        self.hint = hint
        self.default = default
        self.required = required
        self.validator = validator
        self._style = style or DEFAULT_INPUT_STYLE
        self._value: str | None = None

    @property
    def value(self) -> str | None:
        return self._value

    def show(self) -> str | None:
        style = self._style

        # Print prompt lines
        if self.message:
            print(f"{style.prompt_style}{style.prompt_color}{style.prompt_symbol}{RESET} {self.message}")
        if self.hint:
            print(f"{style.hint_style}  {self.hint}{RESET}")

        # Get input
        print(f"{style.input_color}{style.input_indicator}{RESET} ", end="", flush=True)

        try:
            value = self._readline()
        except (KeyboardInterrupt, EOFError):
            print(f"{YELLOW}Cancelled{RESET}")
            return None

        if value is None:  # ESC
            print(f"{YELLOW}Cancelled{RESET}")
            return None

        value = value.strip()

        # Handle empty
        if not value:
            if self.default is not None:
                value = self.default
            elif self.required:
                _print_inline_error("Value cannot be empty")
                return None
            else:
                return value

        # Validate
        if self.validator and value:
            result = self.validator(value)
            if result is not True:
                msg = result if isinstance(result, str) else "Invalid value"
                _print_inline_error(msg)
                return None

        self._value = value
        self._highlight(value)
        return value

    def _readline(self) -> str | None:
        """Read line with ESC/Backspace support."""
        chars = []
        while True:
            ch = getch(with_arrows=False)
            if ch == "ESC":
                return None
            elif ch in ("\r", "\n"):
                print()
                return "".join(chars)
            elif ch in ("\x7f", "\x08"):  # Backspace
                if chars:
                    chars.pop()
                    print("\b \b", end="", flush=True)
            elif ch == "\x03":  # Ctrl+C
                return None
            elif ch:
                chars.append(ch)
                print(ch, end="", flush=True)

    def _highlight(self, value: str) -> None:
        """Replace input line with blue background highlight."""
        if not value.strip():
            return

        terminal_width = shutil.get_terminal_size().columns
        prefix = f"{self._style.input_indicator} "
        prefix_width = len(prefix)
        text_width = display_width(value)
        num_lines = max(1, (prefix_width + text_width + terminal_width - 1) // terminal_width)

        # Move cursor up to the start of input, clear the line(s)
        print(f"\033[{num_lines}A\r\033[K", end="")

        print(f"{BG_BLUE}{BOLD}{WHITE}{prefix}{value}{RESET}")


# --- Select ---

class Select(Generic[T]):
    """Single-selection menu with keyboard navigation and pagination."""

    def __init__(
        self,
        title: str,
        choices: list[tuple[T, str]],
        current: T | None = None,
        max_width: int | None = None,
        page_size: int = 8,
        style: SelectStyle | None = None,
    ):
        self.title = title
        self.choices = choices
        self.current = current
        self.max_width = max_width
        self.page_size = page_size
        self._style = style or DEFAULT_SELECT_STYLE

        self.selected = 0
        self.scroll_offset = 0

        if current is not None:
            for i, (key, _) in enumerate(choices):
                if key == current:
                    self.selected = i
                    break

    def show(self) -> T | None:
        if not self.choices:
            return None

        with esc_paused():
            self._render()

            while True:
                key = getch(with_arrows=True)
                if key == "UP":
                    self.selected = (self.selected - 1) % len(self.choices)
                    self._update()
                elif key == "DOWN":
                    self.selected = (self.selected + 1) % len(self.choices)
                    self._update()
                elif key in ("\r", "\n", "RIGHT"):
                    result = self.choices[self.selected][0]
                    self._clear()
                    return result
                elif key in ("LEFT", "ESC"):
                    self._clear()
                    return None

    def _render(self) -> None:
        eff_page = _effective_page_size(self.page_size)
        start, end = _visible_range(self.selected, self.scroll_offset, len(self.choices), eff_page)
        self.scroll_offset = start

        lines = 0
        if self.title:
            print(self._format_title(eff_page))
            lines += 1

        for i in range(start, end):
            print(self._format_choice(i))
            lines += 1

        self._rendered = lines

    def _update(self) -> None:
        print(f"\033[{self._rendered}A\r\033[J", end="")
        self._render()

    def _clear(self) -> None:
        """Clear rendered menu lines from screen."""
        if self._rendered > 0:
            print(f"\033[{self._rendered}A\r\033[J", end="")
            self._rendered = 0

    def _format_title(self, eff_page: int) -> str:
        s = self._style
        symbol = f"{s.title_style}{s.title_color}{s.title_symbol}{RESET}"
        terminal_width = shutil.get_terminal_size().columns

        if len(self.choices) <= eff_page:
            title = truncate_text(self.title, terminal_width - 3)
            return f"{symbol} {title}"

        pos = self.selected + 1
        total = len(self.choices)
        pos_text = f" ({pos}/{total})"
        title = truncate_text(self.title, terminal_width - 3 - len(pos_text))
        return f"{symbol} {title} {s.position_style}{pos_text}{RESET}"

    def _format_choice(self, index: int) -> str:
        s = self._style
        width = _effective_width(self.max_width)
        key, text = self.choices[index]
        text = truncate_text(text, width)

        if index == self.selected:
            return f"  {s.selected_color}{s.selected_indicator}{RESET} {s.selected_style}{text}{RESET}"
        elif key == self.current:
            return f"  {s.current_color}{s.current_indicator}{RESET} {text}"
        else:
            return f"  {s.normal_style} {RESET} {s.normal_style}{text}{RESET}"


# --- MultiSelect ---

class MultiSelect(Generic[T]):
    """Multi-selection menu with toggle support."""

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
        self.title = title
        self.choices = choices
        self.max_width = max_width
        self.page_size = page_size
        self._style = style or DEFAULT_SELECT_STYLE
        self.min_selections = min_selections
        self.max_selections = max_selections

        self.selected = 0
        self.scroll_offset = 0
        self._selected_values: set[T] = set(pre_selected) if pre_selected else set()

    def show(self) -> list[T] | None:
        if not self.choices:
            return []

        with esc_paused():
            self._render()

            while True:
                key = getch(with_arrows=True)

                if key == "UP":
                    self.selected = (self.selected - 1) % len(self.choices)
                    self._update()
                elif key == "DOWN":
                    self.selected = (self.selected + 1) % len(self.choices)
                    self._update()
                elif key == " ":
                    self._toggle_current()
                    self._update()
                elif key.lower() == "a":
                    self._toggle_all()
                    self._update()
                elif key in ("\r", "\n"):
                    if len(self._selected_values) >= self.min_selections:
                        result = list(self._selected_values)
                        self._clear()
                        return result
                elif key == "ESC":
                    self._clear()
                    return None

    def _toggle_current(self) -> None:
        value = self.choices[self.selected][0]
        if value in self._selected_values:
            if len(self._selected_values) > self.min_selections:
                self._selected_values.discard(value)
        else:
            if self.max_selections is None or len(self._selected_values) < self.max_selections:
                self._selected_values.add(value)

    def _toggle_all(self) -> None:
        all_values = [c[0] for c in self.choices]
        if self._selected_values == set(all_values):
            if self.min_selections == 0:
                self._selected_values.clear()
        else:
            if self.max_selections is None:
                self._selected_values = set(all_values)
            else:
                self._selected_values = set(all_values[:self.max_selections])

    def _render(self) -> None:
        eff_page = _effective_page_size(self.page_size, reserve=4)
        start, end = _visible_range(self.selected, self.scroll_offset, len(self.choices), eff_page)
        self.scroll_offset = start

        lines = 0
        if self.title:
            print(self._format_title())
            lines += 1

        for i in range(start, end):
            print(self._format_choice(i))
            lines += 1

        print(f"{DIM}  [space] toggle  [a] all  [enter] confirm  [esc] cancel{RESET}")
        lines += 1

        self._rendered = lines

    def _update(self) -> None:
        print(f"\033[{self._rendered}A\r\033[J", end="")
        self._render()

    def _clear(self) -> None:
        """Clear rendered menu lines from screen."""
        if self._rendered > 0:
            print(f"\033[{self._rendered}A\r\033[J", end="")
            self._rendered = 0

    def _format_title(self) -> str:
        s = self._style
        terminal_width = shutil.get_terminal_size().columns
        symbol = f"{s.title_style}{s.title_color}{s.title_symbol}{RESET}"
        count = f"({len(self._selected_values)} selected)"
        title = truncate_text(self.title, terminal_width - len(count) - 4)
        return f"{symbol} {title} {DIM}{count}{RESET}"

    def _format_choice(self, index: int) -> str:
        s = self._style
        width = _effective_width(self.max_width, prefix_len=6)
        value, text = self.choices[index]
        text = truncate_text(text, width)

        checked = value in self._selected_values
        checkbox = f"{GREEN}[x]{RESET}" if checked else f"{DIM}[ ]{RESET}"
        focused = index == self.selected

        if focused:
            return f"  {s.selected_color}{s.selected_indicator}{RESET} {checkbox} {s.selected_style}{text}{RESET}"
        else:
            return f"    {checkbox} {s.normal_style}{text}{RESET}"


# --- Helpers ---

def _print_inline_error(msg: str) -> None:
    """Print error on a new line below the input."""
    from .styles import RED
    print(f"{RED}x {msg}{RESET}")
