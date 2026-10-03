"""The codemode plugin — one `codemode` tool, registered per conversation.

The plugin itself is stateless: ``build()`` hands the tool a context
reference, and everything the tool needs at call time — the dispatcher, the
plugin's config, its session-persisted state slot — is read off that context,
which has grown into a HostContext by then. There is no ``prepare`` and no
``close``: nothing is acquired.
"""

from __future__ import annotations

import asyncio
import json
import math
import re
import time
from typing import TYPE_CHECKING

from .....core.events import Notice
from .....core.tool import Tool, ToolPolicy, ToolResult
from ...base import Plugin
from .description import DESCRIPTION
from .env import build_env
from .output import Output, build_result
from .runtime import _ScriptExit, run_script
from .store import (
    DEFAULT_STORE_MAX_TOTAL_CHARS,
    DEFAULT_STORE_MAX_VALUE_CHARS,
    Store,
)

if TYPE_CHECKING:
    from .....core.hook import ToolCallContext
    from ...context import BuildContext, HostContext

__all__ = ["CodemodePlugin", "PLUGIN", "codemode_tool"]


class CodemodePlugin(Plugin):
    """Register the ``codemode`` tool — a Python-script orchestrator."""

    name = "codemode"
    description = "Run a Python script that calls other tools"

    def build(self, ctx: "BuildContext") -> None:
        """Register the tool. Cheap and synchronous — no prompt section, the
        DESCRIPTION carries the whole contract."""
        host = ctx  # the same object grows into a HostContext at assembly
        ctx.tools.register(codemode_tool(host))


PLUGIN = CodemodePlugin()


#: Matches a first-line `# @options: {...}` comment.
_OPTIONS_RE = re.compile(r"^\s*#\s*@options:\s*(\{.*\})\s*$")


def effective_options(script: str, options: dict | None) -> dict:
    """The call's options: the `@options` comment line is the base, explicit
    ``options`` arguments win. An unparseable comment is ignored."""
    merged: dict = {}
    lines = script.splitlines()
    if lines:
        match = _OPTIONS_RE.match(lines[0])
        if match:
            try:
                parsed = json.loads(match.group(1))
            except ValueError:
                parsed = None
            if isinstance(parsed, dict):
                merged.update(parsed)
    merged.update(options or {})
    return merged


def _deadline_seconds(options: dict, config: dict) -> float | None:
    """The whole-script deadline in seconds: explicit ``timeout_ms`` (ceil'd,
    at least one), then ``plugins.codemode.timeout_s``; ``None`` when neither
    is set, leaving the call to the agent's tool timeout."""
    ms = options.get("timeout_ms")
    if ms:
        return max(1, math.ceil(ms / 1000))
    seconds = config.get("timeout_s", 0)
    if seconds:
        return seconds
    return None


def codemode_tool(host: "HostContext") -> Tool:
    """The ``codemode`` tool — closures over the host, stateless plugin."""

    concurrency_warned = False  # one warning per conversation, not per call

    def policy(args: dict) -> ToolPolicy:
        """Whole-script deadline handed to the dispatcher: explicit options,
        then the `@options` comment, then ``plugins.codemode.timeout_s``;
        0/absent falls back to the agent's configured tool timeout."""
        options = effective_options(args.get("script") or "", args.get("options"))
        return ToolPolicy(
            timeout=_deadline_seconds(options, host.plugin_config("codemode"))
        )

    async def run(args: dict, call_ctx: "ToolCallContext") -> ToolResult:
        nonlocal concurrency_warned
        script = args["script"]
        options = effective_options(script, args.get("options"))
        config = host.plugin_config("codemode")
        output = Output()
        store = Store(
            host.plugin_state("codemode"),
            max_value_chars=config.get(
                "store_max_value_chars", DEFAULT_STORE_MAX_VALUE_CHARS
            ),
            max_total_chars=config.get(
                "store_max_total_chars", DEFAULT_STORE_MAX_TOTAL_CHARS
            ),
        )
        # D5: an optional fan-out cap. Absent means unlimited; anything that
        # is not a positive integer is reported once and ignored.
        raw_limit = config.get("max_concurrency")
        max_concurrency = None
        if raw_limit is not None:
            if (
                isinstance(raw_limit, bool)
                or not isinstance(raw_limit, int)
                or raw_limit <= 0
            ):
                if not concurrency_warned:
                    concurrency_warned = True
                    await host.emit(
                        Notice(
                            message=(
                                "plugins.codemode.max_concurrency must be a "
                                f"positive integer, got {raw_limit!r} "
                                "— running without a concurrency cap."
                            ),
                            level="warn",
                        )
                    )
            else:
                max_concurrency = raw_limit
        env, toolbox = build_env(
            host.tools,
            host.agent.dispatcher,
            call_ctx.tool_call_id,
            output,
            store,
            max_concurrency=max_concurrency,
        )
        deadline = _deadline_seconds(options, config)
        started = time.monotonic()
        ok = False
        error: BaseException | None = None
        value = None
        timed_out = False
        if deadline is not None:
            # D6: an explicit deadline is enforced here, inside the plugin,
            # so when it fires the result keeps what the script already
            # emitted; the dispatcher's timeout path (which discards partial
            # output by cancelling the call) never gets the chance.
            script_task = asyncio.create_task(run_script(script, env))
            try:
                value = await asyncio.wait_for(script_task, timeout=deadline)
                ok = True
            except _ScriptExit:
                ok = True
            except asyncio.CancelledError:
                # A turn cancelled mid-script unwinds untouched — the
                # dispatcher records the outcome. wait_for already cancelled
                # the script task.
                raise
            except TimeoutError:
                if script_task.cancelled():
                    # Our own deadline fired — reported as a normal failure
                    # below, with the partial output kept.
                    timed_out = True
                else:
                    # The script itself raised TimeoutError — its own error,
                    # not the deadline.
                    error = script_task.exception() or TimeoutError("timed out")
            except Exception as e:  # the script's failure is the tool's result
                error = e
        else:
            try:
                value = await run_script(script, env)
                ok = True
            except _ScriptExit:
                ok = True
            except asyncio.CancelledError:
                # A turn cancelled or a timed-out call must unwind untouched —
                # the dispatcher records the outcome.
                raise
            except Exception as e:  # the script's failure is the tool's result
                error = e
        if ok and value is not None:
            output.text(value)
        if timed_out:
            # A finished-looking failure: partial output plus the marker,
            # store writes from the unfinished script discarded.
            output.text(f"Script timed out after {deadline}s.")
        if ok:
            # Store limits are validated before anything is applied — a
            # limit breach turns the run into a failure with nothing staged.
            try:
                store.commit()
            except Exception as e:
                ok = False
                error = e
        ms = int((time.monotonic() - started) * 1000)
        return build_result(
            ok=ok,
            ms=ms,
            output=output,
            error=error,
            tool_calls=toolbox.calls,
            max_chars=options.get("max_output_chars")
            or config.get("max_output_chars", 12000),
            script=script,
            timed_out=timed_out,
        )

    return Tool(
        name="codemode",
        description=DESCRIPTION,
        schema={
            "type": "object",
            "properties": {
                "script": {"type": "string", "description": "Python source to run"},
                "options": {
                    "type": "object",
                    "properties": {
                        "timeout_ms": {"type": "integer"},
                        "max_output_chars": {"type": "integer"},
                    },
                },
            },
            "required": ["script"],
        },
        func=run,
        with_context=True,
        availability="model",
        tags=frozenset({"codemode"}),
        policy=policy,
    )
