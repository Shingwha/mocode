"""effort plugin — the conversation's reasoning effort, as a command.

``/effort`` reports the level the conversation currently sends and the levels
the model offers; ``/effort <level>`` switches this conversation (never the
config file). Both need nothing but a conversation to publish on, so every
frontend gets them — an interactive picker over the same levels is a
frontend choice, not a requirement.
"""

from __future__ import annotations

from ...command import CONTINUE, Command, CommandContext, CommandResult
from ..base import Plugin
from ..context import BuildContext


async def _effort(ctx: CommandContext) -> CommandResult:
    conversation = ctx.conversation
    spec = conversation.model
    if spec is None:
        await conversation.notify("No model is set for this conversation.", level="warn")
        return CONTINUE

    levels = list(spec.efforts)
    arg = ctx.args.strip()
    if not arg:
        current = spec.effort or "(server default)"
        await conversation.notify(
            f"Reasoning effort: {current} — available: {', '.join(levels)}"
        )
        return CONTINUE

    if arg not in levels:
        await conversation.notify(
            f"Unknown effort '{arg}' — available: {', '.join(levels)}", level="warn"
        )
        return CONTINUE

    # A decision about this conversation only — nothing is written to
    # config.json. The ``effort`` on a model entry is the default new
    # conversations start from; this command only changes what the current
    # one sends.
    conversation.set_effort(arg)
    await conversation.notify(f"Reasoning effort: {arg}")
    return CONTINUE


class EffortPlugin(Plugin):
    name = "effort"
    description = "Reasoning effort: /effort shows or sets the level"

    def build(self, ctx: BuildContext) -> None:
        ctx.commands.register(
            Command(
                "/effort",
                "Show or set the reasoning effort (e.g. /effort high)",
                handler=_effort,
            )
        )


PLUGIN = EffortPlugin()
