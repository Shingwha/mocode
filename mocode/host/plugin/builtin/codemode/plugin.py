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

from .....core.tool import Tool, ToolPolicy, ToolResult
from ...base import Plugin
from .api import (
    DEFAULT_STORE_MAX_TOTAL_CHARS,
    DEFAULT_STORE_MAX_VALUE_CHARS,
    Store,
    build_env,
)
from .description import DESCRIPTION
from .output import Output, build_result
from .runtime import _ScriptExit, run_script

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


def codemode_tool(host: "HostContext") -> Tool:
    """The ``codemode`` tool — closures over the host, stateless plugin."""

    def policy(args: dict) -> ToolPolicy:
        """Whole-script deadline: explicit options, then the `@options`
        comment, then `plugins.codemode.timeout_s`; 0/absent falls back to
        the agent's configured tool timeout."""
        options = effective_options(args.get("script") or "", args.get("options"))
        ms = options.get("timeout_ms")
        if ms:
            return ToolPolicy(timeout=max(1, math.ceil(ms / 1000)))
        seconds = host.plugin_config("codemode").get("timeout_s", 0)
        if seconds:
            return ToolPolicy(timeout=seconds)
        return ToolPolicy(timeout=None)

    async def run(args: dict, call_ctx: "ToolCallContext") -> ToolResult:
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
        env, toolbox = build_env(
            host.tools,
            host.agent.dispatcher,
            call_ctx.tool_call_id,
            output,
            store,
        )
        started = time.monotonic()
        ok = False
        error: BaseException | None = None
        value = None
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
