"""The package entry — assembled from submodules by relative import.

When ``mocode/plugin.py`` is absent and ``mocode/plugin/__init__.py`` is
present, this file *is* the entry module the loader imports. It is where
the pieces meet: each submodule owns one contribution, and this file only
wires them onto the plugin.

The module-level ``plugin`` instance is the convention that cannot miss in
package form — the loader takes it before it ever looks for a class, and
``vars()`` of a package only sees the names ``__init__.py`` imported, so a
class buried in a submodule would stay invisible anyway.
"""

from __future__ import annotations

from mocode.plugins import BuildContext, Plugin

from .commands import motd_command
from .sections import motd_section


class MotdPlugin(Plugin):
    name = "multi-file"
    description = "A prompt section and a /motd command, in package form"

    def build(self, ctx: BuildContext) -> None:
        ctx.commands.register(motd_command())
        ctx.prompt_sections.append(motd_section())


plugin = MotdPlugin()
