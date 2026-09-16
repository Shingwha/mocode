"""HostContext — the shared blackboard between the host and its plugins."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ...core.agent import AgentLoop
    from ...core.hook import AgentHook
    from ...core.prompt import Section
    from ...core.provider import ModelSpec
    from ...core.tool import ToolRegistry
    from ..cli.commands import Command, CommandRegistry
    from ..cli.display import Display
    from ..config import Config


@dataclass
class HostContext:
    """Everything a plugin may read, and everything it may contribute to.

    Fields fall into three groups:

    - **Ready at construction**: paths, config, and the shared registries.
    - **Contribution targets**: ``tools`` / ``commands`` / ``hooks`` /
      ``prompt_sections`` — plugins write into these during ``build()``.
    - **Assigned later**: ``agent``, set by the host once the loop is built.
    """

    # ── Host state ──
    home: Path
    cwd: Path
    config: Config
    interactive: bool
    display: Display | None = None
    #: Facts about the model in use (name, context window, output cap). Known
    #: before assembly, so plugins may read it during build().
    model: ModelSpec | None = None

    # ── Contribution targets ──
    tools: ToolRegistry = None  # type: ignore[assignment]
    commands: CommandRegistry = None  # type: ignore[assignment]
    hooks: list[AgentHook] = field(default_factory=list)
    prompt_sections: list[Section] = field(default_factory=list)

    # ── Assigned after assembly ──
    agent: AgentLoop | None = None

    def __post_init__(self) -> None:
        if self.tools is None:
            from ...core.tool import ToolRegistry

            self.tools = ToolRegistry()
        if self.commands is None:
            from ..cli.commands import CommandRegistry

            self.commands = CommandRegistry()

    def plugin_config(self, name: str) -> dict:
        """Settings for plugin *name* from the ``plugins`` section of config.json."""
        return self.config.plugins.get(name, {})

    def register(self, *commands: Command) -> None:
        """Register slash commands contributed by a plugin."""
        for command in commands:
            self.commands.register(command)
