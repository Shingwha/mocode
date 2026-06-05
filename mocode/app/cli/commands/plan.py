"""/plan commands — create and execute implementation plans."""

from __future__ import annotations

from . import CommandContext, CommandResult


# ── Shared handler ──────────────────────────────────────

class _PlanHandler:
    """Shared logic for all /plan:* commands."""

    def __init__(self, app):
        self._app = app

    def start(self, ctx: CommandContext) -> CommandResult:
        path = self._app.active_plan_path
        if not path:
            ctx.display.warn("No active plan. Use /plan <description> to create one.")
            return CommandResult.CONTINUE

        prompt = (
            f"[Plan Mode — Read the plan file and execute step by step]\n\n"
            f"Read the plan file at: {path}\n"
            f"Then execute each step, using edit/write/bash as needed.\n"
            f"After each step, verify the result before moving on."
        )
        if ctx.args:
            prompt += f"\n\n---\n\nUser context: {ctx.args}"
        return CommandResult.text(prompt)

    def start_clean(self, ctx: CommandContext) -> CommandResult:
        path = self._app.active_plan_path
        if not path:
            ctx.display.warn("No active plan. Use /plan <description> to create one.")
            return CommandResult.CONTINUE

        self._app.clear_conversation()
        return self.start(ctx)

    def status(self, ctx: CommandContext) -> CommandResult:
        path = self._app.active_plan_path
        if path:
            ctx.display.info(f"Active plan: {path}")
        else:
            ctx.display.info("No active plan.")
        return CommandResult.CONTINUE

    def clear(self, ctx: CommandContext) -> CommandResult:
        self._app.active_plan_path = None
        ctx.display.info("Active plan cleared.")
        return CommandResult.CONTINUE


# ── Commands (each registered independently) ───────────

class PlanStartCommand:
    name = "/plan:start"
    description = "Execute the active plan (keep context)"
    aliases = ()

    def __init__(self, handler: _PlanHandler):
        self._handler = handler

    async def run(self, ctx: CommandContext) -> CommandResult:
        return self._handler.start(ctx)


class PlanStartCleanCommand:
    name = "/plan:start-clean"
    description = "Execute the active plan (clear context first)"
    aliases = ()

    def __init__(self, handler: _PlanHandler):
        self._handler = handler

    async def run(self, ctx: CommandContext) -> CommandResult:
        return self._handler.start_clean(ctx)


class PlanStatusCommand:
    name = "/plan:status"
    description = "Show the active plan path"
    aliases = ()

    def __init__(self, handler: _PlanHandler):
        self._handler = handler

    async def run(self, ctx: CommandContext) -> CommandResult:
        return self._handler.status(ctx)


class PlanClearCommand:
    name = "/plan:clear"
    description = "Clear the active plan"
    aliases = ()

    def __init__(self, handler: _PlanHandler):
        self._handler = handler

    async def run(self, ctx: CommandContext) -> CommandResult:
        return self._handler.clear(ctx)
