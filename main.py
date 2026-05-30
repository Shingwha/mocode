"""MoCode 0.3 — CLI agent tool.

Usage:
    uv run main.py
"""

import asyncio
import signal
import sys
from pathlib import Path

if sys.platform == "win32":
    import ctypes

    try:
        ctypes.windll.kernel32.SetConsoleMode(
            ctypes.windll.kernel32.GetStdHandle(-11), 7
        )
    except Exception:
        pass

RST, BOLD, DIM, GRAY, RED, GREEN, YELLOW, CYAN, MAGENTA = (
    "\033[0m", "\033[1m", "\033[2m", "\033[90m",
    "\033[91m", "\033[92m", "\033[93m", "\033[96m", "\033[95m",
)


def _s(text, *codes):
    return f"{''.join(codes)}{text}{RST}"


_TOOL_KEY = {
    "read": "path", "write": "path", "append": "path", "edit": "path",
    "bash": "command", "glob": "pat", "grep": "pat",
    "fetch": "url", "sub_agent": "task", "skill": "name",
    "goal": "action", "image": "prompt",
}


def _tool_summary(name, args):
    key = _TOOL_KEY.get(name)
    if not key:
        return ""
    val = str(args.get(key, ""))
    return val[:60] + ("..." if len(val) > 60 else "")


import logging

from mocode.app import Config
from mocode.core import Agent, AgentHook, AgentHookContext
from mocode.core.skill import SkillManager
from mocode.core.tool import ToolRegistry
from mocode.hooks import CompactHook, GoalHook
from mocode.prompts.app import build_system_prompt
from mocode.providers.openai import OpenAIProvider
from mocode.tools import (
    AppendTool,
    BashTool,
    CompactTool,
    EditTool,
    FetchTool,
    GlobTool,
    GoalTool,
    GrepTool,
    ImageTool,
    ReadTool,
    SkillTool,
    SubAgentTool,
    WriteTool,
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


def _read_memory(name):
    p = HOME / "memory" / name
    return p.read_text(encoding="utf-8").strip() if p.exists() else ""


class Display:
    def tool_start(self, name, summary):
        print(f"{_s('→', DIM)} {_s(name, CYAN)}{_s(f'({summary})', DIM)}")

    def tool_error(self, msg):
        print(f"{_s(f'× {msg}', RED)}")

    def tool_timeout(self, seconds):
        print(f"{_s(f'× timeout: {seconds}s', RED)}")

    def reasoning(self, content):
        for line in content.splitlines():
            print(f"{_s(f'┊ {line}', DIM)}")

    def text_response(self, content):
        for line in content.strip().splitlines():
            print(f"{_s(f'│ {line}', DIM, MAGENTA)}")

    def usage(self, prompt, completion):
        print(f"{_s(f'✦ ↑{prompt:,} ↓{completion:,}', DIM)}")

    def compact(self, old, new):
        print(f"{_s(f'─ Compacted: {old} → {new} msgs', YELLOW)}")

    def response(self, text):
        print(f"\n{text}\n")

    def prompt(self):
        return input("> ").strip()


class CLIDisplayHook(AgentHook):
    def __init__(self, display):
        self._d = display
        self._prompt = self._completion = 0

    async def on_response(self, ctx):
        if ctx.reasoning_content and not (ctx.response and ctx.response.tool_calls):
            self._d.reasoning(ctx.reasoning_content)
        if ctx.final_content and ctx.response and ctx.response.tool_calls:
            self._d.text_response(ctx.final_content)
        if ctx.usage:
            self._prompt += ctx.usage.prompt_tokens
            self._completion += ctx.usage.completion_tokens

    async def after_iteration(self, ctx):
        if self._prompt or self._completion:
            self._d.usage(self._prompt, self._completion)
            self._prompt = self._completion = 0

    async def on_tool_start(self, ctx):
        self._d.tool_start(ctx.tool_name, _tool_summary(ctx.tool_name, ctx.tool_args))

    async def on_tool_complete(self, ctx):
        if ctx.tool_timeout is not None:
            self._d.tool_timeout(ctx.tool_timeout)
        elif ctx.tool_error:
            self._d.tool_error(ctx.tool_error[:80])

    async def on_compact(self, ctx):
        self._d.compact(ctx.compact_old, ctx.compact_new)


def create_agent(display):
    pc = config.current

    provider = OpenAIProvider(
        api_key=pc.api_key,
        model=pc.model,
        base_url=pc.base_url,
        extra_body=pc.extra_body,
    )

    tools = ToolRegistry()
    for t in [ReadTool(), WriteTool(), AppendTool(), EditTool(), GlobTool(), GrepTool(), BashTool(), FetchTool()]:
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
        .hooks([CLIDisplayHook(display), compact_hook, goal_hook])
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
    display = Display()
    agent = create_agent(display)

    while True:
        try:
            user_input = display.prompt()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not user_input:
            continue
        if user_input.lower() in ("exit", "quit"):
            break
        print()

        def _make_interrupt_handler(task):
            def handler(signum, frame):
                if not task.done():
                    task.cancel()
            return handler

        task = asyncio.ensure_future(agent.chat(user_input))
        original_handler = signal.signal(signal.SIGINT, _make_interrupt_handler(task))
        try:
            result = await task
        except asyncio.CancelledError:
            print(f"\n{_s('Response interrupted.', YELLOW)}\n")
            continue
        finally:
            signal.signal(signal.SIGINT, original_handler)
        if result:
            display.response(result)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
