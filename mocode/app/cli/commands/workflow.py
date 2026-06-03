"""/workflow command — manage and run YAML workflow DAGs."""

from __future__ import annotations

from ...workflow import Workflow, WorkflowRegistry
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

        # Reset state for re-run
        wf.status = "idle"
        wf.results.clear()

        ctx.display.workflow_start(wf)

        runner = DAGRunner(
            wf,
            on_progress=ctx.display.set_spinner_detail,
            on_wave_start=ctx.display.workflow_wave_start,
            on_node_done=ctx.display.workflow_node_done,
            on_node_skip=ctx.display.workflow_node_skip,
            on_loop_iter=ctx.display.workflow_loop_iter,
            on_condition=ctx.display.workflow_condition,
        )
        try:
            async with ctx.display.spinner(f"Workflow: {wf.name}"):
                results = await runner.run(args=user_args)
        except Exception as e:
            ctx.display.error(f"Workflow failed: {e}")
            return CommandResult.CONTINUE

        ctx.display.workflow_summary(wf)
        return CommandResult.CONTINUE

    async def _create(self, ctx: CommandContext, description: str) -> CommandResult:
        if not description.strip():
            description = await text_input(
                "Describe the workflow you want to create:"
            )
            if not description:
                return CommandResult.CONTINUE

        prompt = (
            f"Create a MoCode workflow YAML file based on this description: {description}\n\n"
            "The workflow YAML format:\n"
            "```yaml\n"
            "name: my-workflow\n"
            "description: description text\n"
            "max_iterations: 100          # optional, default 100\n"
            "\n"
            "nodes:\n"
            "  # Task node (default type)\n"
            "  - id: overview\n"
            "    task: Analyze project structure in {args.path}\n"
            "\n"
            "  # Parallel nodes that depend on overview\n"
            "  - id: check_security\n"
            "    task: Review security issues in {nodes.overview.output}\n"
            "    depends: [overview]\n"
            "\n"
            "  - id: check_style\n"
            "    task: Review style issues in {nodes.overview.output}\n"
            "    depends: [overview]\n"
            "\n"
            "  # Node that waits for both parallel nodes\n"
            "  - id: summary\n"
            "    task: |\n"
            "      Security: {nodes.check_security.output}\n"
            "      Style: {nodes.check_style.output}\n"
            "    depends: [check_security, check_style]\n"
            "\n"
            "  # Router node (conditional branching)\n"
            "  - id: decide\n"
            "    type: router\n"
            "    depends: [summary]\n"
            "    routes:\n"
            "      - match: \"critical\"     # regex match on dependency outputs\n"
            "        to: [fix]             # activate fix node\n"
            "      - match: null            # fallback (no match pattern)\n"
            "        to: [done]\n"
            "\n"
            "  - id: fix\n"
            "    task: Fix critical issues from {nodes.summary.output}\n"
            "    depends: [decide]\n"
            "\n"
            "  # Router with loop (back-edge)\n"
            "  - id: verify_router\n"
            "    type: router\n"
            "    depends: [fix]\n"
            "    routes:\n"
            "      - match: \"FAIL\"\n"
            "        to: [fix]             # back-edge → loop\n"
            "        max: 3                # max 3 retries\n"
            "      - match: null\n"
            "        to: [done]\n"
            "\n"
            "  - id: done\n"
            "    task: Generate final report\n"
            "    depends: [decide, verify_router]  # convergence point\n"
            "```\n\n"
            "Template variables: {args.key}, {nodes.id.output}, {nodes.id.exit_code}, "
            "{nodes.id.error}, {nodes.id.duration}, {previous}, {env.VAR}\n\n"
            "Save the file to .mocode/workflows/<name>.yaml in the current project directory. "
            "Create the .mocode/workflows/ directory if it does not exist."
        )
        return CommandResult.text(prompt)
