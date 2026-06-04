"""Agent builder — the composition root.

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

from .agent import AgentLoop, AgentConfig
from .hook import AgentHook, HookRunner
from .prompt import Prompt, Section
from .provider import Provider
from .tool import Tool, ToolRegistry


class Agent:
    """Fluent builder for constructing AgentLoop instances."""

    def __init__(self):
        self._provider: Provider | None = None
        self._system_prompt: str | Prompt | list[Section] | None = None
        self._tools: ToolRegistry | list[Tool] | None = None
        self._hooks: list[AgentHook] | None = None
        self._agent_config: AgentConfig | None = None
        self._prompt_context: dict[str, Any] | None = None
        self._prompt_format: str = "xml"

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

    def prompt_context(self, **kwargs: Any) -> Self:
        self._prompt_context = kwargs
        return self

    def prompt_format(self, format: str) -> Self:
        self._prompt_format = format
        return self

    def build(self) -> AgentLoop:
        if self._provider is None:
            raise ValueError("Provider is required. Call .provider() first.")
        if self._system_prompt is None:
            raise ValueError("System prompt is required. Call .prompt() first.")

        # Tools
        if isinstance(self._tools, list):
            registry = ToolRegistry()
            for tool in self._tools:
                registry.register(tool)
        elif isinstance(self._tools, ToolRegistry):
            registry = self._tools
        else:
            registry = ToolRegistry()

        # Prompt
        if isinstance(self._system_prompt, list):
            pb = Prompt()
            for s in self._system_prompt:
                pb.register(s)
            ctx = self._prompt_context or {}
            prompt_str = pb.context(**ctx).build(fmt=self._prompt_format)
        elif isinstance(self._system_prompt, Prompt):
            ctx = self._prompt_context or {}
            prompt_str = self._system_prompt.context(**ctx).build(
                fmt=self._prompt_format
            )
        else:
            prompt_str = self._system_prompt

        return AgentLoop(
            provider=self._provider,
            system_prompt=prompt_str,
            tools=registry,
            hooks=HookRunner(self._hooks or []),
            config=self._agent_config or AgentConfig(),
        )
