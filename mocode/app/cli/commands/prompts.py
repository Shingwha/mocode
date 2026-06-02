"""Prompt commands — data-driven commands that inject prompts into agent chat."""

from __future__ import annotations

from typing import TYPE_CHECKING

from . import CommandContext, CommandResult

if TYPE_CHECKING:
    from ..commands import CommandRegistry


# name -> (template, description)
# Templates: {args} is replaced with user input. If no {args} but user
# provides input, it is appended as "The user has the following additional requirements: ...".
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
    "/plan": (
        "Create an implementation plan for: {args}\n\n"
        "=== READ-ONLY PLANNING MODE ===\n"
        "Do NOT create, modify, or delete any files. Do NOT run state-changing commands.\n"
        "Your role is to explore the codebase and design an implementation plan.\n\n"
        "## Process\n\n"
        "1. **Understand** — restate the goal in your own words.\n"
        "2. **Explore**:\n"
        "   - Use `glob` to find relevant files and understand project structure.\n"
        "   - Use `grep` to locate patterns, conventions, and similar features.\n"
        "   - Use `read` to examine key files in detail.\n"
        "   - Use `bash` for read-only commands only (ls, git log, git diff).\n"
        "   - Understand the architecture and identify reference implementations.\n"
        "3. **Design** — consider trade-offs. Follow existing patterns where appropriate.\n"
        "4. **Plan**:\n"
        "   - Break into concrete, ordered steps.\n"
        "   - Each step: what to do, which files, how to verify.\n"
        "   - Identify dependencies and sequencing.\n\n"
        "## Output Format\n\n"
        "### Goal\n"
        "1-2 sentences.\n\n"
        "### Affected Files\n"
        "Files to create or modify.\n\n"
        "### Steps\n"
        "Numbered steps, each with: what, which files, how to verify.\n\n"
        "### Risks / Open Questions\n"
        "Potential issues or decisions needed.\n\n"
        "### Critical Files\n"
        "3-5 files most critical for implementation.",
        "Create an implementation plan",
    ),
}


class PromptCommand:
    """A command that injects a prompt template into the agent chat."""

    def __init__(self, name: str, template: str, description: str) -> None:
        self.name = name
        self.template = template
        self.description = description
        self.aliases: tuple[str, ...] = ()

    async def run(self, ctx: CommandContext) -> CommandResult:
        if "{args}" in self.template:
            prompt = self.template.replace("{args}", ctx.args)
        elif ctx.args:
            prompt = f"{self.template}\n\nThe user has the following additional requirements: {ctx.args}"
        else:
            prompt = self.template
        return CommandResult.text(prompt)


def register_prompt_commands(registry: CommandRegistry) -> None:
    """Register all prompt commands from the PROMPTS dict."""
    for name, (template, desc) in PROMPTS.items():
        registry.register(PromptCommand(name, template, desc))
