"""Component coordinator for managing UI elements."""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional

from .base import Component, ComponentState

if TYPE_CHECKING:
    from .components import Animated, Input, Message, Select
    from .components.styles import MessagePreset


@dataclass
class SpacingRule:
    """Rules for spacing between components."""

    # Types that need leading space
    before_types: set[str] = field(
        default_factory=lambda: {
            "assistant",
            "tool_call",
            "tool_result",
            "permission",
            "error",
            "exit",
            "command",
            "user_input",
        }
    )
    # Pairs that don't need space between them (previous, current)
    no_space_pairs: set[tuple[str, str]] = field(
        default_factory=lambda: {
            ("tool_call", "tool_result"),
            ("user_input", "assistant"),
            ("user_input", "tool_call"),
        }
    )


class ComponentCoordinator:
    """Coordinates UI components.

    Responsibilities:
    - Manage component lifecycle
    - Handle spacing between components
    - Track last component type for spacing decisions
    """

    DEFAULT_SPACING_RULE = SpacingRule()

    def __init__(self, spacing_rule: SpacingRule | None = None):
        """Initialize coordinator.

        Args:
            spacing_rule: Custom spacing rules (uses default if None)
        """
        self._components: dict[str, Component] = {}
        self._spacing_rule = spacing_rule or self.DEFAULT_SPACING_RULE
        self._last_type: str | None = None

    def register(self, component: Component, type_hint: str = "") -> str:
        """Register a component.

        Args:
            component: Component to register
            type_hint: Override component's type_hint

        Returns:
            Component ID
        """
        component_id = component.id
        if type_hint:
            component.type_hint = type_hint
        self._components[component_id] = component

        # Register state change callback
        component.on_state_change(self._on_component_state_change)

        return component_id

    def unregister(self, component_id: str) -> None:
        """Unregister a component.

        Args:
            component_id: ID of component to remove
        """
        if component_id in self._components:
            del self._components[component_id]

    def _on_component_state_change(
        self, old_state: ComponentState, new_state: ComponentState
    ) -> None:
        """Handle component state changes."""
        pass  # Can be extended for event emission

    def print_space_if_needed(self, current_type: str) -> None:
        """Print leading space if needed based on last type.

        Args:
            current_type: Type of current component
        """
        # First message doesn't need space
        if self._last_type is not None:
            # Check no-space pairs
            pair = (self._last_type, current_type)
            if pair not in self._spacing_rule.no_space_pairs:
                # Check if current type needs space
                if (
                    current_type in self._spacing_rule.before_types
                    or self._last_type not in self._spacing_rule.before_types
                ):
                    print()
        self._last_type = current_type

    def reset_spacing(self) -> None:
        """Reset spacing state."""
        self._last_type = None

    def set_last_type(self, type_hint: str) -> None:
        """Set last type without printing space.

        Args:
            type_hint: Type to set
        """
        self._last_type = type_hint

    # Factory methods for creating components

    def message(
        self, text: str, preset: "MessagePreset", type_hint: str = ""
    ) -> "Message":
        """Create and register a Message component.

        Args:
            text: Message text
            preset: Message preset style
            type_hint: Optional type hint override

        Returns:
            Registered Message component
        """
        from .components import Message

        msg = Message(text, preset)
        self.register(msg, type_hint or "message")
        return msg

    def input(
        self, message: str = "", type_hint: str = "user_input", **kwargs
    ) -> "Input":
        """Create and register an Input component.

        Args:
            message: Prompt message
            type_hint: Type hint (default "user_input")
            **kwargs: Additional Input arguments

        Returns:
            Registered Input component
        """
        from .components import Input

        inp = Input(message, **kwargs)
        self.register(inp, type_hint)
        return inp

    def select(
        self, title: str, choices: list, type_hint: str = "select", **kwargs
    ) -> "Select":
        """Create and register a Select component.

        Args:
            title: Menu title
            choices: List of (value, label) tuples
            type_hint: Type hint
            **kwargs: Additional Select arguments

        Returns:
            Registered Select component
        """
        from .components import Select

        sel = Select(title, choices, **kwargs)
        self.register(sel, type_hint)
        return sel

    def animated(
        self, text: str = "Thinking", type_hint: str = "thinking", **kwargs
    ) -> "Animated":
        """Create and register an Animated component.

        Args:
            text: Animation text
            type_hint: Type hint
            **kwargs: Additional Animated arguments

        Returns:
            Registered Animated component
        """
        from .components import Animated

        anim = Animated(text, **kwargs)
        self.register(anim, type_hint)
        return anim
