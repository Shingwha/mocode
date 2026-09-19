"""CLIApp — the terminal front-end.

Assembly (config → plugins → agent → session) lives in
:class:`~mocode.host.runtime.MoCode`, so an embedding application gets exactly
the same setup. This class adds only what a terminal needs: the REPL, input,
slash-command dispatch and Ctrl-C handling.

Its commands go in through :mod:`mocode.cli.plugin`, the same channel a
third-party plugin uses. Its renderer it installs itself: drawing a terminal is
not a plugin contribution, it is this frontend consuming the event stream.
"""

from __future__ import annotations

import asyncio
import signal
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from ..host.command import (
    CONTINUE,
    EXIT,
    CommandContext,
    CommandRegistry,
    CommandResult,
    Kind,
)
from ..host.config import Config
from ..host.runtime import MoCode

if TYPE_CHECKING:
    from .display import Display


class CLIApp:
    """Interactive CLI — a MoCode runtime plus a terminal."""

    def __init__(
        self,
        config: Config | None = None,
        display: "Display | None" = None,
        home: Path | None = None,
        interactive: bool = True,
        render: bool = False,
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

        # Built before the runtime: the input completer needs it.
        self.commands = CommandRegistry()
        self.display: Display | None = None
        if self.render:
            from .display import Display
            from .input import Input
            from .theme import Theme

            # A render-only run never prompts, so the Input goes unused — it is
            # cheap to build and prompt_toolkit is imported only on first use.
            self.display = display or Display(
                input_=Input(self.commands, ps1="❯"), theme=Theme()
            )

        from .plugin import PLUGIN as cli_plugin

        self.runtime = MoCode(
            config=self.config,
            home=self.home,
            cwd=self.cwd,
            display=self.display,
            commands=self.commands,
            extra_plugins=[cli_plugin],
        )

        # Installed here rather than contributed by a plugin: the renderer is
        # what this frontend does with the event stream. It is added after
        # `MoCode` so it can read the finished tool registry, and it survives a
        # config that disables the `cli` plugin — losing the terminal's commands
        # must not mean losing the screen.
        if self.display is not None:
            from .hook import CLIDisplayHook

            self.runtime.agent.hooks.add(
                CLIDisplayHook(self.display, self.runtime.tools)
            )

    # ── Dispatch ───────────────────────────────────────────

    async def _dispatch(self, text: str) -> CommandResult:
        """Resolve input: run a command if slash-prefixed, else send it to the agent."""
        parts = text.split(None, 1)
        cmd_text = parts[0].lower()
        args = parts[1] if len(parts) > 1 else ""

        cmd = self.commands.get(cmd_text)
        if cmd is not None:
            ctx = CommandContext(
                app=self.runtime,
                args=args,
                frontend=self.display,
                commands=self.commands,
            )
            return await cmd.handler(ctx)

        if text.startswith("/"):
            self._suggest_command(cmd_text)
            return CONTINUE

        return CommandResult(Kind.PROMPT, text)

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

    async def _run_chat(self, prompt: str) -> None:
        """Stream one turn into the display, interruptible with Ctrl-C.

        Rendering is the display hook's job — it consumes the same event stream
        a headless caller would, so this only pumps it.
        """

        async def pump() -> None:
            async for _ in self.runtime.chat(prompt):
                pass

        task = asyncio.ensure_future(pump())

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

    # ── REPL ───────────────────────────────────────────────

    async def _repl(self) -> None:
        try:
            while True:
                try:
                    user_input = await self.display.prompt()
                except (EOFError, KeyboardInterrupt):
                    print()
                    break
                if not user_input:
                    continue

                result = await self._dispatch(user_input)
                if result.kind is Kind.EXIT:
                    break
                if result.kind is Kind.PROMPT:
                    self.display.user_message(user_input)
                    await self._run_chat(result.prompt)

                    self.runtime.save_session()
        finally:
            self.runtime.save_session()

    def run(self) -> None:
        """Sync entry point for the interactive CLI."""
        try:
            asyncio.run(self._repl())
        except KeyboardInterrupt:
            self.runtime.save_session()

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
        if result:
            print(result)

    async def _oneshot(self, prompt: str, stdin_text: str | None):
        result = await self._dispatch(prompt)
        if result.kind is not Kind.PROMPT or not result.prompt:
            return None

        text = _compose_prompt(result.prompt, stdin_text)
        if self.display is None:
            return await self.runtime.agent.chat(text)

        # Rendered on the way past, so there is nothing left to print.
        await self._run_chat(text)
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
