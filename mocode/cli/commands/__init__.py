"""The terminal's own commands.

The command *contract* — ``Command``, ``CommandRegistry``, ``CommandResult`` —
lives in :mod:`mocode.host.command`, because any frontend can dispatch one and a
plugin can contribute one. These are the terminal's: ``/model`` and ``/resume``
need a picker and ``/copy`` needs a clipboard.

They are registered by :class:`~mocode.cli.plugin.BuiltinCommands` — the first
implementation of the terminal's plugin interface, so that contributing to the
terminal has exactly one shape whether you are MoCode or a third party.
"""

from __future__ import annotations

from . import misc, model, session

#: Every command the terminal ships, in the order they are registered.
COMMANDS = (*misc.commands, *model.commands, *session.commands)

__all__ = ["COMMANDS", "misc", "model", "session"]
