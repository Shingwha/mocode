"""The plugin's host contributions — the `mocode/` namespace.

Everything here uses the host API only, so it works in every MoCode frontend: a
terminal, a web backend, an editor. The tool and the prompt section are for the
agent; the command is for whoever dispatches commands.
"""

from __future__ import annotations

import subprocess

from mocode.plugins import CONTINUE, Command, CommandContext, CommandResult, Plugin, Section, Tool


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

    def __init__(self, cwd) -> None:
        super().__init__(
            name="git_status",
            description=(
                "Show the working tree status of the conversation's git repository "
                "in short format."
            ),
            params={},
            func=self._run,
            tags=frozenset({"git"}),
        )
        self._cwd = cwd

    def _run(self, args: dict) -> str:
        # A conversation works in its own project; git has to be asked there.
        try:
            result = subprocess.run(
                ["git", "status", "--short"],
                capture_output=True,
                text=True,
                timeout=10,
                cwd=self._cwd,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            return f"error: {e}"
        return (result.stdout or result.stderr).strip() or "(clean)"


async def _branch(ctx: CommandContext) -> CommandResult:
    """A slash command the user can run — in any frontend."""
    branch = _git("rev-parse", "--abbrev-ref", "HEAD")
    await ctx.conversation.notify(f"Branch: {branch}")
    return CONTINUE


def _guidance(_context: dict) -> str:
    """A prompt section the model always sees."""
    return "\n".join(
        [
            "- Call `git_status` instead of `bash` when you only need repo status.",
            "- The user can run `/branch` to show the current branch.",
            "- `/commit` is a skill: it loads the commit conventions for this repo.",
        ]
    )


class GitStatusPlugin(Plugin):
    name = "git-status"
    description = "A git status tool, a /branch command and a commit skill"

    def build(self, ctx) -> None:
        ctx.tools.register(GitStatusTool(ctx.cwd))
        ctx.register(Command("/branch", "Show the current git branch", handler=_branch))
        ctx.prompt_sections.append(Section("git", _guidance, priority=45))


plugin = GitStatusPlugin()
