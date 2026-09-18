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
        validated = self._validate_args(args)
        if self.wants_context:
            return self.func(validated, ctx)
        return self.func(validated)

    async def run_async(
        self, args: dict, ctx: "ToolCallContext | None" = None
    ) -> "str | ToolResult":
        validated = self._validate_args(args)
        if self.is_async:
            if self.wants_context:
                return await self.func(validated, ctx)
            return await self.func(validated)
        return self.run(validated, ctx)

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
    """Instance-scoped tool registry — storage and schema generation."""

    def __init__(self):
        self._tools: dict[str, Tool] = {}
        self._schema_cache: list[dict] | None = None

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool
        self._schema_cache = None

    def unregister(self, name: str) -> Tool | None:
        result = self._tools.pop(name, None)
        if result:
            self._schema_cache = None
        return result

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def all(self) -> list[Tool]:
        return list(self._tools.values())

    def names(self) -> list[str]:
        return list(self._tools.keys())

    def all_schemas(self) -> list[dict]:
        if self._schema_cache is None:
            self._schema_cache = [t.to_schema() for t in self._tools.values()]
        return self._schema_cache

    def select(
        self,
        *,
        include_tags: set[str] | None = None,
        exclude_tags: set[str] | None = None,
        include_names: set[str] | None = None,
        exclude_names: set[str] | None = None,
    ) -> ToolRegistry:
        """Create a filtered view sharing the same Tool instances.

        A tool is kept when it matches every filter that is supplied:
        ``include_tags`` / ``include_names`` are allow-lists, ``exclude_tags`` /
        ``exclude_names`` are deny-lists.
        """
        new = ToolRegistry()
        for name, tool in self._tools.items():
            if include_names is not None and name not in include_names:
                continue
            if exclude_names and name in exclude_names:
                continue
            if include_tags is not None and not (tool.tags & include_tags):
                continue
            if exclude_tags and (tool.tags & exclude_tags):
                continue
            new._tools[name] = tool
        return new

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools
