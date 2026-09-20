"""Tool + ToolRegistry — instance-scoped, storage and schema only."""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from .hook import ToolCallContext

#: Prefixes AgentLoop puts on failed tool results. Display layers use them when
#: re-rendering saved history; live rendering reads ToolCallContext.status instead.
ERROR_PREFIX = "error:"
TIMEOUT_PREFIX = "timeout:"
DENIED_PREFIX = "denied:"
ERROR_PREFIXES: tuple[str, ...] = (ERROR_PREFIX, TIMEOUT_PREFIX, DENIED_PREFIX)


class ToolError(Exception):
    """Tool execution error — callers catch this."""

    def __init__(self, message: str, code: str = "execution_error"):
        self.message = message
        self.code = code
        super().__init__(message)


@dataclass
class ToolResult:
    """What a tool returns when a bare string is not enough.

    ``content`` is what the model reads. ``details`` is structured data for
    everything else — a frontend, a plugin, an application reading
    ``run.state``. Keep the model's context clean and the UI rich by putting
    formatting in ``content`` and facts in ``details``.

    A tool may still return a plain ``str``; that is exactly
    ``ToolResult(content=s)`` with no details.
    """

    content: str = ""
    details: dict[str, Any] = field(default_factory=dict)


def split_result(result: "str | ToolResult") -> tuple[str, dict[str, Any]]:
    """Normalize a tool's return value into (content, details)."""
    if isinstance(result, ToolResult):
        return result.content, dict(result.details)
    return str(result), {}


def _accepts_context(func: Callable) -> bool:
    """Whether *func* declares a second parameter for its ToolCallContext."""
    try:
        params = inspect.signature(func).parameters.values()
    except (TypeError, ValueError):
        return False
    positional = [
        p
        for p in params
        if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
    ]
    return len(positional) >= 2 or any(p.kind == p.VAR_POSITIONAL for p in params)


class Tool:
    """Tool — supports sync and async functions.

    Metadata beyond the schema:
      - ``tags``: semantic capabilities (e.g. ``{"fs"}``, ``{"shell"}``,
        ``{"delegation"}``). Registries are filtered by tag rather than by a
        hard-coded list of tool names.
      - ``summary_key``: which argument to show in one-line activity summaries.
        Defaults to the first parameter.
      - ``result_key``: which entry of :class:`ToolResult` ``details`` to show
        alongside that summary, so the same line can report what came back.
        Empty means "show nothing extra".

    A tool may declare a second parameter to receive its
    :class:`~mocode.core.hook.ToolCallContext`. That is how a long-running tool
    reports progress (``await ctx.emit(ToolOutput(...))``) or reads the
    arguments a hook rewrote. Tools that don't ask for it keep the plain
    ``(args) -> str`` shape.

    A tool may return a :class:`ToolResult` instead of a string when it has
    structured facts worth passing on.

    run() / run_async() propagate ToolError and Exception upward.
    The caller (AgentLoop) decides how to handle errors.
    """

    def __init__(
        self,
        name: str,
        description: str,
        params: dict[str, dict],
        func: Callable[[dict], str],
        *,
        tags: frozenset[str] = frozenset(),
        summary_key: str = "",
        result_key: str = "",
    ):
        self.name = name
        self.description = description
        self.tags = frozenset(tags)
        self.summary_key = summary_key or (next(iter(params), ""))
        self.result_key = result_key
        self._required = []
        normalized = {}
        for k, v in params.items():
            if not isinstance(v, dict):
                raise TypeError(
                    f"Tool '{name}': param '{k}' must be a dict with 'type' and 'description', "
                    f"got {type(v).__name__}: {v!r}"
                )
            normalized[k] = v
            if "default" not in v and not v.get("optional"):
                self._required.append(k)
        self.params = normalized
        self.func = func
        self.is_async = inspect.iscoroutinefunction(func)
        self.wants_context = _accepts_context(func)

    def run(self, args: dict, ctx: "ToolCallContext | None" = None) -> "str | ToolResult":
        """Call the function directly. An async tool returns its coroutine."""
        return self._invoke(self._validate_args(args), ctx)

    async def run_async(
        self, args: dict, ctx: "ToolCallContext | None" = None
    ) -> "str | ToolResult":
        result = self._invoke(self._validate_args(args), ctx)
        return await result if self.is_async else result

    def _invoke(
        self, validated: dict, ctx: "ToolCallContext | None"
    ) -> "str | ToolResult":
        if self.wants_context:
            return self.func(validated, ctx)
        return self.func(validated)

    def _validate_args(self, args: dict) -> dict:
        result = dict(args)
        for param_name, param_spec in self.params.items():
            if param_name not in result:
                if "default" in param_spec:
                    result[param_name] = param_spec["default"]
                elif param_name in self._required:
                    raise ToolError(
                        f"Missing required parameter '{param_name}'",
                        "missing_param",
                    )
        return result

    def to_schema(self) -> dict:
        properties = {}

        for param_name, param_spec in self.params.items():
            prop = {"type": param_spec.get("type", "string")}
            if "description" in param_spec:
                prop["description"] = param_spec["description"]
            if "enum" in param_spec:
                prop["enum"] = param_spec["enum"]
            if "default" in param_spec:
                prop["default"] = param_spec["default"]
            properties[param_name] = prop

        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": self._required,
                },
            },
        }


class ToolRegistry:
    """Instance-scoped tool registry — storage and schema generation.

    The container surface Prompt and HookRunner also use, with one twist on
    meaning: ``all()`` is the management view (every registered tool), while
    ``names()`` / ``all_schemas()`` / ``select()`` are the *visible* projection
    — a disabled tool stays registered and ``get()``-able but is not offered to
    the model, exactly like a disabled prompt section is not rendered.

    The visible projection can also be *pinned* (:meth:`freeze`): the schemas
    offered stop following the registry while every other view stays live.
    That is the host's cache-protection lever — a request's tool payload held
    byte-identical turn after turn, while a tool switched off after the freeze
    is still absent from ``names()`` and still refuses to run. Off by default:
    an unpinned registry reads live on every call, which is what an embedder
    that wants the raw flexibility gets.
    """

    def __init__(self):
        self._tools: dict[str, Tool] = {}
        self._disabled: set[str] = set()
        self._schema_cache: list[dict] | None = None
        self._frozen: list[dict] | None = None

    def register(self, tool: Tool) -> "ToolRegistry":
        self._tools[tool.name] = tool
        self._disabled.discard(tool.name)
        self._schema_cache = None
        return self

    def unregister(self, name: str) -> Tool | None:
        tool = self._tools.pop(name, None)
        self._disabled.discard(name)
        if tool is not None:
            self._schema_cache = None
        return tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def all(self) -> list[Tool]:
        return list(self._tools.values())

    def names(self) -> list[str]:
        """Names of the enabled tools — the set the model is offered."""
        return [name for name in self._tools if name not in self._disabled]

    def enable(self, name: str) -> "ToolRegistry":
        self._disabled.discard(name)
        self._schema_cache = None
        return self

    def disable(self, name: str) -> "ToolRegistry":
        if name in self._tools:
            self._disabled.add(name)
            self._schema_cache = None
        return self

    @property
    def pinned(self) -> bool:
        """Whether the offered interface is being held still."""
        return self._frozen is not None

    def freeze(self, schemas: list[dict] | None = None) -> "ToolRegistry":
        """Hold the offered interface still — *schemas*, or the registry as it
        stands now — until the next freeze.

        The host's cache-protection lever, and the reason it lives here rather
        than in the loop: what a request offers is this registry's projection,
        so pinning the projection pins the request. Everything else stays
        live, which is what makes the pin safe — a tool switched off after the
        freeze disappears from ``names()`` (and refuses to run) while the
        payload the model was offered stays byte-identical. Pass a stored
        interface to reinstate one, the way a resumed session does.
        """
        if schemas is None:
            schemas = self._live_schemas()
        self._frozen = list(schemas)
        return self

    def all_schemas(self) -> list[dict]:
        if self._frozen is not None:
            return self._frozen
        if self._schema_cache is None:
            self._schema_cache = self._live_schemas()
        return self._schema_cache

    def _live_schemas(self) -> list[dict]:
        return [
            self._tools[name].to_schema()
            for name in self._tools
            if name not in self._disabled
        ]

    def select(
        self,
        *,
        include_tags: set[str] | None = None,
        exclude_tags: set[str] | None = None,
        include_names: set[str] | None = None,
        exclude_names: set[str] | None = None,
    ) -> ToolRegistry:
        """Create a filtered view sharing the same Tool instances.

        A tool is kept when it is enabled and matches every filter that is
        supplied: ``include_tags`` / ``include_names`` are allow-lists,
        ``exclude_tags`` / ``exclude_names`` are deny-lists.
        """
        new = ToolRegistry()
        for name, tool in self._tools.items():
            if name in self._disabled:
                continue
            if include_names is not None and name not in include_names:
                continue
            if exclude_names and name in exclude_names:
                continue
            if include_tags is not None and not (tool.tags & include_tags):
                continue
            if exclude_tags and tool.tags & exclude_tags:
                continue
            new._tools[name] = tool
        if self._frozen is not None:
            new.freeze()  # a child view freezes at spawn, as its parent did
        return new

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools
