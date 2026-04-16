"""SubAgent tool — LLM 可将任务委派给子 Agent"""

import asyncio

from ..tool import Tool, ToolRegistry
from ..subagent import SubAgent, SubAgentConfig


def register_subagent_tools(registry: ToolRegistry, config, provider) -> None:
    parent_tools = registry

    def _sub_agent(args: dict) -> str:
        task = args.get("task", "")
        tool_names = None
        if args.get("tools"):
            tool_names = [t.strip() for t in args["tools"].split(",") if t.strip()]

        sub_config = SubAgentConfig(
            system_prompt=args.get("system_prompt", "You are a helpful assistant."),
            tool_names=tool_names,
            max_tool_calls=args.get("max_tool_calls", 20),
            max_tokens=args.get("max_tokens", 4096),
            bypass_permissions=True,
            tool_timeout=config.tool_timeout,
        )
        sub = SubAgent(provider=provider, tools=parent_tools, config=sub_config)
        result = asyncio.run(sub.run(task))
        if result.had_error:
            return f"[SubAgent error] {result.content}"
        return result.content

    registry.register(Tool(
        "sub_agent",
        "Delegate a task to a sub-agent with independent system prompt and tool subset.",
        {
            "task": {"type": "string", "description": "The task to delegate"},
            "system_prompt": {"type": "string", "description": "Custom system prompt", "default": "You are a helpful assistant."},
            "tools": {"type": "string", "description": "Comma-separated tool names (empty = all)", "default": ""},
            "max_tool_calls": {"type": "integer", "description": "Max tool calls", "default": 20},
            "max_tokens": {"type": "integer", "description": "Max response tokens", "default": 4096},
        },
        _sub_agent,
    ))
