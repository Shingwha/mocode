"""Agent builder — the composition root for embedding MoCode's core.

Usage:
    from mocode.core import Agent

    agent = (Agent()
        .provider(my_provider)
        .prompt("You are a helpful assistant.")
        .tools([my_tool])
        .build())

    result = await agent.chat("hello")
"""

from __future__ import annotations

from typing import Any, Self

from .agent import AgentConfig, AgentLoop
from .hook import AgentHook, HookRunner
from .prompt import Prompt, Section
from .provider import ModelSpec, Provider
from .tool import Tool, ToolRegistry


class Agent:
    """Fluent builder for constructing AgentLoop instances."""

    def __init__(self):
        self._provider: Provider | None = None
        self._system_prompt: str | Prompt | list[Section] | None = None
        self._tools: ToolRegistry | list[Tool] | None = None
        self._hooks: list[AgentHook] | None = None
        self._agent_config: AgentConfig | None = None
        self._model: ModelSpec | None = None
        self._prompt_context: dict[str, Any] | None = None

    def provider(self, provider: Provider) -> Self:
        self._provider = provider
        return self

    def prompt(self, prompt: str | Prompt | list[Section]) -> Self:
        self._system_prompt = prompt
        return self

    def tools(self, tools: ToolRegistry | list[Tool]) -> Self:
        self._tools = tools
        return self

    def hooks(self, hooks: list[AgentHook]) -> Self:
        self._hooks = hooks
        return self

    def config(self, config: AgentConfig) -> Self:
        self._agent_config = config
        return self

    def model(self, model: ModelSpec) -> Self:
        """Describe the model being driven (name, context window, output cap)."""
        self._model = model
        return self

    def prompt_context(self, **kwargs: Any) -> Self:
        self._prompt_context = kwargs
        return self

    def build(self) -> AgentLoop:
        if self._provider is None:
            raise ValueError("Provider is required. Call .provider() first.")
        if self._system_prompt is None:
            raise ValueError("System prompt is required. Call .prompt() first.")

        registry = self._as_registry(self._tools)
        prompt_str = self._as_prompt(self._system_prompt)

        return AgentLoop(
            provider=self._provider,
            system_prompt=prompt_str,
            tools=registry,
            hooks=HookRunner(self._hooks or []),
            config=self._agent_config or AgentConfig(),
            model=self._model,
        )

    @staticmethod
    def _as_registry(tools: ToolRegistry | list[Tool] | None) -> ToolRegistry:
        if isinstance(tools, ToolRegistry):
            return tools
        registry = ToolRegistry()
        for tool in tools or []:
            registry.register(tool)
        return registry

    def _as_prompt(self, prompt: str | Prompt | list[Section]) -> str:
        if isinstance(prompt, str):
            return prompt
        if isinstance(prompt, list):
            pb = Prompt()
            for section in prompt:
                pb.register(section)
        else:
            pb = prompt
        return pb.context(**(self._prompt_context or {})).build()
