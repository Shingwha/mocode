"""CLIApp — the terminal front-end.

Assembly (config → plugins → agent → conversation) lives in
:class:`~mocode.host.runtime.MoCode` and :class:`~mocode.host.conversation.Conversation`,
so an embedding application gets exactly the same setup. This class adds only
what a terminal needs: the REPL, input, slash-command dispatch and Ctrl-C.

It contributes nothing to the host. Its commands it registers on the registry it
owns, its rendering it does by subscribing to the conversation's event stream —
the same stream a browser or a test would read.
"""

from __future__ import annotations

import asyncio
import signal
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from ..core.events import Event, RunFailed, RunFinished
from ..host.command import (
    CONTINUE,
    CommandRegistry,
    CommandResult,
    Kind,
    dispatch,
)
from ..host.config import Config
from ..host.runtime import MoCode

if TYPE_CHECKING:
    from ..core.channel import Subscription
    from ..core.agent import Turn
    from .display import Display
    from .render import CLIRenderer


class CLIApp:
    """Interactive CLI — a MoCode runtime, one conversation, and a terminal."""

    def __init__(
        self,
        config: Config | None = None,
        display: "Display | None" = None,
        home: Path | None = None,
        interactive: bool = True,
        render: bool = False,
        plugin_dirs: list[Path] | None = None,
    ):
        """``render`` attaches a frontend without a REPL — a one-shot run draws
        its turn while it happens. It can only ask for rendering, never remove
        it: ``interactive`` already implies one.
        """
        self.home = home or Path.home() / ".mocode"
        self.cwd = Path.cwd()
        self.interactive = interactive
        self.render = interactive or render
        _fix_console()

        self.config = config or Config.load()
        if self.config is None:
            return  # caller checks and reports

        # Built before the conversation: the input completer reads the same
        # registry the terminal dispatches from.
        self.commands = CommandRegistry()

        self.display: "Display | None" = None
        self.input: "Input | None" = None
        if self.render:
            from .display import Display
            from .input import Input
            from .theme import Theme

            # A render-only run never prompts, so the Input goes unused — it is
            # cheap to build and prompt_toolkit is imported only on first use.
            self.input = Input(self.commands, ps1="❯")
            self.display = display or Display(input_=self.input, theme=Theme())

        self.runtime = MoCode(
            config=self.config, home=self.home, plugin_dirs=plugin_dirs
        )
        self.conversation = self.runtime.new_conversation(
            cwd=self.cwd, commands=self.commands
        )
        self.renderer: "CLIRenderer | None" = None
        if self.display is not None:
            from .render import CLIRenderer

            self.renderer = CLIRenderer(self.display, self.conversation)

        # The terminal's own contributions — its built-in commands, plus
        # whatever the project's plugins ship under `mocode.cli`. Built last,
        # so a plugin can reach the conversation it landed in.
        from .plugin import build_cli_plugins

        self.plugins = build_cli_plugins(
            self, self.runtime.plugin_sources_for(self.cwd)
        )

    # ── Dispatch ───────────────────────────────────────────

    async def _dispatch(self, text: str) -> CommandResult:
        """Resolve input: run a command if slash-prefixed, else send it to the agent."""
        head = text.split(None, 1)[0].lower()
        if text.startswith("/") and self.commands.get(head) is None:
            self._suggest_command(head)
            return CONTINUE
        return await dispatch(
            text, conversation=self.conversation, commands=self.commands
        )

    def _suggest_command(self, cmd_text: str) -> None:
        if self.display is None:
            return
        import difflib

        names = [c.name for c in self.commands.all()]
        matches = difflib.get_close_matches(cmd_text, names, n=1, cutoff=0.6)
        if matches:
            self.display.warn(f"Unknown command: {cmd_text} — did you mean {matches[0]}?")
        else:
            self.display.warn(f"Unknown command: {cmd_text}")

    # ── Chat ───────────────────────────────────────────────

    async def _run_chat(self, prompt: str, subscription: "Subscription") -> None:
        """Run one turn, drawing it as it happens. Ctrl-C stops the turn.

        The turn is started rather than merely streamed, so a stop is a decision
        about the conversation: the reader going away cancels it, and the
        terminal's own subscription keeps reading the rest of the session.
        """
        turn = self.conversation.run(prompt)

        task = asyncio.ensure_future(self._follow(subscription, turn))

        def _on_sigint(signum, frame):
            if not task.done():
                task.cancel()

        original_handler = signal.signal(signal.SIGINT, _on_sigint)
        try:
            await task
        except asyncio.CancelledError:
            self.display.warn("\nResponse interrupted.\n")
        finally:
            signal.signal(signal.SIGINT, original_handler)

    async def _follow(self, subscription: "Subscription", turn: "Turn") -> None:
        """Draw events until this turn ends. Leaving early stops the turn."""
        try:
            while True:
                event = await subscription.get()
                if event is None:  # the conversation is closed
                    return
                self._draw(event)
                if event.run_id == turn.id and isinstance(
                    event, (RunFinished, RunFailed)
                ):
                    return
        finally:
            turn.cancel()  # no-op once the turn has ended on its own

    def _draw(self, event: Event) -> None:
        if self.renderer is not None:
            self.renderer.draw(event)

    def _drain(self, subscription: "Subscription") -> None:
        """Draw what is already queued — commands publish outside any turn."""
        while True:
            event = subscription.take()
            if event is None:
                return
            self._draw(event)

    # ── REPL ───────────────────────────────────────────────

    async def _repl(self) -> None:
        subscription = self.conversation.subscribe()
        try:
            while True:
                self._drain(subscription)
                try:
                    user_input = await self.display.prompt()
                except (EOFError, KeyboardInterrupt):
                    print()
                    break
                if not user_input:
                    continue

                result = await self._dispatch(user_input)
                self._drain(subscription)
                if result.kind is Kind.EXIT:
                    break
                if result.kind is Kind.PROMPT:
                    self.display.user_message(user_input)
                    await self._run_chat(result.prompt, subscription)
                    self._drain(subscription)
                    self.conversation.save()
        finally:
            subscription.close()
            self.conversation.save()

    def run(self) -> None:
        """Sync entry point for the interactive CLI."""
        try:
            asyncio.run(self._repl())
        except KeyboardInterrupt:
            self.conversation.save()
        finally:
            self.conversation.close()

    # ── Oneshot ────────────────────────────────────────────

    def run_oneshot(self, prompt: str, stdin_text: str | None = None) -> None:
        """Non-interactive: run one query and exit.

        With a frontend attached the turn is drawn as it happens; without one
        only the answer is printed, which is what keeps ``-p`` pipeable.
        """
        try:
            result = asyncio.run(self._oneshot(prompt, stdin_text))
        except KeyboardInterrupt:
            print("\nInterrupted.", file=sys.stderr)
            sys.exit(1)
        finally:
            # A one-shot is not a session: it saves nothing, and releases
            # whatever the plugins built for it.
            self.conversation.close(save=False)
        if result:
            print(result)

    async def _oneshot(self, prompt: str, stdin_text: str | None):
        result = await self._dispatch(prompt)
        if result.kind is not Kind.PROMPT or not result.prompt:
            return None

        text = _compose_prompt(result.prompt, stdin_text)
        if self.display is None:
            return await self.conversation.chat(text)

        # Rendered on the way past, so there is nothing left to print.
        subscription = self.conversation.subscribe()
        try:
            await self._run_chat(text, subscription)
        finally:
            subscription.close()
        return None


def _compose_prompt(prompt: str, stdin_text: str | None) -> str:
    """Combine stdin context with the user's prompt."""
    if not stdin_text or not stdin_text.rstrip():
        return prompt
    return f"{stdin_text.rstrip()}\n\n---\n\n{prompt}"


def _fix_console() -> None:
    """Enable ANSI escape codes on Windows."""
    if sys.platform != "win32":
        return
    import ctypes

    try:
        ctypes.windll.kernel32.SetConsoleMode(
            ctypes.windll.kernel32.GetStdHandle(-11), 7
        )
    except Exception:
        pass
