"""Built-in commands — aggregator for backward compatibility.

Submodules:
  - misc.py     — /quit, /help, /clear, /copy, /compact
  - model.py    — /model
  - session.py  — /resume, /export
  - connect.py  — /connect
"""

from __future__ import annotations

from . import Command
from .connect import (
    _connect,
    _connect_add,
    _connect_edit,
    _connect_extra_body,
    _mask_key,
)
from .misc import _clear, _compact, _copy, _help, _quit
from .model import _model
from .session import _export, _resume, _resume_from_file, _resume_interactive

# Re-export all handler functions for backward-compatible imports
__all__ = [
    "commands",
    # misc
    "_quit",
    "_help",
    "_clear",
    "_copy",
    "_compact",
    # model
    "_model",
    # session
    "_export",
    "_resume",
    "_resume_from_file",
    "_resume_interactive",
    # connect
    "_connect",
    "_connect_add",
    "_connect_edit",
    "_connect_extra_body",
    "_mask_key",
]

# Aggregate commands from all submodules
commands: list[Command] = (
    []  # type: ignore[assignment]
    # populated below to avoid import cycle with __init__
)

from .connect import commands as _connect_cmds
from .misc import commands as _misc_cmds
from .model import commands as _model_cmds
from .session import commands as _session_cmds

commands = [*_misc_cmds, *_model_cmds, *_session_cmds, *_connect_cmds]
