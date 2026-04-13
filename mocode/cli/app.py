"""CLI Application - main entry point for terminal interface"""

import asyncio
import os

from ..core.orchestrator import MocodeCore
from ..core.config import Config
from ..core.interrupt import InterruptToken
from ..tools import register_all_tools
from .commands import (
    CommandContext,
    CommandResult,
    CommandRegistry,
    CommandExecutor,
    BUILTIN_COMMANDS,
)
from .events import CLIEventHandler
from .monitor import ESCMonitor
from .ui.display import Display
from .ui.permission import CLIPermissionHandler


class CLIApp:
    """CLI Application - thin orchestrator.

    Coordinates initialization, main loop, and shutdown.
    All specific logic is delegated to specialized components.
    """

    def __init__(self):
        self.client: MocodeCore | None = None
        self.display = Display()
        self.commands = CommandRegistry()

        self._interrupt_token = InterruptToken()
        self._esc_monitor: ESCMonitor | None = None
        self._event_handler: CLIEventHandler | None = None
        self._command_executor: CommandExecutor | None = None
        self._is_running = False

    async def run(self) -> None:
        """Run the CLI application."""
        os.system("cls" if os.name == "nt" else "clear")

        self._initialize()
        try:
            await self._main_loop()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            self.display.error(str(e))
        finally:
            self._shutdown()

    def _initialize(self) -> None:
        """Initialize all components."""
        # Display
        self.display.initialize()

        # Tools
        register_all_tools()

        # Commands
        for cmd_class in BUILTIN_COMMANDS:
            self.commands.register(cmd_class())
        self._command_executor = CommandExecutor(self.commands)

        # Client
        permission_handler = CLIPermissionHandler(display=self.display)
        config = Config.load()

        self.client = MocodeCore(
            config=config,
            permission_handler=permission_handler,
            interrupt_token=self._interrupt_token,
        )

        # Events
        self._event_handler = CLIEventHandler(self.display)
        self._event_handler.setup(self.client.event_bus)

        # ESC Monitor
        self._esc_monitor = ESCMonitor(self._interrupt_token)
        self._esc_monitor.start()

        # Welcome
        self.display.welcome("mocode", self.client.config.display_name, os.getcwd())
        self._is_running = True

        # Dream scheduler
        self.client.start_dream_scheduler()

    def _shutdown(self) -> None:
        """Clean up all resources."""
        if self.client:
            self.client.stop_dream_scheduler()

        if self.client and self.client.agent.messages:
            session = self.client.save_session()
            self.display.session_saved(session.id, session.message_count)

        if self._esc_monitor:
            self._esc_monitor.stop()

        if self._event_handler and self.client:
            self._event_handler.teardown(self.client.event_bus)

        self.display.cleanup()
        self._is_running = False

    async def _main_loop(self) -> None:
        """Main input/output loop."""
        while self._is_running:
            try:
                user_input = await self.display.get_input()
                user_input = user_input.strip()

                if not user_input:
                    continue

                # Command
                if user_input.startswith("/"):
                    ctx = CommandContext(
                        client=self.client,
                        args=user_input,
                        extra={"display": self.display},
                    )
                    result = await self._command_executor.execute(ctx)
                    if isinstance(result, CommandResult):
                        if result.should_exit:
                            break
                        if result.data and result.data.get("type") == "skill_activated":
                            await self.client.chat(
                                f"/{result.data['skill_name']}\n\n{result.data['content']}"
                            )
                    continue

                # Chat
                await self.client.chat(user_input)

            except (KeyboardInterrupt, EOFError, asyncio.CancelledError):
                break
            except Exception as e:
                self.display.set_thinking(False)
                self.display.error(str(e))
