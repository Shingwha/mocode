"""/workflow command — manage and run YAML workflow DAGs."""

from __future__ import annotations

from ...workflow import Workflow, WorkflowRegistry
from ...workflow._template import WORKFLOW_CREATE_PROMPT
from ...workflow.runner import DAGRunner
from ..prompts import Choice, select, text_input
from . import CommandContext, CommandResult


class WorkflowCommand:
    name = "/workflow"
    description = "Manage and run YAML workflows"
    aliases = ("workflow",)

    async def run(self, ctx: CommandContext) -> CommandResult:
        parts = ctx.args.split(None, 1) if ctx.args else []
        sub = parts[0] if parts else ""
        sub_args = parts[1] if len(parts) > 1 else ""

        if sub in ("list", "ls"):
            return await self._list(ctx)
        if sub == "run":
            return await self._run(ctx, sub_args)
        if sub == "show":
            return await self._show(ctx, sub_args)
        if sub == "create":
            return await self._create(ctx, sub_args)
        return await self._menu(ctx)

    # ── Interactive menu ──────────────────────────────────────

    async def _menu(self, ctx: CommandContext) -> CommandResult:
        registry = ctx.app.workflow_registry
        workflows = registry.list()
        if not workflows:
            ctx.display.info("No workflows found. Use /workflow create <description>")
            return CommandResult.CONTINUE

        choices = [
            Choice(title=wf.name, value=wf.name, description=wf.description[:60])
            for wf in workflows
        ]
        choices.append(Choice(title="Back", value="__back__"))
        chosen = await select("Workflows:", choices)
        if chosen is None or chosen == "__back__":
            return CommandResult.CONTINUE

        action = await select(
            f"Workflow '{chosen}':",
            [
                Choice(title="Run", value="run"),
                Choice(title="Show details", value="show"),
                Choice(title="Back", value="back"),
            ],
        )
        if action is None or action == "back":
            return CommandResult.CONTINUE
        if action == "run":
            return await self._run(ctx, chosen)
        return await self._show(ctx, chosen)

    # ── Subcommands ───────────────────────────────────────────

    async def _list(self, ctx: CommandContext) -> CommandResult:
        registry = ctx.app.workflow_registry
        workflows = registry.list()
        if not workflows:
            ctx.display.info("No workflows found.")
            return CommandResult.CONTINUE

        text = ctx.display.workflow_list(workflows)
        ctx.display.info("Workflows:\n" + text)
        return CommandResult.CONTINUE

    async def _show(self, ctx: CommandContext, name: str) -> CommandResult:
        name = name.strip()
        registry = ctx.app.workflow_registry
        wf = registry.get(name)
        if wf is None:
            ctx.display.warn(f"Workflow '{name}' not found.")
            return CommandResult.CONTINUE

        text = ctx.display.workflow_show(wf)
        ctx.display.info(text)
        return CommandResult.CONTINUE

    async def _run(self, ctx: CommandContext, args_str: str) -> CommandResult:
        parts = args_str.split()
        if not parts:
            ctx.display.warn("Usage: /workflow run <name> [key=value...]")
            return CommandResult.CONTINUE

        name = parts[0]
        user_args: dict[str, str] = {}
        for p in parts[1:]:
            if "=" in p:
                k, v = p.split("=", 1)
                user_args[k.strip()] = v.strip()

        registry = ctx.app.workflow_registry
        wf = registry.get(name)
        if wf is None:
            ctx.display.warn(f"Workflow '{name}' not found.")
            return CommandResult.CONTINUE

        ctx.display.workflow_start(wf)

        runner = DAGRunner(
            wf,
            on_event=ctx.display.handle_event,
        )
        try:
            async with ctx.display.spinner(f"Workflow: {wf.name}"):
                results = await runner.run(args=user_args)
        except Exception as e:
            ctx.display.error(f"Workflow failed: {e}")
            return CommandResult.CONTINUE

        ctx.display.workflow_summary(wf, results)
        return CommandResult.CONTINUE

    async def _create(self, ctx: CommandContext, description: str) -> CommandResult:
        if not description.strip():
            description = await text_input(
                "Describe the workflow you want to create:"
            )
            if not description:
                return CommandResult.CONTINUE

        prompt = WORKFLOW_CREATE_PROMPT.format(description=description.strip())
        return CommandResult.text(prompt)
