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
        "Fix the following issue: {args}",
        "Fix a bug or issue",
    ),
    "/review": (
        "Review the following code or file for issues and improvements: {args}",
        "Review code for issues",
    ),
    "/explain": (
        "Explain the following in detail: {args}",
        "Explain something in detail",
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
