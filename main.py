"""MoCode 0.3 — Multi-channel agent gateway.

Usage:
    uv run main.py
"""

import asyncio
import logging
from pathlib import Path

from mocode.app import Config, FileSessionStore, Gateway
from mocode.app.gateway import register_gateway_tools
from mocode.channels import WeixinChannel
from mocode.core import Agent, Hooks, TOOL_START, TOOL_COMPLETE
from mocode.core.skill import SkillManager
from mocode.core.tool import ToolRegistry
from mocode.prompts.app import build_system_prompt
from mocode.providers.openai import OpenAIProvider
from mocode.tools import (
    BashTool, ReadTool, EditTool, GlobTool, GrepTool,
    CompactManager, CompactTool, SubAgentTool, SkillTool,
    ImageTool,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("tools")

HOME = Path.home() / ".mocode"

config = Config.load()
if not config:
    raise SystemExit("Config not found. Create ~/.mocode/config.json first.")


def _read_memory(name: str) -> str:
    p = HOME / "memory" / name
    return p.read_text(encoding="utf-8").strip() if p.exists() else ""


def _setup_hooks() -> Hooks:
    hooks = Hooks()

    def on_start(d):
        log.info("▶ %s(%s)", d.get("name"), str(d.get("args", ""))[:200])

    def on_complete(d):
        if "error" in d:
            log.warning("✖ %s: %s", d.get("name"), d["error"])
        elif "timeout" in d:
            log.warning("⏱ %s: timeout %ss", d.get("name"), d["timeout"])
        else:
            log.info("✔ %s → %s", d.get("name"), str(d.get("result", ""))[:300])

    return hooks.on(TOOL_START, on_start).on(TOOL_COMPLETE, on_complete)


def create_agent(session_key: str):
    """Per-user agent via standard build flow."""
    pc = config.current

    provider = OpenAIProvider(
        api_key=pc.api_key, model=pc.model,
        base_url=pc.base_url, extra_body=pc.extra_body,
    )

    tools = ToolRegistry()
    for t in [ReadTool, EditTool, GlobTool, GrepTool, BashTool()]:
        tools.register(t)
    register_gateway_tools(tools)

    ic = config.image
    if ic.enabled:
        tools.register(ImageTool(
            base_url=ic.base_url, api_key=ic.api_key, model=ic.model,
        ))

    skill_mgr = SkillManager([HOME / "skills"])
    tools.register(SkillTool(skill_mgr))

    prompt = build_system_prompt(
        tools=tools, skill_manager=skill_mgr, cwd=str(Path.cwd()),
        soul=_read_memory("SOUL.md"), user=_read_memory("USER.md"),
        memory=_read_memory("MEMORY.md"),
        home=str(HOME), config_path=str(HOME / "config.json"),
        skills_dir=str(HOME / "skills"), sessions_dir=str(HOME / "sessions"),
    )

    hooks = _setup_hooks()

    # Standard build flow: Agent() -> .provider() -> .prompt() -> .tools() -> .hooks() -> .build()
    agent = (
        Agent()
        .provider(provider)
        .prompt(prompt)
        .tools(tools)
        .hooks(hooks)
        .build()
    )

    # Post-build tools (need agent reference)
    compact = CompactManager(provider, hooks=hooks)
    compact.register(agent)
    tools.register(CompactTool(lambda: agent.messages, compact))
    tools.register(SubAgentTool(lambda: agent.provider, tools, tool_timeout=agent.config.tool_timeout))

    return agent


async def main():
    channel = WeixinChannel(
        state_dir=HOME / "weixin",
        media_dir=HOME / "media" / "weixin",
        provider_config=config.current,
    )
    if not await channel.login():
        return

    gateway = Gateway(
        channels={"weixin": channel},
        agent_factory=create_agent,
        session_store=FileSessionStore(HOME / "sessions"),
    )
    try:
        await gateway.run()
    except KeyboardInterrupt:
        await gateway.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
