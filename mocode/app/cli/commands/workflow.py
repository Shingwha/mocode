"""/workflow command — manage and run YAML workflows."""

from __future__ import annotations

from ...workflow import Workflow, WorkflowRegistry
from ...workflow.runner import WorkflowRunner
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

        lines = []
        for wf in workflows:
            phase_count = wf.total_phases
            step_count = wf.total_steps()
            lines.append(
                f"  {wf.name:<20} {wf.description[:40]:<40} "
                f"{phase_count} phase(s), {step_count} step(s)"
            )
        ctx.display.info("Workflows:\n" + "\n".join(lines))
        return CommandResult.CONTINUE

    async def _show(self, ctx: CommandContext, name: str) -> CommandResult:
        name = name.strip()
        registry = ctx.app.workflow_registry
        wf = registry.get(name)
        if wf is None:
            ctx.display.warn(f"Workflow '{name}' not found.")
            return CommandResult.CONTINUE

        lines = [
            f"Workflow: {wf.name}",
            f"Description: {wf.description}",
            f"Max iterations: {wf.max_iterations}",
            "",
        ]
        for pi, phase in enumerate(wf.phases):
            phase_info = f"  Phase {pi + 1}: {phase.name}"
            phase_extras = []
            if phase.max_iterations:
                phase_extras.append(f"max_iterations={phase.max_iterations}")
            if phase.goto:
                goto_str = ", ".join(
                    f"goto: {g.to}" + (f" (match={g.match!r})" if g.match else "")
                    + (f" (max={g.max})" if g.max else "")
                    for g in phase.goto
                )
                phase_extras.append(goto_str)
            if phase_extras:
                phase_info += " (" + "; ".join(phase_extras) + ")"
            lines.append(phase_info)

            if phase.lanes:
                for lane in phase.lanes:
                    lane_str = f"    Lane: {lane.name}"
                    if lane.id:
                        lane_str += f" [{lane.id}]"
                    lines.append(lane_str)
                    for si, step in enumerate(lane.steps):
                        lines.append(self._step_line(si, step, indent=6))
            else:
                for si, step in enumerate(phase.steps):
                    lines.append(self._step_line(si, step, indent=4))

        ctx.display.info("\n".join(lines))
        return CommandResult.CONTINUE

    def _step_line(self, si: int, step, indent: int) -> str:
        s = f"{' ' * indent}{si + 1}. {step.task[:60]}"
        if step.id:
            s += f" [{step.id}]"
        if step.goto:
            goto_str = ", ".join(
                f"→ {g.to}" + (f" if /{g.match}/" if g.match else "")
                + (f" (max={g.max})" if g.max else "")
                for g in step.goto
            )
            s += f" ({goto_str})"
        return s

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
        wf.phase_index = 0
        wf.step_index = 0
        wf.results.clear()

        ctx.display.workflow_start(wf)

        runner = WorkflowRunner(
            wf,
            on_progress=ctx.display.set_spinner_detail,
            on_step_done=ctx.display.workflow_step_done,
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
            "phases:\n"
            "  - id: phase_id             # optional\n"
            "    name: Phase Name\n"
            "    max_iterations: 5        # optional, overrides workflow default\n"
            "    # Sequential mode:\n"
            "    steps:\n"
            "      - id: step_id          # optional\n"
            "        task: Task description (supports {args.key}, {steps.id.output},\n"
            "              {previous}, {lane.id.output}, {phase.id.output}, {env.VAR})\n"
            "        goto:                # optional\n"
            "          - match: pattern   # optional regex\n"
            "            to: target       # next, end, step_id, phase.xxx, __end__\n"
            "            max: 3           # optional, max hits before skipping\n"
            "          - to: target       # fallback (no match)\n"
            "    # OR parallel mode:\n"
            "    lanes:\n"
            "      - id: lane_id          # optional\n"
            "        name: Lane Name\n"
            "        steps:\n"
            "          - id: step_id\n"
            "            task: Task description\n"
            "            goto: [...]\n"
            "    goto:                    # optional Phase-level\n"
            "      - match: pattern\n"
            "        to: phase.other_phase\n"
            "      - to: target\n"
            "```\n\n"
            "Save the file to .mocode/workflows/<name>.yaml in the current project directory. "
            "Create the .mocode/workflows/ directory if it does not exist."
        )
        return CommandResult.text(prompt)
