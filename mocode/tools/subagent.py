"""SubAgent — lightweight child agent for task delegation.

SubAgent runs an isolated agent loop with its own message history,
delegating to AgentLoop internally to avoid duplicating tool execution logic.
SubAgentTool exposes this as an LLM-callable tool.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ..core.agent import AgentConfig, AgentLoop
from ..core.hook import AgentHook, HookRunner
from ..core.tool import Tool, ToolRegistry
from ..prompts.subagent import subagent_system_prompt

if TYPE_CHECKING:
    from ..core.provider import Provider


def _resolve_provider(provider):
    """Normalize Provider or provider-getter into a () -> Provider callable."""
    if callable(provider) and not hasattr(provider, "call"):
        return provider
    _p = provider
    return lambda: _p


@dataclass(frozen=True)
class SubAgentConfig:
    system_prompt: str
    max_tool_calls: int = 50
    max_tokens: int = 4096
    tool_timeout: int | None = 240
    tool_result_limit: int = 0


@dataclass
class SubAgentResult:
    content: str = ""
    tool_calls_made: int = 0
    messages: list[dict] = field(default_factory=list)
    had_error: bool = False


class SubAgent:
    """Lightweight agent loop — delegates to AgentLoop internally."""

    def __init__(
        self,
        provider: Provider | Callable[[], Provider],
        tools: ToolRegistry,
        config: SubAgentConfig,
        hooks: list[AgentHook] | None = None,
    ):
        self._provider_getter = _resolve_provider(provider)
        self._tools = tools
        self._config = config
        self._hooks: list[AgentHook] | None = hooks

    def _build_agent_loop(self) -> AgentLoop:
        """Construct an AgentLoop with SubAgent's configuration."""
        provider = self._provider_getter()

        agent_config = AgentConfig(
            max_tokens=self._config.max_tokens,
            tool_result_limit=self._config.tool_result_limit,
            tool_timeout=self._config.tool_timeout or 240,
            max_iterations=self._config.max_tool_calls,
        )

        return AgentLoop(
            provider=provider,
            system_prompt=self._config.system_prompt,
            tools=self._tools,
            hooks=HookRunner(self._hooks or []),
            config=agent_config,
        )

    async def run(self, user_prompt: str) -> SubAgentResult:
        messages = [{"role": "user", "content": user_prompt}]
        return await self.run_messages(messages)

    async def run_messages(self, messages: list[dict]) -> SubAgentResult:
        loop = self._build_agent_loop()
        result = await loop.run_with_messages(messages)
        return SubAgentResult(
            content=result.content,
            tool_calls_made=result.iterations,
            messages=result.messages,
            had_error=result.had_error,
        )


_BLOCKED_TOOLS = {"sub_agent", "compact"}


def SubAgentTool(
    provider: Provider | Callable[[], Provider],
    parent_tools: ToolRegistry,
    tool_timeout: int = 240,
) -> Tool:
    """Create a tool that lets the LLM delegate tasks to a sub-agent."""

    async def _sub_agent(args: dict) -> str:
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
            system_prompt=subagent_system_prompt.build(format="xml"),
            max_tool_calls=args.get("max_tool_calls", 50),
            max_tokens=args.get("max_tokens", 8192),
            tool_timeout=tool_timeout,
        )
        sub = SubAgent(provider=provider, tools=derived_tools, config=sub_config)
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
