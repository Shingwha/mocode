"""Command result - structured return value for all commands"""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class CommandResult:
    """Structured result returned by every command execution.

    Commands return this instead of printing directly.
    The caller (CLI wrapper, gateway, web) decides how to present it.
    """

    success: bool = True
    should_exit: bool = False
    message: str = ""
    data: Any = None
