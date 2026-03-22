"""Plugin system core interfaces"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from .context import PluginContext


class HookPoint(Enum):
    """Hook points for plugin interception"""

    # Plugin lifecycle
    PLUGIN_LOAD = auto()
    PLUGIN_ENABLE = auto()
    PLUGIN_DISABLE = auto()
    PLUGIN_UNLOAD = auto()

    # Agent lifecycle
    AGENT_CHAT_START = auto()
    AGENT_CHAT_END = auto()

    # Tool lifecycle
    TOOL_BEFORE_RUN = auto()
    TOOL_AFTER_RUN = auto()

    # Message lifecycle
    MESSAGE_BEFORE_SEND = auto()
    MESSAGE_AFTER_RECEIVE = auto()

    # Prompt lifecycle
    PROMPT_BUILD_START = auto()
    PROMPT_BUILD_END = auto()

    # UI Component lifecycle
    UI_COMPONENT_CREATED = auto()
    UI_COMPONENT_RENDERED = auto()
    UI_COMPONENT_COMPLETED = auto()
    UI_COMPONENT_CLEARED = auto()


class PluginState(Enum):
    """Plugin state machine"""

    DISCOVERED = auto()  # Found but not loaded
    LOADED = auto()  # Loaded into memory
    ENABLED = auto()  # Active and running
    DISABLED = auto()  # Loaded but inactive
    ERROR = auto()  # Failed to load/enable


@dataclass
class PluginMetadata:
    """Plugin metadata"""

    name: str
    version: str = "1.0.0"
    description: str = ""
    author: str = ""
    dependencies: list[str] = field(default_factory=list)
    permissions: list[str] = field(default_factory=list)
    replaces_tools: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict) -> "PluginMetadata":
        return cls(
            name=data.get("name", ""),
            version=data.get("version", "1.0.0"),
            description=data.get("description", ""),
            author=data.get("author", ""),
            dependencies=data.get("dependencies", []),
            permissions=data.get("permissions", []),
            replaces_tools=data.get("replaces_tools", []),
        )


@dataclass
class HookContext:
    """Context passed to hooks during execution"""

    hook_point: HookPoint
    data: dict[str, Any] = field(default_factory=dict)
    result: Any = None
    modified: bool = False
    _proceed: bool = field(default=True, repr=False)
    _error: Exception | None = field(default=None, repr=False)

    def stop_propagation(self) -> None:
        """Stop further hook execution"""
        self._proceed = False

    def set_result(self, result: Any) -> None:
        """Set the result and mark as modified"""
        self.result = result
        self.modified = True

    def set_error(self, error: Exception) -> None:
        """Set an error and stop propagation"""
        self._error = error
        self._proceed = False

    @property
    def has_error(self) -> bool:
        return self._error is not None


@runtime_checkable
class Hook(Protocol):
    """Hook protocol - intercepts and modifies behavior"""

    @property
    def name(self) -> str:
        """Unique hook name"""
        ...

    @property
    def hook_point(self) -> HookPoint:
        """Which hook point this hook responds to"""
        ...

    @property
    def priority(self) -> int:
        """Execution priority (lower = earlier)"""
        ...

    def execute(self, context: HookContext) -> HookContext:
        """Execute the hook logic"""
        ...

    def should_execute(self, context: HookContext) -> bool:
        """Whether this hook should run for the given context"""
        ...


class HookBase:
    """Base class for hooks - inherit from this, not from Hook Protocol

    Hook is a Protocol for type checking, HookBase provides default implementations.
    Subclasses should set _name, _hook_point, _priority and implement execute().
    """

    _name: str = ""
    _hook_point: HookPoint = HookPoint.AGENT_CHAT_START
    _priority: int = 50

    @property
    def name(self) -> str:
        return self._name

    @property
    def hook_point(self) -> HookPoint:
        return self._hook_point

    @property
    def priority(self) -> int:
        return self._priority

    def should_execute(self, context: HookContext) -> bool:
        return True

    @abstractmethod
    def execute(self, context: HookContext) -> HookContext:
        pass


class Plugin(ABC):
    """Abstract base class for plugins"""

    metadata: PluginMetadata
    state: PluginState = PluginState.DISCOVERED
    _hooks: list[Hook] = field(default_factory=list, init=False)
    _context: "PluginContext | None" = field(default=None, init=False, repr=False)

    def on_load(self) -> None:
        """Called when plugin is loaded into memory. Override if needed."""
        pass

    def on_enable(self) -> None:
        """Called when plugin is enabled. Override if needed."""
        pass

    def on_disable(self) -> None:
        """Called when plugin is disabled. Override if needed."""
        pass

    def on_unload(self) -> None:
        """Called when plugin is unloaded from memory. Override if needed."""
        pass

    def set_context(self, context: "PluginContext") -> None:
        """Called by PluginManager to inject context after on_enable"""
        self._context = context

    @property
    def context(self) -> "PluginContext | None":
        """Access to plugin context (available after on_enable)"""
        return self._context

    def get_hooks(self) -> list[Hook]:
        """Return list of hooks provided by this plugin"""
        return []

    def get_tools(self) -> list:
        """Return list of tools provided by this plugin"""
        return []

    def get_commands(self) -> list:
        """Return list of commands provided by this plugin"""
        return []

    def get_prompt_sections(self) -> list:
        """Return list of prompt sections provided by this plugin"""
        return []


@dataclass
class PluginInfo:
    """Information about a discovered plugin"""

    name: str
    path: str
    metadata: PluginMetadata | None = None
    state: PluginState = PluginState.DISCOVERED
    error: str | None = None
    instance: Plugin | None = field(default=None, repr=False)

    @property
    def is_loaded(self) -> bool:
        return self.state in (PluginState.LOADED, PluginState.ENABLED, PluginState.DISABLED)

    @property
    def is_enabled(self) -> bool:
        return self.state == PluginState.ENABLED

    @property
    def has_error(self) -> bool:
        return self.state == PluginState.ERROR or self.error is not None
