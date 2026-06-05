"""/plan commands — create and execute implementation plans."""

from __future__ import annotations

from pathlib import Path

from . import Command, CommandContext, CommandResult, Subcommand


# ── Standalone handler functions ──────────────────────────


async def _start(ctx: CommandContext, args: str) -> CommandResult:
    path = ctx.app.active_plan_path
    if not path:
        ctx.display.warn("No active plan. Use /plan <description> to create one.")
        return CommandResult.CONTINUE

    prompt = (
        f"[Plan Mode — Read the plan file and execute step by step]\n\n"
        f"Read the plan file at: {path}\n"
        f"Then execute each step, using edit/write/bash as needed.\n"
        f"After each step, verify the result before moving on."
    )
    if args:
        prompt += f"\n\n---\n\nUser context: {args}"
    return CommandResult.text(prompt)


async def _start_clean(ctx: CommandContext, args: str) -> CommandResult:
    path = ctx.app.active_plan_path
    if not path:
        ctx.display.warn("No active plan. Use /plan <description> to create one.")
        return CommandResult.CONTINUE

    ctx.app.clear_conversation()
    return await _start(ctx, args)


async def _status(ctx: CommandContext, args: str) -> CommandResult:
    path = ctx.app.active_plan_path
    if path:
        ctx.display.info(f"Active plan: {path}")
    else:
        ctx.display.info("No active plan.")
    return CommandResult.CONTINUE


async def _clear(ctx: CommandContext, args: str) -> CommandResult:
    ctx.app.active_plan_path = None
    ctx.display.info("Active plan cleared.")
    return CommandResult.CONTINUE


async def _copy(ctx: CommandContext, args: str) -> CommandResult:
    path = ctx.app.active_plan_path
    if not path:
        ctx.display.warn("No active plan. Use /plan <description> to create one.")
        return CommandResult.CONTINUE

    try:
        content = Path(path).expanduser().read_text(encoding="utf-8")
    except Exception as e:
        ctx.display.error(f"Failed to read plan: {e}")
        return CommandResult.CONTINUE

    try:
        import pyperclip

        pyperclip.copy(content)
        preview = content[:60].replace("\n", " ").strip()
        suffix = "…" if len(content) > 60 else ""
        ctx.display.info(f"Copied: {preview}{suffix}")
    except Exception as e:
        ctx.display.error(f"Clipboard error: {e}")

    return CommandResult.CONTINUE


# ── Default handler (free-text plan creation) ────────────


async def _default_plan(ctx: CommandContext, args: str) -> CommandResult:
    """Handle ``/plan <description>`` — prompt agent to write and register a plan."""
    prompt = (
        f"The user wants to create an implementation plan for:\n\n"
        f"{args}\n\n"
        f"Write the plan to a file under ~/.mocode/plans/ (create the directory if needed),\n"
        f"then call the plan tool with action='done' and the file path.\n"
        f"After that, tell the user to run /plan:start to execute."
    )
    return CommandResult.text(prompt)


# ── Command definition ────────────────────────────────────


command = Command(
    name="/plan",
    description="Create and execute implementation plans",
    subcommands=(
        Subcommand("start", "Execute plan (keep context)", _start),
        Subcommand("start-clean", "Execute plan (clear context)", _start_clean),
        Subcommand("status", "Show active plan path", _status),
        Subcommand("clear", "Clear the active plan", _clear),
        Subcommand("copy", "Copy plan to clipboard", _copy),
    ),
    default=_default_plan,
)
