"""shell plugin — run commands in a persistent bash session.

The session is asynchronous so output can be reported as it is produced: a
command that takes a minute is watchable rather than a blank wait, and a
timeout can actually kill the child process instead of abandoning a thread.

Commands run either in the **foreground** — the tool blocks until the command
is done, streaming each line as it lands — or in the **background**, where the
tool returns a handle immediately (``shell_1``) and the model comes back for
the output when it wants it: ``bash_output`` reads incrementally (each read
returns only what arrived since the last one), ``kill_shell`` stops a job.
Background output accumulates in bounded rings, so a dev server that runs
overnight cannot grow memory without bound; what the rings dropped is counted
and reported.

Every command runs in its own process group (POSIX), so a timeout or an
explicit kill takes down the whole tree — ``bash -c "npm run dev"`` must not
leave its node children behind. On Windows there are no process groups: the
direct child is killed, and grandchildren survive it (a known limitation).

The package is split by responsibility — :mod:`.ring` the bounded output
buffer, :mod:`.session` the persistent bash session, :mod:`.tool` the tool
builders, :mod:`.plugin` the plugin — and re-exports the names the original
single module exposed, so ``from ...builtin.shell import ShellPlugin,
bash_tool`` keeps working.
"""

from __future__ import annotations

from .plugin import PLUGIN, ShellPlugin
from .ring import _Ring
from .session import (
    JOB_COMPLETED,
    JOB_KILLED,
    JOB_RUNNING,
    JOB_TIMED_OUT,
    BashSession,
    OutputSink,
    _DRAIN_GRACE,
    _Job,
    _NOTIFY_WINDOW,
    _SIGKILL,
    _is_wsl_path,
    _pump,
    _start_order,
    _terminate,
    find_bash,
)
from .tool import (
    _BASH_TAG,
    _early_output_sink,
    _tool_output_sink,
    bash_output_tool,
    bash_tool,
    bash_tool_for,
    kill_shell_tool,
)

__all__ = [
    "PLUGIN",
    "ShellPlugin",
    "BashSession",
    "OutputSink",
    "find_bash",
    "bash_tool",
    "bash_tool_for",
    "bash_output_tool",
    "kill_shell_tool",
    "JOB_RUNNING",
    "JOB_COMPLETED",
    "JOB_KILLED",
    "JOB_TIMED_OUT",
    "_Ring",
    "_Job",
    "_SIGKILL",
    "_terminate",
    "_start_order",
    "_pump",
    "_BASH_TAG",
    "_is_wsl_path",
    "_NOTIFY_WINDOW",
    "_DRAIN_GRACE",
    "_tool_output_sink",
    "_early_output_sink",
]
