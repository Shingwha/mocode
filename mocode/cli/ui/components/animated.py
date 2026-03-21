"""Animated component for spinner animations."""

import asyncio
import shutil
from typing import Any, Coroutine

from ..base import Component, ComponentState
from ..colors import RESET
from .styles import AnimatedStyle, DEFAULT_ANIMATED_STYLE


class Animated(Component):
    """Animated spinner component.

    Features:
    - Async animation with configurable frames
    - Optional clear on completion
    - Text update during animation
    - Run with coroutine support
    """

    def __init__(
        self,
        text: str = "Thinking",
        style: AnimatedStyle | None = None,
        clear_on_complete: bool = True,
        type_hint: str = "thinking"
    ):
        """Initialize Animated component.

        Args:
            text: Animation text
            style: Custom style configuration
            clear_on_complete: Whether to clear on stop
            type_hint: Type hint for spacing coordinator
        """
        super().__init__(type_hint=type_hint)
        self.text = text
        self._style = style or DEFAULT_ANIMATED_STYLE
        self._clear_on_complete = clear_on_complete

        self._task: asyncio.Task | None = None
        self._is_running = False
        self._frame_index = 0

    @property
    def is_running(self) -> bool:
        """Whether animation is currently running."""
        return self._is_running

    def render(self) -> str:
        """Render current frame.

        Returns:
            Current frame string
        """
        frames = self._style.frames
        frame = frames[self._frame_index % len(frames)]
        color = self._style.color
        text_style = self._style.text_style

        return f"{color}{frame}{RESET} {text_style}{self.text}{RESET}"

    async def start(self) -> None:
        """Start the animation."""
        if self._is_running:
            return

        self._is_running = True
        self.set_state(ComponentState.RENDERING)

        try:
            while self._is_running:
                frame = self.render()
                # Use \r to stay on same line
                print(f"\r{frame}", end="", flush=True)
                self._frame_index += 1
                await asyncio.sleep(self._style.interval)
        except asyncio.CancelledError:
            pass

    def stop(self, clear: bool | None = None) -> None:
        """Stop the animation.

        Args:
            clear: Override clear_on_complete (None uses default)
        """
        self._is_running = False

        if self._task:
            self._task.cancel()
            self._task = None

        should_clear = clear if clear is not None else self._clear_on_complete

        if should_clear:
            # Clear the animation line
            width = shutil.get_terminal_size().columns
            print(f"\r{' ' * width}\r", end="")
        else:
            # Just move to next line
            print()

        self.set_state(ComponentState.COMPLETED)

    async def run_with(self, coro: Coroutine) -> Any:
        """Run a coroutine while showing animation.

        Args:
            coro: Coroutine to run

        Returns:
            Coroutine result
        """
        self._task = asyncio.create_task(self.start())

        try:
            result = await coro
            self.stop()
            return result
        except Exception as e:
            self.stop()
            raise e

    def update_text(self, text: str) -> None:
        """Update animation text.

        Args:
            text: New text to display
        """
        self.text = text

    def show(self) -> None:
        """Show single frame (for synchronous use).

        Note: For async animation, use start() instead.
        """
        frame = self.render()
        print(frame)
        self._rendered_lines = 1
        self.set_state(ComponentState.COMPLETED)
