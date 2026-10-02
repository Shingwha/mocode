"""The plugin's terminal contributions — the `mocode.cli/` namespace.

The drawer for the host side's ``diff-demo/patch`` kind: a unified diff,
line by line — additions in the success colour, removals in the error
colour, hunk headers in the info colour, context dimmed. This is the
official example of the message-drawer surface: one ``kind`` registered
with ``ctx.drawers.register``, and every frontend that ships no drawer for
it falls back to the message's own summary.
"""

from __future__ import annotations

from mocode.cli import CLIContext, CLIPlugin
from mocode.cli import lines as L
from mocode.plugins import PluginMessage

#: The first character of a diff row says what it is; the theme colour
#: names say how it should read.
_STYLES = {"+": "success", "-": "error", "@": "info"}


def draw_diff(event: PluginMessage) -> list[L.Line]:
    """A unified diff as lines, one per row, coloured by its leading sign."""
    rows = str(event.data.get("diff", "")).splitlines()
    if not rows:
        return [L.notice("(empty diff)", "info")]
    return [L.Line(text=row, style=_STYLES.get(row[:1], "dim")) for row in rows]


class MessageDrawersCLI(CLIPlugin):
    name = "message-drawers.cli"
    description = "message-drawers in the terminal: a drawer for diff-demo/patch"

    def build(self, ctx: CLIContext) -> None:
        ctx.drawers.register("diff-demo/patch", draw_diff)


plugin = MessageDrawersCLI()
