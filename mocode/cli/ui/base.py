"""Component base classes for unified UI system."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Optional


class ComponentState(Enum):
    """Component lifecycle states."""

    CREATED = auto()     # Created but not rendered
    RENDERING = auto()   # Currently rendering
    ACTIVE = auto()      # Active and interactive
    COMPLETED = auto()   # Result determined, possibly cleared
    CLEARED = auto()     # Removed from display


@dataclass
class ComponentStyle:
    """Style configuration for a component."""

    symbol: str = ""
    color: str = ""
    text_style: str = ""  # BOLD, DIM
    bg_color: str = ""    # Background color for completed state

    def format(self, text: str) -> str:
        """Format text with this style.

        Args:
            text: Text to format

        Returns:
            Formatted text with ANSI codes
        """
        parts = []
        if self.color:
            parts.append(self.color)
        if self.text_style:
            parts.append(self.text_style)
        if self.bg_color:
            parts.append(self.bg_color)

        if parts:
            prefix = "".join(parts)
            return f"{prefix}{text}\033[0m"
        return text

    def format_symbol(self) -> str:
        """Format the symbol with color only."""
        if self.symbol and self.color:
            return f"{self.color}{self.symbol}\033[0m"
        return self.symbol


class Component(ABC):
    """Base class for all UI components.

    Components have a lifecycle:
    1. CREATED - Component created but not shown
    2. RENDERING - Component is being rendered (for animated components)
    3. ACTIVE - Component is interactive (for input/select)
    4. COMPLETED - Component has a result
    5. CLEARED - Component removed from display

    State transitions trigger optional callbacks for coordination.
    """

    def __init__(self, component_id: str | None = None, type_hint: str = ""):
        """Initialize component.

        Args:
            component_id: Optional unique identifier
            type_hint: Type hint for spacing coordinator
        """
        self.id = component_id or str(id(self))
        self.type_hint = type_hint
        self._state = ComponentState.CREATED
        self._rendered_lines: int = 0
        self._result: Any = None
        self._on_state_change: Callable[[ComponentState, ComponentState], None] | None = None

    @property
    def state(self) -> ComponentState:
        """Current component state."""
        return self._state

    @property
    def result(self) -> Any:
        """Component result (available after COMPLETED)."""
        return self._result

    @property
    def rendered_lines(self) -> int:
        """Number of lines rendered."""
        return self._rendered_lines

    def set_state(self, new_state: ComponentState) -> None:
        """Set component state and trigger callback.

        Args:
            new_state: New state to set
        """
        old_state = self._state
        if old_state == new_state:
            return
        self._state = new_state
        if self._on_state_change:
            self._on_state_change(old_state, new_state)

    def on_state_change(self, callback: Callable[[ComponentState, ComponentState], None]) -> None:
        """Register state change callback.

        Args:
            callback: Function called with (old_state, new_state)
        """
        self._on_state_change = callback

    @abstractmethod
    def render(self) -> str:
        """Render component content.

        Returns:
            Rendered content string
        """
        pass

    def clear(self) -> str:
        """Clear component from display.

        Returns:
            ANSI escape sequence to clear component
        """
        if self._rendered_lines > 0:
            # Move cursor up and clear
            seq = f"\033[{self._rendered_lines}A\r\033[J"
            self._rendered_lines = 0
            self.set_state(ComponentState.CLEARED)
            return seq
        return ""

    def print_clear(self) -> None:
        """Print clear sequence."""
        if self._rendered_lines > 0:
            print(f"\033[{self._rendered_lines}A\r\033[J", end="")
            self._rendered_lines = 0
            self.set_state(ComponentState.CLEARED)

    def complete(self, result: Any = None) -> None:
        """Mark component as completed.

        Args:
            result: Component result value
        """
        self._result = result
        self.set_state(ComponentState.COMPLETED)

    def show(self) -> Any:
        """Show component and return result.

        This is the main entry point for synchronous components.
        Override for async or interactive components.

        Returns:
            Component result
        """
        content = self.render()
        self._rendered_lines = content.count("\n") + 1
        print(content)
        self.set_state(ComponentState.COMPLETED)
        return self._result
