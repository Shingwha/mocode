"""Tool + ToolRegistry — instance-scoped, storage and schema only."""

from __future__ import annotations

import inspect
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Literal

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


class ToolConflictError(Exception):
    """Two different sources registered a tool under the same name.

    A registration-time explosion instead of a silent overwrite: whoever
    registered first keeps the name, and the collision says whose is whose.
    Same-source reregistration stays an overwrite (a plugin hot-updating its
    own tools), and ``register(..., replace=True)`` forces the takeover.
    """

    def __init__(self, tool_name: str, existing: str, incoming: str):
        self.tool_name = tool_name
        self.existing = existing
        self.incoming = incoming
        super().__init__(
            f"tool '{tool_name}' is already registered by "
            f"{existing or '<unattributed>'}; refusing the one from "
            f"{incoming or '<unattributed>'} — register with replace=True to "
            f"force it"
        )


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


# ── The schema dialect and its checker ──────────────────────
#
# Tool arguments are declared as a JSON Schema object node — the dialect every
# model speaks natively, so what the model is offered and what the arguments
# are checked against are the same document. The checker below is deliberately
# small and dependency-free: it enforces the common keywords and *passes*
# everything else (logged at debug) rather than guessing at semantics it does
# not implement. A keyword it does not know may therefore accept more than a
# full validator would — forward compatibility beats false rejections here,
# because the alternative is a tool nobody can call after their provider
# grew a new schema feature.

_log = logging.getLogger(__name__)

#: Per-type value checks. ``bool`` is excluded from ``integer`` and ``number``
#: explicitly: in Python it is an ``int`` subclass, in JSON it is not a number.
_TYPE_CHECKS: dict[str, Callable[[Any], bool]] = {
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "array": lambda v: isinstance(v, list),
    "object": lambda v: isinstance(v, dict),
    "null": lambda v: v is None,
}

#: Keywords the checker enforces (plus ``description``, an annotation it
#: reads past silently — it carries no validation semantics to skip).
_SUPPORTED_KEYWORDS = frozenset(
    {"type", "required", "properties", "items", "enum", "anyOf", "oneOf", "default", "description"}
)


def _type_name(value: Any) -> str:
    if value is None:
        return "null"
    return {bool: "boolean", int: "integer", float: "number", str: "string",
            list: "array", dict: "object"}.get(type(value), type(value).__name__)


def _check(value: Any, schema: Any, path: str) -> Any:
    """Validate *value* against one schema node, filling defaults as reached.

    Returns the value (with ``default`` filled into object levels the check
    actually reached). Raises :class:`ToolError` on the first violation —
    ``missing_param`` for an absent required property, ``invalid_type`` for
    everything else (a wrong type, a value outside an ``enum``, a miss against
    every ``anyOf`` branch, a ``oneOf`` that matches two). Unknown keywords
    pass, with a debug log; unknown types pass the same way.
    """
    if not isinstance(schema, dict):
        return value  # nothing we can read — treat as unconstrained
    for keyword in schema:
        if keyword not in _SUPPORTED_KEYWORDS:
            _log.debug(
                "schema keyword %r at %s is not checked by the built-in "
                "validator; the value passes",
                keyword,
                path,
            )

    # anyOf / oneOf: match against the branches instead of this node's own
    # type — the union is the whole constraint.
    branches = schema.get("anyOf")
    exclusive = False
    if branches is None:
        branches = schema.get("oneOf")
        exclusive = branches is not None
    if isinstance(branches, list):
        matches = 0
        for branch in branches:
            try:
                _check(value, branch, path)
            except ToolError:
                continue
            matches += 1
        if matches == 0:
            raise ToolError(f"{path}: matches none of the allowed schemas", "invalid_type")
        if exclusive and matches > 1:
            raise ToolError(f"{path}: matches more than one schema (oneOf)", "invalid_type")
        return value

    enum = schema.get("enum")
    if isinstance(enum, list) and value not in enum:
        raise ToolError(
            f"{path}: must be one of {enum!r}, got {value!r}", "invalid_type"
        )

    declared = schema.get("type")
    if isinstance(declared, str):
        check = _TYPE_CHECKS.get(declared)
        if check is not None and not check(value):
            raise ToolError(
                f"{path}: expected {declared}, got {_type_name(value)}",
                "invalid_type",
            )
    elif isinstance(declared, list):
        checks = [_TYPE_CHECKS.get(t) for t in declared if isinstance(t, str)]
        if checks and not any(c is not None and c(value) for c in checks):
            raise ToolError(
                f"{path}: expected one of {list(declared)!r}, got {_type_name(value)}",
                "invalid_type",
            )

    if isinstance(value, dict):
        required = schema.get("required")
        if isinstance(required, list):
            for key in required:
                if key not in value:
                    raise ToolError(
                        f"{path}.{key}: missing required parameter",
                        "missing_param",
                    )
        properties = schema.get("properties")
        if isinstance(properties, dict):
            for key, sub in properties.items():
                if key in value:
                    value[key] = _check(value[key], sub, f"{path}.{key}")
                elif isinstance(sub, dict) and "default" in sub:
                    value[key] = sub["default"]
    elif isinstance(value, list):
        items = schema.get("items")
        if isinstance(items, dict):
            for i, item in enumerate(value):
                value[i] = _check(item, items, f"{path}[{i}]")
    return value


def _positional_shape(func: Callable) -> tuple[int, int, bool] | None:
    """(declared positional params, required ones, accepts ``*args``) — or
    ``None`` when *func* cannot be introspected."""
    try:
        params = list(inspect.signature(func).parameters.values())
    except (TypeError, ValueError):
        return None
    positional = [
        p
        for p in params
        if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
    ]
    required = [p for p in positional if p.default is p.empty]
    star_args = any(p.kind == p.VAR_POSITIONAL for p in params)
    return len(positional), len(required), star_args


class Tool:
    """Tool — supports sync and async functions.

    Arguments are declared in ``schema`` — a JSON Schema object node
    (``{"type": "object", "properties": {...}, "required": [...]}``), passed
    through to the provider as the function's ``parameters`` and used by the
    built-in checker (see :func:`_check`) to fill defaults and reject bad
    arguments before the function runs. ``returns`` is optional structured-
    output metadata for SDKs to render; it never enters the request.

    Metadata beyond the schema:
      - ``tags``: semantic capabilities (e.g. ``{"fs"}``, ``{"shell"}``,
        ``{"delegation"}``). Registries are filtered by tag rather than by a
        hard-coded list of tool names.
      - ``summary_key``: which argument to show in one-line activity summaries.
        Defaults to the first required parameter, or the first property when
        nothing is required.
      - ``result_key``: which entry of :class:`ToolResult` ``details`` to show
        alongside that summary, so the same line can report what came back.
        Empty means "show nothing extra".
      - ``availability``: who may use the tool — the model, program code, or
        both (the default). A tool invisible to an audience is neither offered
        to it nor runnable by it; see :meth:`ToolRegistry.names`.
      - ``source``: who registered the tool — a channel-prefixed name stamped
        by the registration path (``builtin:<name>``, ``plugin:<name>``,
        ``host``); empty means bare core. A self-reported value does not
        survive host registration: the path is the authority, so a tool
        cannot claim an identity its loader cannot back.

    A tool that wants its :class:`~mocode.core.hook.ToolCallContext` says so
    explicitly: ``with_context=True`` means the function is called as
    ``(args, ctx)`` — that is how a long-running tool reports progress
    (``await ctx.emit(ToolOutput(...))``) or reads the arguments a hook
    rewrote. The declaration is checked at construction, so both mistakes (a
    declared context the function cannot receive, and a second required
    parameter nobody will pass) fail at import/build time instead of
    mid-turn. Tools that don't ask for it keep the plain ``(args) -> str``
    shape.

    A tool may return a :class:`ToolResult` instead of a string when it has
    structured facts worth passing on.

    run() / run_async() propagate ToolError and Exception upward.
    The caller (AgentLoop) decides how to handle errors.
    """

    def __init__(
        self,
        name: str,
        description: str,
        schema: dict,
        func: Callable[[dict], "str | ToolResult"],
        *,
        tags: frozenset[str] = frozenset(),
        summary_key: str = "",
        result_key: str = "",
        returns: dict | None = None,
        with_context: bool = False,
        availability: Literal["model", "program", "both"] = "both",
        source: str = "",
    ):
        if availability not in ("model", "program", "both"):
            raise ValueError(
                f"Tool '{name}': availability must be 'model', 'program' or "
                f"'both', got {availability!r}"
            )
        if not isinstance(schema, dict):
            raise TypeError(
                f"Tool '{name}': schema must be a JSON Schema object node "
                f"(a dict), got {type(schema).__name__}"
            )
        self.name = name
        self.description = description
        self.schema = schema
        self.returns = returns
        self.tags = frozenset(tags)
        properties = schema.get("properties") or {}
        required = list(schema.get("required") or [])
        self.summary_key = summary_key or (
            required[0] if required else next(iter(properties), "")
        )
        self.result_key = result_key
        self.availability = availability
        self.source = source
        self.func = func
        self.is_async = inspect.iscoroutinefunction(func)
        self.wants_context = with_context
        shape = _positional_shape(func)
        if shape is not None:
            declared, required_positional, star_args = shape
            if with_context and declared < 2 and not star_args:
                raise TypeError(
                    f"Tool '{name}': with_context=True needs a function callable "
                    f"as (args, ctx) — it declares {declared} positional "
                    "parameter(s)"
                )
            if not with_context and required_positional > 1:
                raise TypeError(
                    f"Tool '{name}': the function requires {required_positional} "
                    "positional parameters but no context is declared — pass "
                    "with_context=True so the second one receives its "
                    "ToolCallContext"
                )

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
        """Fill defaults and check the arguments against ``self.schema``.

        A violation is a :class:`ToolError` — ``missing_param`` or
        ``invalid_type`` — so it flows through the ordinary ``error:`` result
        pipeline instead of breaking the turn. Defaults are filled at the top
        level and at every nested level the check reaches.
        """
        result = _check(dict(args), self.schema, "$")
        return result if isinstance(result, dict) else dict(args)

    def to_schema(self) -> dict:
        """The tool as a chat-completion function definition.

        ``parameters`` is the declared ``schema``, passed through unchanged.
        """
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.schema,
            },
        }


#: What each audience may see. A tool declared for one audience only is
#: invisible to the other — invisible means neither offered nor runnable.
_AUDIENCES: dict[str, frozenset[str]] = {
    "model": frozenset({"model", "both"}),
    "program": frozenset({"program", "both"}),
}

Audience = Literal["model", "program"]


class ToolRegistry:
    """Instance-scoped tool registry — storage and schema generation.

    The container surface Prompt and HookRunner also use, with one twist on
    meaning: ``all()`` is the management view (every registered tool), while
    ``names()`` / ``all_schemas()`` / ``select()`` are the *visible* projection
    — a disabled tool stays registered and ``get()``-able but is not offered to
    the model, exactly like a disabled prompt section is not rendered.

    The projection is per **audience**: ``names()`` and friends answer for the
    model by default, and ``audience="program"`` answers for code running
    tools on its own behalf — one mechanism expresses an additive deployment
    (everything ``both``), a folded one (the folded tools marked ``program``,
    the model sees the fold point only) or a mixed one, without anyone
    reaching for the freeze lever to fake it.

    The visible projection can also be *pinned* (:meth:`freeze`): the schemas
    offered to the model stop following the registry while every other view
    stays live. That is the host's cache-protection lever — a request's tool
    payload held byte-identical turn after turn, while a tool switched off
    after the freeze is still absent from ``names()`` and still refuses to
    run. Off by default: an unpinned registry reads live on every call, which
    is what an embedder that wants the raw flexibility gets.
    """

    def __init__(self):
        self._tools: dict[str, Tool] = {}
        self._disabled: set[str] = set()
        self._schema_cache: list[dict] | None = None
        self._frozen: list[dict] | None = None

    def register(self, tool: Tool, *, replace: bool = False) -> "ToolRegistry":
        """Register *tool*, refusing a takeover by a different source.

        A same-name registration is an overwrite, as ever — but only when the
        two agree on where they came from: both unattributed (bare core), or
        both carrying the same source (a plugin hot-updating its own tools).
        Two different non-empty sources collide loudly
        (:class:`ToolConflictError`) instead of silently replacing each
        other's work; ``replace=True`` forces the takeover knowingly.
        """
        existing = self._tools.get(tool.name)
        if (
            existing is not None
            and existing is not tool
            and not replace
            and existing.source
            and tool.source
            and existing.source != tool.source
        ):
            raise ToolConflictError(tool.name, existing.source, tool.source)
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

    def names(self, *, audience: Audience = "model") -> list[str]:
        """Names of the tools visible to *audience* — the set the model is offered."""
        allowed = _AUDIENCES[audience]
        return [
            name
            for name, tool in self._tools.items()
            if name not in self._disabled and tool.availability in allowed
        ]

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

        What is pinned is the *model* projection, as ever; the program
        projection keeps reading live.
        """
        if schemas is None:
            schemas = self._live_schemas()
        self._frozen = list(schemas)
        return self

    def all_schemas(self, *, audience: Audience = "model") -> list[dict]:
        """The schemas offered to *audience* — the model's by default."""
        if audience == "model":
            if self._frozen is not None:
                return self._frozen
            if self._schema_cache is None:
                self._schema_cache = self._live_schemas()
            return self._schema_cache
        return self._live_schemas(audience)

    def _live_schemas(self, audience: Audience = "model") -> list[dict]:
        allowed = _AUDIENCES[audience]
        return [
            tool.to_schema()
            for name, tool in self._tools.items()
            if name not in self._disabled and tool.availability in allowed
        ]

    def select(
        self,
        *,
        audience: Audience = "model",
        include_tags: set[str] | None = None,
        exclude_tags: set[str] | None = None,
        include_names: set[str] | None = None,
        exclude_names: set[str] | None = None,
    ) -> ToolRegistry:
        """Create a filtered view sharing the same Tool instances.

        A tool is kept when it is visible to *audience*, enabled, and matches
        every filter that is supplied: ``include_tags`` / ``include_names``
        are allow-lists, ``exclude_tags`` / ``exclude_names`` are deny-lists.
        """
        new = ToolRegistry()
        allowed = _AUDIENCES[audience]
        for name, tool in self._tools.items():
            if name in self._disabled:
                continue
            if tool.availability not in allowed:
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
