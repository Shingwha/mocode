"""SubAgentTool — LLM-callable tool for delegating tasks to sub-agents."""
from __future__ import annotations

from ..core.subagent import SubAgent, SubAgentConfig
from ..core.tool import Tool, ToolRegistry

_BLOCKED_TOOLS = {"sub_agent", "compact"}

_SUB_AGENT_PARAMS = {
    "task": {"type": "string", "description": "The task to delegate to the sub-agent"},
    "tools": {"type": "string", "description": "Comma-separated allowlist of tool names. LEAVE EMPTY to give the sub-agent full access to all tools.", "default": ""},
    "max_tool_calls": {"type": "integer", "description": "Max tool calls (default 50)", "default": 50},
    "max_tokens": {"type": "integer", "description": "Max response tokens (default 8192)", "default": 8192},
}
_SUB_AGENT_DESC = (
    "Delegate a task to a sub-agent that inherits ALL your tools by default. "
    "The sub-agent runs autonomously with its own message history and returns the final result. "
    "Put any special requirements or constraints directly in the task description."
)


class SubAgentTool(Tool):
    """Let the LLM delegate tasks to a sub-agent."""
    def __init__(self, agent, parent_tools: ToolRegistry, tool_timeout: int = 240) -> None:
        self._agent = agent
        self._parent_tools = parent_tools
        self._tool_timeout = tool_timeout
        super().__init__(name="sub_agent", description=_SUB_AGENT_DESC, params=_SUB_AGENT_PARAMS, func=self._execute)

    async def _execute(self, args: dict) -> str:
        from ..prompts.subagent import subagent_system_prompt
        task = args["task"]
        derived_tools = self._parent_tools.derived(exclude=_BLOCKED_TOOLS)
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
            tool_timeout=self._tool_timeout,
        )
        sub = SubAgent(agent=self._agent, tools=derived_tools, config=sub_config)
        result = await sub.run(task)
        if result.had_error:
            return f"[SubAgent error] {result.content}"
        return result.content
