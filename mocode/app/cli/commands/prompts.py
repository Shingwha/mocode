"""Prompt commands — data-driven commands that inject prompts into agent chat."""

from __future__ import annotations

from . import Command, CommandContext, CommandResult


# name -> (template, description)
# Templates: {args} is replaced with user input. If no {args} but user
# provides input, it is appended as "The user has the following additional requirements: ...".


def _make_prompt_handler(template: str):
    """Create a handler function for a prompt template."""
    async def _handle(ctx: CommandContext) -> CommandResult:
        if "{args}" in template:
            prompt = template.replace("{args}", ctx.args)
        elif ctx.args:
            prompt = f"{template}\n\nThe user has the following additional requirements: {ctx.args}"
        else:
            prompt = template
        return CommandResult.text(prompt)
    return _handle


PROMPTS: dict[str, tuple[str, str]] = {
    "/init": (
        "Analyze this codebase and create an AGENTS.md file.\n\n"
        "What to include:\n"
        "1. Development commands: how to install dependencies, run tests (all and single), build, lint.\n"
        "2. High-level architecture: the big picture that requires reading multiple files to understand.\n"
        "3. Code conventions and patterns specific to this project.\n\n"
        "Rules:\n"
        "- If there is already an AGENTS.md, review and improve it instead of recreating.\n"
        "- Do not repeat yourself.\n"
        "- Do not include obvious instructions or generic development practices.\n"
        "- Do not include information that can be easily discovered by reading a single file.\n"
        "- Do not make up sections unless they are backed by actual files you read.",
        "Analyze project and create/update AGENTS.md",
    ),
    "/fix": (
        "Fix the following issue: {args}\n\n"
        "## Process\n\n"
        "1. **Understand the Issue** — restate the problem in your own words. Identify expected vs actual behavior.\n"
        "2. **Locate the Root Cause**:\n"
        "   - Use `glob` and `grep` to find relevant code.\n"
        "   - Use `read` to examine the affected files in detail.\n"
        "   - Trace the code path from symptom to root cause.\n"
        "3. **Design the Fix**:\n"
        "   - Prefer the minimal change that resolves the issue.\n"
        "   - Follow existing patterns and conventions in the codebase.\n"
        "   - Consider side effects — will this break anything else?\n"
        "4. **Implement and Verify**:\n"
        "   - Make the change.\n"
        "   - Verify: does it fix the issue? Do existing tests still pass?\n"
        "   - If no test covers the bug, consider adding one.\n\n"
        "## Rules\n\n"
        "- Do not refactor unrelated code.\n"
        "- Do not add features beyond what was asked.\n"
        "- If the fix is ambiguous, explain the options before proceeding.",
        "Fix a bug or issue",
    ),
}


def _build_commands() -> list[Command]:
    """Build Command instances from the PROMPTS dict."""
    return [
        Command(
            name=name,
            description=desc,
            handler=_make_prompt_handler(template),
        )
        for name, (template, desc) in PROMPTS.items()
    ]


# Module-level commands list for bulk registration
commands: list[Command] = _build_commands()
