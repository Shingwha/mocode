"""The shell tools — the bash tool and its background companions.

``bash_tool()`` builds a self-contained bash tool (its session created
inside, captured by the closure); ``bash_tool_for()`` wraps an existing
session — how the plugin registers the trio so all three share one
session. The sink factories route a run's live output onto the event
stream: the foreground call's lines under its own call id, a background
job's early lines into the open block of its start call.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from .....core.events import ToolOutput
from .....core.tool import Tool, ToolPolicy, ToolResult
from .session import BashSession, OutputSink

if TYPE_CHECKING:
    from .....core.hook import ToolCallContext

_BASH_TAG = frozenset({"shell"})


def _tool_output_sink(ctx: ToolCallContext) -> OutputSink:
    """Publish each line to the run's event stream under this call's id."""

    async def sink(text: str, stream: str) -> None:
        await ctx.emit(ToolOutput(call_id=ctx.tool_call_id, text=text, stream=stream))

    return sink


def _early_output_sink(session: BashSession, ctx: ToolCallContext) -> OutputSink:
    """A background job's early output, into the open block of its start call.

    Bound to the turn that started the job: once that turn has ended, its
    blocks have committed, and the job's rings — not the event stream — are
    the record. Only the plugin path can know the turn (a bare session has no
    host to ask), so there is nothing to report otherwise.
    """
    agent = getattr(session._host, "agent", None)
    turn = agent.turn if agent is not None else None

    async def sink(text: str, stream: str) -> None:
        if turn is not None and not turn.done:
            await ctx.emit(
                ToolOutput(call_id=ctx.tool_call_id, text=text, stream=stream)
            )

    return sink


def bash_tool(cwd: Path, default_timeout: int = 240, *, host=None) -> Tool:
    """Run shell commands in a persistent bash session — one tool per
    conversation, its session created here and captured by the closure."""
    return bash_tool_for(BashSession(cwd, host=host), default_timeout)


def bash_tool_for(session: BashSession, default_timeout: int = 240) -> Tool:
    """The bash tool around an existing session — how the plugin registers it,
    so the trio below shares one session."""
    # Only for runs nobody dispatches (bare tool.run): when the dispatcher
    # is involved it resolves the deadline — the model's ``timeout``
    # argument via the policy below, else the config — and hands it back
    # on the context, which drives this tool's foreground wait.
    fallback_timeout = default_timeout

    async def execute(args: dict, ctx=None) -> "str | ToolResult":
        if args.get("restart"):
            session.restart()
            return ToolResult("Bash session restarted")

        timeout = fallback_timeout
        if ctx is not None and ctx.tool_timeout is not None:
            timeout = ctx.tool_timeout
        if args.get("run_in_background"):
            # The policy deadline bounds the *start* call only; the job runs
            # on its own deadline — the explicit argument, capped by config.
            return await session.start_background(
                args["command"],
                timeout=args.get("timeout"),
                on_output=(
                    _early_output_sink(session, ctx) if ctx is not None else None
                ),
            )
        return await session.execute(
            args["command"],
            timeout=timeout,
            on_output=_tool_output_sink(ctx) if ctx is not None else None,
        )

    tool = Tool(
        name="bash",
        description=(
            "Run a shell command in a persistent bash session (Unix-style, e.g. ls, grep, find). "
            "Working directory and environment variables persist across commands. "
            "Use 'restart' to reset session state (cwd, env vars), or "
            "run_in_background for a long-running command: it returns a shell_id "
            "immediately, and bash_output(shell_id) reads what it printed."
        ),
        schema={
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The bash command to execute (Unix-style syntax)",
                },
                "restart": {
                    "type": "boolean",
                    "description": "Reset session state (working directory and environment variables)",
                },
                "timeout": {
                    "type": "number",
                    "description": "Max execution time in seconds (default: the host's tool_timeout policy)",
                },
                "run_in_background": {
                    "type": "boolean",
                    "description": (
                        "Start the command and return a shell_id immediately instead of "
                        "waiting for it; read its output with bash_output(shell_id)"
                    ),
                    "default": False,
                },
            },
            "required": ["command"],
        },
        func=execute,
        tags=_BASH_TAG,
        summary_key="command",
        result_key="exit_code",
        with_context=True,
        # The model-facing timeout argument is policy, not bookkeeping:
        # the dispatcher enforces it around the whole call; absent means
        # None, i.e. fall through to the config default.
        policy=lambda args: ToolPolicy(timeout=args.get("timeout")),
    )
    # The conversation's session, for whoever must reach it from outside the
    # tool (the plugin's close() kills background jobs through it). The tool
    # owns the session and the registry owns the tool — the per-conversation
    # lookup goes through the registry, never through plugin state, which one
    # instance shares across every conversation in the process.
    tool.session = session  # type: ignore[attr-defined]
    return tool


def bash_output_tool(session: BashSession) -> Tool:
    """Read a background shell's output incrementally — reading is consuming."""

    async def execute(args: dict) -> ToolResult:
        return await session.read_output(
            args["shell_id"],
            filter=args.get("filter"),
            wait=bool(args.get("wait")),
            timeout=args.get("timeout"),
        )

    return Tool(
        name="bash_output",
        description=(
            "Read new output from a background shell started with "
            "bash(run_in_background=true). Each call returns only the lines that "
            "arrived since the last read. A filter regex returns (and consumes) "
            "just the matching lines, keeping the rest buffered; wait=true blocks "
            "until the job finishes. The status says whether it is still running."
        ),
        schema={
            "type": "object",
            "properties": {
                "shell_id": {
                    "type": "string",
                    "description": "The background shell to read, e.g. shell_1",
                },
                "filter": {
                    "type": "string",
                    "description": (
                        "A regex: only matching lines are returned and consumed; "
                        "non-matching lines stay buffered"
                    ),
                },
                "wait": {
                    "type": "boolean",
                    "description": "Block until the job completes (or timeout) before reading",
                    "default": False,
                },
                "timeout": {
                    "type": "number",
                    "description": "Seconds to wait when wait=true (default: unbounded)",
                },
            },
            "required": ["shell_id"],
        },
        func=execute,
        tags=_BASH_TAG,
        summary_key="shell_id",
        result_key="status",
    )


def kill_shell_tool(session: BashSession) -> Tool:
    """Stop a background shell — the process group where the platform has one."""

    async def execute(args: dict) -> ToolResult:
        return await session.kill(args["shell_id"])

    return Tool(
        name="kill_shell",
        description="Stop a background shell started with bash(run_in_background=true).",
        schema={
            "type": "object",
            "properties": {
                "shell_id": {
                    "type": "string",
                    "description": "The background shell to stop, e.g. shell_1",
                },
            },
            "required": ["shell_id"],
        },
        func=execute,
        tags=_BASH_TAG,
        summary_key="shell_id",
        result_key="status",
    )
