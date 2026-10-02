"""The plugin's host contributions — the `mocode/` namespace.

The `diff-demo` tool does no real work: it computes a small unified diff
between two fixed texts and *says so* as a
:class:`~mocode.core.events.PluginMessage` whose kind is ``diff-demo/patch``.
The payload is plain data, so every frontend receives it; how a diff looks
on screen is each frontend's own decision — the terminal's lives in
``mocode.cli/plugin.py`` of this same plugin, registered as a drawer for
that kind. Data rides the conversation, rendering rides the frontend.
"""

from __future__ import annotations

import difflib

from mocode.plugins import Plugin, PluginMessage, Tool

#: Two tiny fixed texts — the demo diff is deterministic, and owes nothing
#: to the repository it runs in.
_BEFORE = "one\ntwo\nthree\n"
_AFTER = "one\nTWO\nthree\nfour\n"


def _demo_diff() -> str:
    return "\n".join(
        difflib.unified_diff(
            _BEFORE.splitlines(),
            _AFTER.splitlines(),
            fromfile="before.txt",
            tofile="after.txt",
            lineterm="",
        )
    )


async def _diff_demo(args: dict, ctx) -> str:
    """A tool that publishes its result as a plugin message, in the W2-A shape."""
    await ctx.emit(
        PluginMessage(kind="diff-demo/patch", data={"diff": _demo_diff()})
    )
    return "A demo diff was emitted as a diff-demo/patch plugin message."


class MessageDrawersPlugin(Plugin):
    name = "message-drawers"
    description = "A diff-demo tool that speaks as a plugin message"

    def build(self, ctx) -> None:
        ctx.tools.register(
            Tool(
                name="diff-demo",
                description=(
                    "Demonstrate plugin messages: emit a small unified diff "
                    "as a diff-demo/patch plugin message."
                ),
                schema={"type": "object", "properties": {}},
                func=_diff_demo,
                with_context=True,
            )
        )


plugin = MessageDrawersPlugin()
