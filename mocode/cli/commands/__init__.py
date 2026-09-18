"""The terminal's built-in commands.

The command *contract* — ``Command``, ``CommandRegistry``, ``CommandResult`` —
lives in :mod:`mocode.host.command`, because a plugin can contribute a command
and any frontend can dispatch one. What lives here is only the terminal's own
three: they need a picker, a clipboard or a screen, so they belong to this
frontend rather than to the host.
"""
