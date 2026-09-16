"""Example MoCode plugin — a tool, a slash command, and a prompt section."""

from __future__ import annotations

import subprocess

from mocode.plugins import (
    CONTINUE,
    Command,
    CommandContext,
    CommandResult,
    Plugin,
    Section,
    Tool,
)


def _git(*args: str) -> str:
    try:
        result = subprocess.run(
            ["git", *args], capture_output=True, text=True, timeout=10
        )
    except FileNotFoundError:
        return "error: git is not installed"
    except subprocess.TimeoutExpired:
        return "error: git timed out"
    return (result.stdout or result.stderr).strip() or "(no output)"


class GitStatusTool(Tool):
    """A tool the model can call."""

    def __init__(self) -> None:
        super().__init__(
            name="git_status",
            description=(
                "Show the working tree status of the current git repository "
                "in short format."
            ),
            params={},
            func=self._run,
            tags=frozenset({"git"}),
        )

    def _run(self, args: dict) -> str:
        return _git("status", "--short")


async def _branch(ctx: CommandContext) -> CommandResult:
    """A slash command the user can run."""
    branch = _git("rev-parse", "--abbrev-ref", "HEAD")
    if ctx.display:
        ctx.display.info(f"Branch: {branch}")
    return CONTINUE


def _guidance(_context: dict) -> str:
    """A prompt section the model always sees."""
    return "\n".join(
        [
            "- Call `git_status` instead of `bash` when you only need repo status.",
            "- The user can run `/branch` to show the current branch.",
        ]
    )


class GitStatusPlugin(Plugin):
    name = "git-status"
    description = "A git status tool and a /branch command — a complete example plugin"

    def build(self, ctx) -> None:
        ctx.tools.register(GitStatusTool())
        ctx.register(Command("/branch", "Show the current git branch", handler=_branch))
        ctx.prompt_sections.append(Section("git", _guidance, priority=45))


plugin = GitStatusPlugin()
