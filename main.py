"""MoCode 0.3 — CLI agent tool.

Usage:
    uv run main.py
"""

import asyncio
import logging
import sys
from pathlib import Path

# Windows ANSI support
if sys.platform == "win32":
    import ctypes

    try:
        ctypes.windll.kernel32.SetConsoleMode(
            ctypes.windll.kernel32.GetStdHandle(-11), 7
        )
    except Exception:
        pass

RST = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
GRAY = "\033[90m"
RED = "\033[91m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
MAGENTA = "\033[95m"

from mocode.app import Config
from mocode.core import Agent, AgentHook, AgentHookContext
from mocode.core.skill import SkillManager
from mocode.core.tool import ToolRegistry
from mocode.hooks import CompactHook, GoalHook
from mocode.prompts.app import build_system_prompt
from mocode.providers.openai import OpenAIProvider
from mocode.tools import (
    BashTool,
    CompactTool,
    EditTool,
    GlobTool,
    GoalTool,
    GrepTool,
    ImageTool,
    ReadTool,
    SkillTool,
    SubAgentTool,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logging.getLogger("httpx").setLevel(logging.WARNING)

HOME = Path.home() / ".mocode"

config = Config.load()
if not config:
    raise SystemExit("Config not found. Create ~/.mocode/config.json first.")


def _read_memory(name: str) -> str:
    p = HOME / "memory" / name
    return p.read_text(encoding="utf-8").strip() if p.exists() else ""


def _tool_summary(name: str, args: dict) -> str:
    """Extract the key param for concise one-line display."""
    if name in ("read", "write", "append", "edit"):
        return args.get("path", "")
    if name == "bash":
        cmd = args.get("command", "")
        return cmd[:60] + ("..." if len(cmd) > 60 else "")
    if name in ("glob", "grep"):
        return args.get("pat", "")
    if name == "fetch":
        return args.get("url", "")
    if name == "sub_agent":
        task = args.get("task", "")
        return task[:60] + ("..." if len(task) > 60 else "")
    if name == "skill":
        return args.get("name", "")
    if name == "goal":
        return args.get("action", "")
    if name == "image":
        prompt = args.get("prompt", "")
        return prompt[:60] + ("..." if len(prompt) > 60 else "")
    return ""


class CLIDisplayHook(AgentHook):
    def __init__(self):
        self._total_prompt = 0
        self._total_completion = 0

    async def on_response(self, ctx: AgentHookContext) -> None:
        if ctx.reasoning_content and not (ctx.response and ctx.response.tool_calls):
            for line in ctx.reasoning_content.splitlines():
                print(f"{DIM}  ┊ {line}{RST}")
        if ctx.final_content and ctx.response and ctx.response.tool_calls:
            text = ctx.final_content.strip()
            if text:
                for line in text.splitlines():
                    print(f"{DIM}  │ {MAGENTA}{line}{RST}")
        if ctx.usage:
            self._total_prompt += ctx.usage.prompt_tokens
            self._total_completion += ctx.usage.completion_tokens

    async def after_iteration(self, ctx: AgentHookContext) -> None:
        if self._total_prompt or self._total_completion:
            print(f"{DIM}  ↑ {self._total_prompt:,}↑ {self._total_completion:,}↓{RST}")
            self._total_prompt = 0
            self._total_completion = 0

    async def on_tool_start(self, ctx: AgentHookContext) -> None:
        summary = _tool_summary(ctx.tool_name, ctx.tool_args)
        print(f"{DIM}  → {CYAN}{ctx.tool_name}{RST}{DIM}({summary}){RST}")

    async def on_tool_complete(self, ctx: AgentHookContext) -> None:
        if ctx.tool_timeout is not None:
            print(f"{RED}  × timeout: {ctx.tool_timeout}s{RST}")
        elif ctx.tool_error:
            print(f"{RED}  × {ctx.tool_error[:80]}{RST}")

    async def on_compact(self, ctx: AgentHookContext) -> None:
        print(
            f"{YELLOW}  ─ Compacted: {ctx.compact_old} → {ctx.compact_new} messages{RST}"
        )


def create_agent():
    pc = config.current

    provider = OpenAIProvider(
        api_key=pc.api_key,
        model=pc.model,
        base_url=pc.base_url,
        extra_body=pc.extra_body,
    )

    tools = ToolRegistry()
    for t in [ReadTool(), EditTool, GlobTool, GrepTool, BashTool()]:
        tools.register(t)

    ic = config.image
    if ic.enabled:
        tools.register(
            ImageTool(
                base_url=ic.base_url,
                api_key=ic.api_key,
                model=ic.model,
            )
        )

    skill_mgr = SkillManager([HOME / "skills"])
    tools.register(SkillTool(skill_mgr))

    prompt = build_system_prompt(
        tools=tools,
        skill_manager=skill_mgr,
        cwd=str(Path.cwd()),
        soul=_read_memory("SOUL.md"),
        user=_read_memory("USER.md"),
        memory=_read_memory("MEMORY.md"),
        home=str(HOME),
        config_path=str(HOME / "config.json"),
        skills_dir=str(HOME / "skills"),
        sessions_dir=str(HOME / "sessions"),
    )

    compact_hook = CompactHook(provider)
    goal_hook = GoalHook()

    agent = (
        Agent()
        .provider(provider)
        .prompt(prompt)
        .tools(tools)
        .hooks([CLIDisplayHook(), compact_hook, goal_hook])
        .build()
    )

    tools.register(CompactTool(provider, lambda: agent.messages))
    tools.register(
        SubAgentTool(
            lambda: agent.provider, tools, tool_timeout=agent.config.tool_timeout
        )
    )
    tools.register(GoalTool(goal_hook))

    return agent


async def main():
    agent = create_agent()
    print(f"{BOLD}MoCode{RST}{DIM}0.3{RST}{GRAY}·{RST}{CYAN}{config.model}{RST}")
    print(f"{DIM}Type 'exit' to quit{RST}\n")

    while True:
        try:
            user_input = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n")
            break

        if not user_input:
            continue
        if user_input.lower() in ("exit", "quit"):
            break
        result = await agent.chat(user_input)
        if result:
            print(f"\n{result}\n")


if __name__ == "__main__":
    asyncio.run(main())
