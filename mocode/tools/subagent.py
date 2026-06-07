"""SubAgentTool — LLM-callable tool for delegating tasks to sub-agents.

SubAgentTool exposes SubAgent as an LLM-callable tool.
"""

from __future__ import annotations

from ..core.subagent import SubAgent, SubAgentConfig
from ..core.tool import Tool, ToolRegistry

_BLOCKED_TOOLS = {"sub_agent", "compact"}


def SubAgentTool(
    agent,
    parent_tools: ToolRegistry,
    tool_timeout: int = 240,
) -> Tool:
    """Create a tool that lets the LLM delegate tasks to a sub-agent."""

    async def _sub_agent(args: dict) -> str:
        from ..prompts.subagent import subagent_system_prompt

        task = args["task"]

        # All filtering happens here: exclude blocked tools first
        derived_tools = parent_tools.derived(exclude=_BLOCKED_TOOLS)

        # If user specified tool names, further filter to only those
        if args.get("tools"):
            tool_names = [t.strip() for t in args["tools"].split(",") if t.strip()]
            filtered = ToolRegistry()
            for name in tool_names:
                tool = derived_tools.get(name)
                if tool:
                    filtered.register(tool)
            derived_tools = filtered

        sub_config = SubAgentConfig(
            system_prompt=subagent_system_prompt.build(fmt="xml"),
            max_tool_calls=args.get("max_tool_calls", 50),
            max_tokens=args.get("max_tokens", 8192),
            tool_timeout=tool_timeout,
        )
        sub = SubAgent(agent=agent, tools=derived_tools, config=sub_config)
        result = await sub.run(task)
        if result.had_error:
            return f"[SubAgent error] {result.content}"
        return result.content

    return Tool(
        "sub_agent",
        "Delegate a task to a sub-agent that inherits ALL your tools by default. "
        "The sub-agent runs autonomously with its own message history and returns the final result. "
        "Put any special requirements or constraints directly in the task description.",
        {
            "task": {
                "type": "string",
                "description": "The task to delegate to the sub-agent",
            },
            "tools": {
                "type": "string",
                "description": "Comma-separated allowlist of tool names. LEAVE EMPTY to give the sub-agent full access to all tools.",
                "default": "",
            },
            "max_tool_calls": {
                "type": "integer",
                "description": "Max tool calls (default 50)",
                "default": 50,
            },
            "max_tokens": {
                "type": "integer",
                "description": "Max response tokens (default 8192)",
                "default": 8192,
            },
        },
        _sub_agent,
    )