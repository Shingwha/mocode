"""Agent Facade - High-level agent operations"""

from typing import TYPE_CHECKING, Any, Callable

from .agent import AsyncAgent
from .events import EventBus
from .interrupt import InterruptToken
from .permission import PermissionHandler, PermissionMatcher
from ..plugins import HookRegistry
from ..providers import AsyncOpenAIProvider

if TYPE_CHECKING:
    from .config import Config
    from .prompt import PromptBuilder


class AgentFacade:
    """High-level facade for agent operations

    Provides a simplified interface for common agent operations like
    chatting, clearing history, switching providers, and updating prompts.
    """

    def __init__(
        self,
        config: "Config",
        event_bus: EventBus,
        interrupt_token: InterruptToken,
        permission_handler: PermissionHandler | None,
        permission_matcher: PermissionMatcher | None,
        hook_registry: HookRegistry,
        prompt_builder: "PromptBuilder",
        workdir: str,
        on_chat: Callable[[], None] | None = None,
        on_clear_history: Callable[[], None] | None = None,
    ):
        """Initialize agent facade

        Args:
            config: Configuration
            event_bus: Event bus for notifications
            interrupt_token: Token for cancellation
            permission_handler: Handler for permission prompts
            permission_matcher: Matcher for permission checks
            hook_registry: Registry for hooks
            prompt_builder: Builder for system prompts
            workdir: Working directory
            on_chat: Callback after chat message
            on_clear_history: Callback after clearing history
        """
        self._config = config
        self._event_bus = event_bus
        self._interrupt_token = interrupt_token
        self._permission_handler = permission_handler
        self._permission_matcher = permission_matcher
        self._hook_registry = hook_registry
        self._prompt_builder = prompt_builder
        self._workdir = workdir
        self._on_chat = on_chat
        self._on_clear_history = on_clear_history

        # Initialize provider
        self._provider = AsyncOpenAIProvider(
            base_url=config.base_url or None,
            api_key=config.api_key,
            model=config.model,
        )

        # Initialize agent
        self._agent = self._create_agent()

    def _create_agent(self) -> AsyncAgent:
        """Create a new agent instance"""
        from ..skills import SkillManager

        skill_manager = SkillManager.get_instance()
        system_prompt = self._prompt_builder.context(
            skill_manager=skill_manager,
            cwd=self._workdir,
        ).build()

        return AsyncAgent(
            provider=self._provider,
            system_prompt=system_prompt,
            max_tokens=self._config.max_tokens,
            event_bus=self._event_bus,
            interrupt_token=self._interrupt_token,
            config=self._config,
            permission_handler=self._permission_handler,
            permission_matcher=self._permission_matcher,
            hook_registry=self._hook_registry,
        )

    async def chat(self, message: str) -> str:
        """Send a message and get response

        Args:
            message: User message

        Returns:
            Assistant response
        """
        if self._on_chat:
            self._on_chat()
        return await self._agent.chat(message)

    def clear_history(self) -> None:
        """Clear conversation history"""
        self._agent.clear()
        if self._on_clear_history:
            self._on_clear_history()

    def switch_provider(self, config: "Config") -> None:
        """Switch to a new provider configuration

        Args:
            config: New configuration to use
        """
        self._config = config
        self._provider = AsyncOpenAIProvider(
            base_url=config.base_url or None,
            api_key=config.api_key,
            model=config.model,
        )
        self._agent.update_provider(self._provider)

    def rebuild_prompt(
        self,
        context: dict[str, Any] | None = None,
        clear_history: bool = False,
    ) -> None:
        """Rebuild system prompt

        Args:
            context: Additional context variables
            clear_history: Whether to clear message history
        """
        from ..skills import SkillManager

        ctx = context or {}
        skill_manager = SkillManager.get_instance()
        self._prompt_builder.clear_caches()
        system_prompt = self._prompt_builder.context(
            skill_manager=skill_manager,
            cwd=self._workdir,
            **ctx,
        ).build()
        self._agent.update_system_prompt(system_prompt, clear_history=clear_history)

    def update_prompt(self, prompt: str, clear_history: bool = False) -> None:
        """Directly update system prompt

        Args:
            prompt: New system prompt
            clear_history: Whether to clear message history
        """
        self._agent.update_system_prompt(prompt, clear_history=clear_history)

    @property
    def messages(self) -> list[dict[str, Any]]:
        """Current message history"""
        return self._agent.messages

    @messages.setter
    def messages(self, value: list[dict[str, Any]]) -> None:
        """Set message history"""
        self._agent.messages = value

    @property
    def agent(self) -> AsyncAgent:
        """Underlying agent instance"""
        return self._agent

    @property
    def provider(self) -> AsyncOpenAIProvider:
        """Current provider instance"""
        return self._provider
