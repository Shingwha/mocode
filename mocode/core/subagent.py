"""SubAgent — lightweight child agent for task delegation.

SubAgent runs an isolated agent loop with its own message history,
delegating to AgentLoop internally to avoid duplicating tool execution logic.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .agent import AgentConfig, AgentLoop
from .hook import AgentHook, HookRunner
from .tool import ToolRegistry


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
        agent,
        tools: ToolRegistry,
        config: SubAgentConfig,
        hooks: list[AgentHook] | None = None,
    ):
        self._agent = agent
        self._tools = tools
        self._config = config
        self._hooks: list[AgentHook] | None = hooks

    def _build_agent_loop(self) -> AgentLoop:
        """Construct an AgentLoop with SubAgent's configuration."""

        agent_config = AgentConfig(
            max_tokens=self._config.max_tokens,
            tool_result_limit=self._config.tool_result_limit,
            tool_timeout=self._config.tool_timeout,
            max_iterations=self._config.max_tool_calls,
        )

        return AgentLoop(
            provider=self._agent.provider,
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