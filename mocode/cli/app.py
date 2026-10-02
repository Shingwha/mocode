"""CLIApp — the terminal front-end.

Assembly (config → plugins → agent → conversation) lives in
:class:`~mocode.host.runtime.MoCode` and :class:`~mocode.host.conversation.Conversation`,
so an embedding application gets exactly the same setup. This class adds only
what a terminal needs: the REPL, input, slash-command dispatch, key handling
while a turn runs and Ctrl-C.

It contributes nothing to the host. Its commands it registers on the registry it
owns, its rendering it does by subscribing to the conversation's event stream —
the same stream a browser or a test would read.
"""

from __future__ import annotations

import asyncio
import difflib
import inspect
import logging
import signal
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from ..core.channel import Subscription
from ..core.events import RunFailed, RunFinished
from ..core.tool import ToolError
from ..host.command import (
    CONTINUE,
    CommandRegistry,
    CommandResult,
    Kind,
)
from ..host.config import Config
from ..host.runtime import MoCode
from .plugin import KeyContext

_log = logging.getLogger(__name__)

if TYPE_CHECKING:
    from ..core.agent import Turn
    from .display import Display
    from .input import Input
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
        _render = interactive or render

        self.config = config or Config.load()
        if self.config is None:
            raise ValueError(
                f"No usable config at {self.home / 'config.json'} — create it first."
            )

        # Built before the conversation: the input completer reads the same
        # registry the terminal dispatches from.
        self.commands = CommandRegistry()

        # The plugin contribution surfaces the input layer and the status bar
        # read. Plain containers: plugins fill them through ctx during build,
        # the Input picks them up when its PromptSession is first created.
        from .plugin import (
            HeaderRegistry,
            InputMiddleware,
            KeyRegistry,
            StatusRegistry,
        )

        self.keys = KeyRegistry()
        self.input_middleware = InputMiddleware()
        self.status = StatusRegistry(state_fn=self._status_state)
        self.header = HeaderRegistry()
        self._running = False

        self.display: "Display | None" = display
        self.input: "Input | None" = None
        if _render and self.display is None:
            from .display import Display
            from .input import Input
            from .theme import Theme

            self.theme = Theme()
            # A render-only run never prompts, so the Input goes unused — it is
            # cheap to build and prompt_toolkit is imported only on first use.
            # key_context is called only once a key is pressed, long after the
            # conversation and ui exist, so deferring the lookups is safe.
            self.input = Input(
                self.commands,
                ps1="❯",
                keys=self.keys,
                key_context=lambda buffer=None: self._key_context(buffer=buffer),
                middleware=self.input_middleware,
                status=self.status,
            )
            self.display = Display(input_=self.input, theme=self.theme)
        else:
            # A display handed in from outside carries its own theme; the
            # context only gets a view when we can see it.
            self.theme = getattr(display, "_t", None)
        if self.display is not None:
            self.header.bind(self.display.print)

        self.runtime = MoCode(
            config=self.config, home=self.home, plugin_dirs=plugin_dirs
        )
        self.conversation = self.runtime.new_conversation(
            cwd=self.cwd, commands=self.commands
        )

        # Rendering is a subscription, as ever — now folded into a transcript
        # and painted from it. The drawer table sits between them: it is where
        # message-like events pick up their lines, so the plugins'
        # registrations (built below) and the renderer share one table.
        from .plugin import DrawerRegistry

        self.drawers = DrawerRegistry()
        self.renderer: "CLIRenderer | None" = None
        if self.display is not None:
            from .render import CLIRenderer

            self.renderer = CLIRenderer(
                self.display, self.conversation, drawers=self.drawers
            )

        # The terminal's own contributions — the commands that need a terminal,
        # plus whatever the project's plugins ship under `mocode.cli`. Built
        # last, so a plugin can reach the conversation it landed in; the
        # commands a conversation offers itself (/export, /clear, /help) were
        # registered before it existed, by the host's built-in plugins.
        from .plugin import CLIContext, UI, build_cli_plugins

        self.ui = UI(
            self.conversation,
            is_interactive=bool(
                self.interactive and self.display is not None and self.display.live
            ),
        )
        self.ctx = CLIContext(
            commands=self.commands,
            drawers=self.drawers,
            ui=self.ui,
            keys=self.keys,
            input=self.input_middleware,
            status=self.status,
            header=self.header,
            theme=self.theme,
            conversation=self.conversation,
        )
        self.plugins = build_cli_plugins(
            self.ctx, self.runtime.plugin_sources_for(self.cwd)
        )
        self._register_builtin_keys()
        self._register_builtin_status()

    def _register_builtin_status(self) -> None:
        """The bar the terminal always shows: model, tokens, cwd."""
        from .plugin import Segment

        self.status.use(lambda s: Segment(s.model, priority=30) if s.model else None)

        def _tokens(s):
            if s.usage is None or not (
                s.usage.prompt_tokens or s.usage.completion_tokens
            ):
                return None
            return Segment(
                f"↑{s.usage.prompt_tokens} ↓{s.usage.completion_tokens}", priority=20
            )

        self.status.use(_tokens)
        self.status.use(lambda s: Segment(_shorten_home(s.cwd), priority=10))

    def _register_builtin_keys(self) -> None:
        """The terminal's own running-time keys — the flagships of the API."""
        self.keys.add(
            "c-b",
            self._promote_foreground,
            when="running",
            description="Move the running command to the background",
        )
        self.keys.add(
            "c-o",
            self._toggle_verbose,
            when="running",
            description="Toggle verbose tool output",
        )

    def _key_context(self, buffer=None, turn: "Turn | None" = None) -> KeyContext:
        """What a key handler sees — built per key press, never stored."""
        return KeyContext(
            conversation=self.conversation, ui=self.ui, buffer=buffer, turn=turn
        )

    def _status_state(self):
        """The world as the status bar should report it — rebuilt per redraw."""
        from .plugin import StatusState

        return StatusState(
            model=self.conversation.model_name,
            cwd=self.cwd,
            running=self._running,
            usage=self.conversation.agent.last_usage,
            pending_approvals=0,
        )

    def _register_builtin_keys(self) -> None:
        """The terminal's own running-time keys — the flagships of the API."""

    # ── Dispatch ───────────────────────────────────────────

    async def _dispatch(self, text: str) -> CommandResult:
        """Resolve input: run a command if slash-prefixed, else send it to the agent."""
        head = text.split(None, 1)[0].lower()
        if text.startswith("/") and head not in self.commands:
            self._suggest_command(head)
            return CONTINUE
        return await self.commands.dispatch(text, conversation=self.conversation)

    def _suggest_command(self, cmd_text: str) -> None:
        if self.display is None:
            return
        names = [c.name for c in self.commands.all()]
        matches = difflib.get_close_matches(cmd_text, names, n=1, cutoff=0.6)
        if matches:
            self.display.warn(f"Unknown command: {cmd_text} — did you mean {matches[0]}?")
        else:
            self.display.warn(f"Unknown command: {cmd_text}")

    # ── Chat ───────────────────────────────────────────────

    async def _run_chat(self, prompt: str, subscription: Subscription) -> None:
        """Run one turn, drawing it as it happens. Ctrl-C stops the turn.

        The turn is started rather than merely streamed, so a stop is a decision
        about the conversation: the reader going away cancels it, and the
        terminal's own subscription keeps reading the rest of the session.
        """
        turn = self.conversation.run(prompt)

        task = asyncio.ensure_future(self._follow(subscription, turn))
        self._running = True
        keys_task = self._start_running_keys(turn)

        def _on_sigint(signum, frame):
            if not task.done():
                task.cancel()

        original_handler = signal.signal(signal.SIGINT, _on_sigint)
        try:
            await task
        except asyncio.CancelledError:
            self.display.print()
            self.display.warn("Response interrupted.")
        finally:
            signal.signal(signal.SIGINT, original_handler)
            self._running = False
            if keys_task is not None:
                keys_task.cancel()
                await asyncio.gather(keys_task, return_exceptions=True)

    def _start_running_keys(self, turn: "Turn"):
        """The raw key loop for this turn — nothing without an interactive TTY."""
        if not self.ui.is_interactive or not sys.stdin.isatty():
            return None
        return asyncio.ensure_future(self._running_keys(turn))

    async def _running_keys(self, turn: "Turn") -> None:
        """Read the terminal raw while a turn runs and dispatch running keys.

        Started by :meth:`_start_running_keys`; cancelled by ``_run_chat`` the
        moment the turn ends. A failure here costs the key channel, never the
        turn — the SIGINT fallback still cancels a run the keys cannot.
        """
        from prompt_toolkit.input import create_input
        from prompt_toolkit.keys import Keys

        try:
            source = create_input(sys.stdin)
        except Exception:
            return
        loop = asyncio.get_running_loop()
        ready = asyncio.Event()

        def _wake() -> None:
            loop.call_soon_threadsafe(ready.set)

        try:
            with source.raw_mode(), source.attach(_wake):
                while True:
                    await ready.wait()
                    ready.clear()
                    for press in source.read_keys():
                        name = (
                            press.key.value if isinstance(press.key, Keys) else press.key
                        )
                        await self._dispatch_running_key(name, turn)
        except asyncio.CancelledError:
            raise
        except (EOFError, OSError):
            pass  # the terminal went away; the turn outlives the keys
        except Exception:
            _log.exception("the running-key loop died")
        finally:
            try:
                source.close()
            except Exception:
                pass

    async def _dispatch_running_key(self, name: str, turn: "Turn") -> None:
        """One key while a turn runs: Esc and Ctrl-C cancel it, the rest dispatch."""
        if name in ("escape", "c-c"):
            turn.cancel()
            return
        binding = self.keys.running(name)
        if binding is None:
            return
        result = binding.handler(self._key_context(turn=turn))
        if inspect.isawaitable(result):
            await result

    async def _promote_foreground(self, kctx: KeyContext) -> None:
        """Ctrl+B — lift the one running foreground command into the background."""
        bash = kctx.conversation.tools.get("bash")
        session = getattr(bash, "session", None)
        if session is None:
            return  # no shell in this conversation — nothing to move
        try:
            handle = await session.promote()
        except ToolError as e:
            if e.code == "no_running_call":
                return  # quiet by design: pressing it early is not an error
            raise
        await kctx.ui.message(f"moved to background as {handle['shell_id']}")

    def _toggle_verbose(self, kctx: KeyContext) -> None:
        """Ctrl+O — flip whether landed calls keep their output tail, and repaint."""
        if self.renderer is None:
            return
        # The painter belongs to the renderer; the flag is its public surface
        # (wired by the renderer work), and this is the one place the app
        # reaches through for an immediate repaint.
        painter = self.renderer._painter
        painter.verbose = not painter.verbose
        painter.redraw_all(self.renderer._transcript)

    async def _follow(self, subscription: Subscription, turn: "Turn") -> None:
        """Draw events until this turn ends. Leaving early stops the turn."""
        try:
            while True:
                event = await subscription.get()
                if event is None:  # the conversation is closed
                    return
                if self.renderer is not None:
                    self.renderer.draw(event)
                if event.run_id == turn.id and isinstance(
                    event, (RunFinished, RunFailed)
                ):
                    return
        finally:
            turn.cancel()  # no-op once the turn has ended on its own

    def _drain(self, subscription: Subscription) -> None:
        """Draw what is already queued — commands publish outside any turn."""
        while True:
            event = subscription.take()
            if event is None:
                return
            if self.renderer is not None:
                self.renderer.draw(event)

    # ── REPL ───────────────────────────────────────────────

    async def _repl(self) -> None:
        subscription = self.conversation.subscribe()
        try:
            while True:
                self._drain(subscription)
                try:
                    user_input = await self.display.prompt()
                except (EOFError, KeyboardInterrupt):
                    self.display.print()
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
        finally:
            subscription.close()
            # Full lifecycle close, inside the loop: the terminal event lands
            # and plugins are released before the channel goes away.
            await self.ctx.aclose()
            await self.conversation.aclose()

    def run(self) -> None:
        """Sync entry point for the interactive CLI."""
        try:
            asyncio.run(self._repl())
        except KeyboardInterrupt:
            self.conversation.save()
        finally:
            # aclose() already ran on every normal path; this only releases
            # what an abrupt exit left behind.
            self.conversation.close(save=False)

    # ── Oneshot ────────────────────────────────────────────

    def run_oneshot(self, prompt: str, stdin_text: str | None = None) -> None:
        """Non-interactive: run one query and exit.

        With a frontend attached the turn is drawn as it happens; without one
        only the answer is printed, which is what keeps ``-p`` pipeable.
        """
        try:
            result = asyncio.run(self._oneshot(prompt, stdin_text))
        except KeyboardInterrupt:
            if self.display is not None:
                self.display.print()
                self.display.error("Interrupted.")
            else:
                print("\nInterrupted.", file=sys.stderr)
            sys.exit(1)
        finally:
            # A one-shot is not a session: it saves nothing, and releases
            # whatever the plugins built for it.
            self.conversation.close(save=False)
        if result:
            print(result)

    async def _oneshot(self, prompt: str, stdin_text: str | None):
        try:
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
        finally:
            # A one-shot is not a session: it saves nothing, and releases
            # whatever the plugins built for it.
            await self.ctx.aclose()
            await self.conversation.aclose(save=False)


def _shorten_home(cwd: Path) -> str:
    """The cwd with the home directory contracted to ``~``."""
    home = Path.home()
    if cwd == home:
        return "~"
    try:
        return f"~/{cwd.relative_to(home)}"
    except ValueError:
        return str(cwd)


def _compose_prompt(prompt: str, stdin_text: str | None) -> str:
    """Combine stdin context with the user's prompt."""
    if not stdin_text or not stdin_text.rstrip():
        return prompt
    return f"{stdin_text.rstrip()}\n\n---\n\n{prompt}"
