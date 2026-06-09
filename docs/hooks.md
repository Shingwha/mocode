# MoCode Hook System — Developer Reference

> Covers the hook infrastructure in `core/hook.py` and the built-in `CompactHook` in `hooks/compact.py`.

---

## Table of Contents

1. [Overview](#overview)
2. [Context Objects](#context-objects)
3. [AgentHook — Base Class](#agenthook--base-class)
4. [HookRunner — Fan-Out Dispatcher](#hookrunner--fan-out-dispatcher)
5. [CompactHook — Auto-Compression](#compacthook--auto-compression)
6. [How Hooks Fit Into the Agent Loop](#how-hooks-fit-into-the-agent-loop)
7. [Writing Custom Hooks](#writing-custom-hooks)
8. [Reference: Built-in Hooks](#reference-built-in-hooks)
9. [Design Notes](#design-notes)

---

## Overview

MoCode uses a **class-based hook system** to observe and modify the `AgentLoop` lifecycle without modifying the loop itself. Hooks receive typed context objects, can read and mutate state, and are executed with **error isolation** — a crash in one hook never breaks another or the agent loop.

The hook system lives in two layers:

| File | Role |
|------|------|
| `mocode/core/hook.py` | Defines the `AgentHook` base class, context dataclasses, `HookRunner` dispatcher, and `ToolTimingTracker` utility. |
| `mocode/hooks/compact.py` | Ships `CompactHook`, the only built-in hook that triggers automatic context compression. |

Additional hooks exist in the application layer (e.g., `CLIDisplayHook` in `app/cli/hook.py` and `_WorkflowNodeHook` in `app/workflow/hooks.py`) but these are app-specific, not part of the core framework.

---

## Context Objects

Three dataclasses carry state through the hook lifecycle. All are defined in `core/hook.py`.

### IterationContext

Shared across the entire iteration lifecycle (`before_iteration` → `on_response` → `after_tools` → `after_iteration`). The `AgentLoop` creates one instance per iteration and passes it to every hook method that needs it.

```python
@dataclass
class IterationContext:
    messages: list[dict]           # mutable — hooks can read/modify
    iteration: int                 # current iteration index (0-based)
    response: Response | None      # set after on_response
    usage: Usage | None            # set after on_response
    final_content: str             # LLM text content (set after on_response)
    reasoning_content: str | None  # reasoning/thinking content (set after on_response)
    stop_reason: str | None        # finish_reason from LLM
    error: Exception | None        # non-None if an exception occurred
    tool_calls: list[ToolCall]     # set before after_tools
    tool_results: list[dict]       # set before after_tools
    _needs_compact: bool           # private — set by hooks, checked by AgentLoop
```

**Mutation rules:**

- `before_iteration`: `messages` is **writable** — hooks can inject/modify messages before the LLM call. Set `_needs_compact = True` to trigger compression (see [CompactHook](#compacthook--auto-compression)).
- `on_response`: **Read-only** context. Observe `response`, `final_content`, `usage`, `reasoning_content`.
- `after_tools`: `messages` is **writable** — hooks can modify messages after tool results are appended.
- `after_iteration`: End of iteration — use for cleanup or summary.

### ToolCallContext

One instance per concurrent tool call. Created by `_run_tool_async` and passed to `on_tool_start` and `on_tool_complete`.

```python
@dataclass
class ToolCallContext:
    tool_name: str          # e.g. "read", "bash"
    tool_args: dict         # parsed JSON arguments
    tool_call_id: str       # unique ID like "call_1", "call_2"
    tool_result: str | None # set by on_tool_complete (success or error)
    tool_error: str | None  # set by on_tool_complete (error message)
    tool_timeout: int | None# set by on_tool_complete (timeout in seconds)
```

Each tool call gets its own independent `ToolCallContext` instance, so concurrent tool calls do not interfere with each other.

### CompactContext

Passed to `on_compact` when context compression is triggered. Contains a **mutable** reference to the message list — hooks replace it in place via slice assignment.

```python
@dataclass
class CompactContext:
    messages: list[dict]  # mutable — hook replaces via messages[:] = ...
    old_count: int        # message count before compaction
    new_count: int        # message count after compaction (set by AgentLoop)
```

### ToolTimingTracker

A reusable helper for per-call timing. Not a context object — it's composed into custom hooks that need timing data.

```python
@dataclass
class ToolTimingTracker:
    _call_start: dict[str, float]   # call_id → monotonic timestamp
    _elapsed: dict[str, float]      # tool_name → max elapsed seconds
    _errors: dict[str, str]         # tool_name → first error message
```

**Methods:**

| Method | Description |
|--------|-------------|
| `start(call_id)` | Record monotonic start time |
| `complete(call_id, name, error=None, timeout=None)` → `float` | Finalize, return elapsed seconds. Tracks max elapsed and first error per tool name. |
| `reset()` | Clear all state |

---

## AgentHook — Base Class

`AgentHook` is the extension point for the agent lifecycle. All methods are **no-ops by default** — subclasses override only what they need.

```python
class AgentHook:
    async def before_iteration(self, ctx: IterationContext) -> None:
        """Before each LLM call. ctx.messages is modifiable."""

    async def on_response(self, ctx: IterationContext) -> None:
        """After each LLM response. Read ctx.response/final_content/usage."""

    async def after_tools(self, ctx: IterationContext) -> None:
        """After all tool results are collected. ctx.messages is modifiable."""

    async def after_iteration(self, ctx: IterationContext) -> None:
        """After each loop iteration completes."""

    async def on_tool_start(self, ctx: ToolCallContext) -> None:
        """Before a single tool executes. Read ctx.tool_name/tool_args/tool_call_id."""

    async def on_tool_complete(self, ctx: ToolCallContext) -> None:
        """After a single tool completes. Read ctx.tool_result/tool_error/tool_timeout."""

    async def on_compact(self, ctx: CompactContext) -> None:
        """When context is compacted. Read/write ctx.messages, ctx.old_count/new_count."""
```

### Lifecycle Sequence

For a typical agent turn with N tool calls:

```
before_iteration(ctx)          ← read/write messages, set _needs_compact?
  ↓
  [if _needs_compact]
  on_compact(compact_ctx)      ← replace messages
  ↓
  LLM call → Response
  ↓
on_response(ctx)               ← observe response, usage, content
  ↓
  [if response has tool_calls]
  on_tool_start(tc₁)          ← per tool, concurrent
  on_tool_complete(tc₁)
  on_tool_start(tc₂)
  on_tool_complete(tc₂)
  ...
  ↓
  after_tools(ctx)             ← messages now includes tool results
  ↓
  [next iteration]

  [if no tool_calls — final response]
  after_iteration(ctx)         ← turn complete
```

### Which Methods Are Called When

| Scenario | Methods called |
|----------|----------------|
| LLM returns text (no tools) | `before_iteration` → `on_response` → `after_iteration` |
| LLM returns tool calls | `before_iteration` → `on_response` → `on_tool_start` × N → `on_tool_complete` × N → `after_tools` → *(loop continues)* |
| Compact triggered | `before_iteration` *(sets `_needs_compact`)* → `on_compact` → LLM call → `on_response` → ... |
| Cancellation during tools | `on_tool_start` → ... → `CancelledError` propagates (no `after_tools`) |
| Tool timeout | `on_tool_start` → `on_tool_complete` *(with `tool_timeout` set)* |
| Tool not found | `on_tool_start` → `on_tool_complete` *(with `tool_error` set)* |

---

## HookRunner — Fan-Out Dispatcher

`HookRunner` holds a list of `AgentHook` instances and fans out each lifecycle event to all of them.

```python
class HookRunner:
    def __init__(self, hooks: list[AgentHook] | None = None):
        self._hooks: list[AgentHook] = list(hooks or [])

    def add(self, hook: AgentHook) -> None:
        self._hooks.append(hook)

    # Public methods mirror AgentHook:
    async def before_iteration(self, ctx) -> None: ...
    async def on_response(self, ctx) -> None: ...
    async def after_tools(self, ctx) -> None: ...
    async def after_iteration(self, ctx) -> None: ...
    async def on_tool_start(self, ctx) -> None: ...
    async def on_tool_complete(self, ctx) -> None: ...
    async def on_compact(self, ctx) -> None: ...
```

### Dispatch Mechanism

Each public method delegates to the internal `_dispatch`:

```python
async def _dispatch(self, method: str, **kwargs) -> None:
    for h in self._hooks:
        try:
            await getattr(h, method)(**kwargs)
        except Exception:
            self._log.debug(
                "Hook %s.%s failed", type(h).__name__, method, exc_info=True
            )
```

Key behaviors:

1. **Sequential execution** — Hooks run in insertion order (the order they were added). `HookRunner` awaits each hook before moving to the next.

2. **Error isolation** — Every hook call is wrapped in `try/except`. If a hook raises, the exception is logged at `DEBUG` level and dispatch continues to the next hook. The agent loop is never interrupted by a failing hook.

3. **`__getattr__` is NOT used for dispatch** — Despite what one might expect, `HookRunner` uses explicit methods (not `__getattr__`). The `AgentHook` base class also uses explicit method definitions. The `__getattr__` pattern exists in other parts of MoCode (e.g., lazy imports in `__init__.py`), but not in the hook system.

4. **Mutation propagation** — Because hooks share the same context objects (which are dataclasses), a mutation in one hook is visible to subsequent hooks and to the agent loop. For example, if `HookA.before_iteration` modifies `ctx.messages`, `HookB.before_iteration` sees the modified list, and `AgentLoop` uses it for the LLM call.

---

## CompactHook — Auto-Compression

`CompactHook` (`hooks/compact.py`) is the only built-in hook. It automatically triggers context compression when token usage approaches the context window limit.

### Constructor

```python
class CompactHook(AgentHook):
    def __init__(
        self,
        agent,                    # the AgentLoop instance (needs agent.provider)
        threshold: float = 0.80,  # fraction of context window to trigger at
        context_window: int = 256_000,  # max tokens in the context window
    ):
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `agent` | required | The `AgentLoop` instance. CompactHook calls `agent.provider` to make LLM summarization calls. |
| `threshold` | `0.80` | Compaction triggers when `prompt_tokens > context_window × threshold`. At 80% of 256K, that's ~204,800 tokens. |
| `context_window` | `256,000` | Total context window size. Should match the model's actual limit. |

### How It Decides to Compact

CompactHook uses the `before_iteration` hook to inspect usage from the *previous* iteration:

```python
async def before_iteration(self, ctx: IterationContext) -> None:
    if ctx.usage:
        self._last_prompt_tokens = ctx.usage.prompt_tokens
    if self._last_prompt_tokens > self._context_window * self._threshold:
        ctx._needs_compact = True
```

**Decision flow:**

1. At the start of each iteration, check if `ctx.usage` exists (it won't on the first iteration since no LLM call has happened yet).
2. Store the last observed `prompt_tokens` count.
3. If `prompt_tokens > context_window × threshold`, set `ctx._needs_compact = True`.
4. `AgentLoop._loop()` reads `_needs_compact` after `before_iteration` returns. If true, it creates a `CompactContext` and calls `on_compact`.

The check is **per-iteration** — compacting only happens at the start of the next LLM call, not mid-iteration. This means the LLM call that pushed usage over the limit completes normally; compression happens before the *next* call.

### What Happens During Compaction

When `on_compact` fires:

```python
async def on_compact(self, ctx: CompactContext) -> None:
    ctx.messages[:] = await compact_messages(
        self._agent.provider,
        ctx.messages,
        summary_system_prompt.build(fmt="xml"),
        COMPACT_USER_TEMPLATE,
    )
    self._last_prompt_tokens = 0
```

1. `compact_messages()` (from `core/compact.py`) is called with the full message history.
2. Messages are **preprocessed**: tool results longer than 800 chars are truncated (head+tail preserved), tool call arguments longer than 200 chars are truncated.
3. The preprocessed messages are formatted into a readable text representation.
4. An LLM call is made using `summary_system_prompt` (system) and `COMPACT_USER_TEMPLATE` (user) to generate a structured summary in this format:

```
[Intent] What the user wants to achieve
[Done] Key actions completed
[State] Current project state
[Blockers] Unresolved errors
[Notes] User preferences and decisions
```

5. If the LLM call fails, a **fallback summary** is built programmatically: extracts first user message as intent, lists tool usage stats, and keeps the last assistant message.
6. The entire message history is replaced with a single user message: `[Context Summary]\n{summary}\n[End of summary]`.
7. `ctx.messages[:] = ...` performs in-place replacement, so all references to the list see the new content.
8. `self._last_prompt_tokens` is reset to 0, preventing immediate re-trigger.

**Net effect:** The conversation context shrinks from potentially hundreds of thousands of tokens to a compact summary, freeing up context window space for continued work.

### Token Reset

After compaction, `_last_prompt_tokens` is set to 0. On the next iteration, the `before_iteration` hook won't have `ctx.usage` yet (the new LLM call hasn't been made), so compaction won't trigger again until the *next* post-response check sees high usage.

---

## How Hooks Fit Into the Agent Loop

The `AgentLoop._loop()` method orchestrates hook calls. Here's the relevant flow:

```python
async def _loop(self) -> str:
    ctx = IterationContext(messages=self.messages)
    while True:
        # 1. Before iteration — hooks can modify messages or request compact
        await self.hooks.before_iteration(ctx)
        self.messages = ctx.messages

        if ctx._needs_compact:
            compact_ctx = CompactContext(messages=ctx.messages, old_count=len(ctx.messages))
            await self.hooks.on_compact(compact_ctx)
            compact_ctx.new_count = len(ctx.messages)
            ctx._needs_compact = False

        # 2. LLM call
        response = await with_retry(self.provider, self.provider.call, ...)

        # 3. Populate context, notify hooks
        ctx.response = response
        ctx.usage = response.usage
        await self.hooks.on_response(ctx)

        if response.tool_calls:
            # 4. Run tools in parallel — each calls on_tool_start/on_tool_complete
            tool_results = await self._run_tool_calls_parallel(response.tool_calls, ctx)
            # 5. After tools — hooks can modify messages
            ctx.messages = self.messages
            await self.hooks.after_tools(ctx)
            self.messages = ctx.messages
        else:
            # 6. Final response — iteration done
            await self.hooks.after_iteration(ctx)
            break
```

Key observations:

- **`self.messages = ctx.messages`** is synchronized back after each hook phase. If a hook replaces the list object (e.g., `ctx.messages = new_list`), the loop picks it up. But for in-place mutations (e.g., `ctx.messages.append(...)`), both the loop and the context already point to the same list.

- **Compact runs before the LLM call**, not after. This ensures the LLM call uses the compressed context.

- **Tool execution** calls `on_tool_start`/`on_tool_complete` per tool. Tools run in parallel via `asyncio.gather`, but each tool's hook calls are sequential (the `on_tool_start` must fire before the tool runs, and `on_tool_complete` after).

- **Cancellation** (`asyncio.CancelledError`) propagates through the tool execution phase. If cancelled during tools, `after_tools` is **not** called.

---

## Writing Custom Hooks

### Step 1: Subclass AgentHook

```python
from mocode.core.hook import AgentHook, IterationContext, ToolCallContext, CompactContext

class MyMetricsHook(AgentHook):
    def __init__(self):
        self.total_prompt_tokens = 0
        self.total_tool_calls = 0
        self.total_tool_errors = 0

    async def on_response(self, ctx: IterationContext) -> None:
        if ctx.usage:
            self.total_prompt_tokens += ctx.usage.prompt_tokens

    async def on_tool_complete(self, ctx: ToolCallContext) -> None:
        self.total_tool_calls += 1
        if ctx.tool_error:
            self.total_tool_errors += 1
```

### Step 2: Register with HookRunner

```python
from mocode.core.hook import HookRunner

hooks = HookRunner()
hooks.add(MyMetricsHook())
hooks.add(CompactHook(agent, threshold=0.80))
```

### Step 3: Pass to AgentLoop

```python
from mocode.core.agent import AgentLoop

loop = AgentLoop(
    provider=provider,
    system_prompt="...",
    tools=tool_registry,
    hooks=hooks,
)
```

Or use the fluent builder:

```python
from mocode.core.builder import Agent

agent = (
    Agent(provider, tool_registry)
    .system_prompt("...")
    .hook(MyMetricsHook())
    .hook(CompactHook(agent_loop, threshold=0.80))
    .build()
)
```

### Pattern: Observe-Only Hook (Logging/Metrics)

Most hooks just observe — they read context but don't modify it:

```python
class LoggingHook(AgentHook):
    """Log every tool call with its arguments and result."""

    _log = logging.getLogger("mocode.tools")

    async def on_tool_start(self, ctx: ToolCallContext) -> None:
        self._log.info("Tool start: %s(%s)", ctx.tool_name, ctx.tool_args)

    async def on_tool_complete(self, ctx: ToolCallContext) -> None:
        status = "OK" if not ctx.tool_error else f"ERROR: {ctx.tool_error}"
        self._log.info("Tool done: %s → %s", ctx.tool_name, status)
```

### Pattern: Stateful Hook with ToolTimingTracker

Use `ToolTimingTracker` for timing data without reimplementing timing logic:

```python
from mocode.core.hook import AgentHook, ToolCallContext, ToolTimingTracker

class PerfHook(AgentHook):
    def __init__(self):
        self.tracker = ToolTimingTracker()

    async def on_tool_start(self, ctx: ToolCallContext) -> None:
        self.tracker.start(ctx.tool_call_id)

    async def on_tool_complete(self, ctx: ToolCallContext) -> None:
        elapsed = self.tracker.complete(
            ctx.tool_call_id, ctx.tool_name,
            ctx.tool_error, ctx.tool_timeout,
        )
        print(f"{ctx.tool_name}: {elapsed:.2f}s")

    async def after_tools(self, ctx) -> None:
        if self.tracker.errors:
            print(f"Errors: {self.tracker.errors}")
        self.tracker.reset()  # reset per batch
```

### Pattern: Message Mutating Hook

A hook that modifies the message history:

```python
class SystemInfoHook(AgentHook):
    """Inject a system info message before each iteration."""

    async def before_iteration(self, ctx: IterationContext) -> None:
        info = f"[System] Iteration {ctx.iteration}, messages: {len(ctx.messages)}"
        # Append a system note (visible to the LLM in the next call)
        ctx.messages.append({"role": "user", "content": info})
```

### Pattern: Compact Customizer

Override `on_compact` to customize compression behavior:

```python
class ThrottledCompactHook(AgentHook):
    """Only compact once every N iterations."""

    def __init__(self, agent, min_interval=5):
        self._agent = agent
        self._min_interval = min_interval
        self._iterations_since_compact = 0
        self._last_prompt_tokens = 0

    async def before_iteration(self, ctx: IterationContext) -> None:
        self._iterations_since_compact += 1
        if ctx.usage:
            self._last_prompt_tokens = ctx.usage.prompt_tokens

    async def on_compact(self, ctx: CompactContext) -> None:
        if self._iterations_since_compact < self._min_interval:
            ctx._needs_compact = False  # suppress this compaction
            return
        # Perform compaction
        ctx.messages[:] = await compact_messages(...)
        self._iterations_since_compact = 0
```

### Pattern: Error Recovery Hook

```python
class ErrorRecoveryHook(AgentHook):
    """After tool errors, inject a helpful message for the LLM."""

    async def after_tools(self, ctx: IterationContext) -> None:
        errors = [r for r in ctx.tool_results if r.get("content", "").startswith("error:")]
        if errors:
            hint = (
                "[System] Some tools failed. Check the error messages above "
                "and try a different approach if needed."
            )
            ctx.messages.append({"role": "user", "content": hint})
```

### Rules for Custom Hooks

1. **Always subclass `AgentHook`** — never implement the interface from scratch. `HookRunner` expects `AgentHook` instances.

2. **Only override what you need** — all methods are no-ops by default.

3. **Keep hooks fast** — hooks run synchronously within the agent loop. Long-running operations block the loop. Use `asyncio.to_thread` for blocking I/O.

4. **Don't raise exceptions** — `HookRunner` catches and logs them, but your hook state may be left inconsistent. Prefer defensive coding.

5. **Reset state between iterations** — `ToolTimingTracker.reset()` exists for a reason. If your hook accumulates state across iterations, clear it at the right time (typically in `after_tools` or `after_iteration`).

6. **Use slice assignment for message replacement** — `ctx.messages[:] = new_list` replaces contents in place. `ctx.messages = new_list` reassigns the reference (also works because `AgentLoop` syncs after each phase).

7. **Context objects are shared** — all hooks in the same `HookRunner` receive the same context instance. Order of registration matters.

---

## Reference: Built-in Hooks

### CompactHook

| Property | Value |
|----------|-------|
| File | `mocode/hooks/compact.py` |
| Trigger | `before_iteration` — checks `prompt_tokens > context_window × threshold` |
| Action | `on_compact` — calls `compact_messages()` to LLM-summarize the conversation |
| Default threshold | 80% of 256K tokens (204,800) |
| Dependencies | `core/compact.py` (compression logic), `prompts/compact.py` (summary prompt) |
| Exported | `mocode/hooks/__init__.py` → `from mocode.hooks import CompactHook` |

### CLIDisplayHook (app-layer, not core)

| Property | Value |
|----------|-------|
| File | `mocode/app/cli/hook.py` |
| Purpose | Bridges `AgentHook` lifecycle to CLI display (spinner, tool status, usage) |
| Methods overridden | `on_response`, `after_iteration`, `on_tool_start`, `on_tool_complete`, `after_tools`, `on_compact` |
| Composes | `ToolTimingTracker` for per-tool timing |

### _WorkflowNodeHook (app-layer, not core)

| Property | Value |
|----------|-------|
| File | `mocode/app/workflow/hooks.py` |
| Purpose | Per-node hook in workflow execution that emits tool call events |
| Methods overridden | `on_tool_start`, `on_tool_complete` |
| Used by | `Executor` when running workflow task nodes |

---

## Design Notes

### Why Class-Based, Not Function-Based?

MoCode uses `AgentHook` subclasses instead of plain callback functions because:

- **Stateful hooks** — Many hooks need to accumulate data across iterations (token counts, timing data, error lists). A class naturally holds this state.
- **Composition** — `ToolTimingTracker` can be composed into any hook via `has-a`, avoiding inheritance gymnastics.
- **Discoverability** — A subclass with named methods makes it clear which lifecycle points are being observed. A bag of callbacks is harder to inspect.
- **Type safety** — Context objects are typed dataclasses, not `*args, **kwargs`.

### Why Error Isolation?

In production, a hooks layer is a debugging and observability tool. If a metrics hook crashes, the user's conversation should continue uninterrupted. `HookRunner._dispatch` wraps every call in `try/except` and logs failures at DEBUG level — visible to developers but invisible to users.

### Why `_needs_compact` Instead of a Method Return?

The `_needs_compact` flag on `IterationContext` is a private field that hooks set to request compression. It's checked by `AgentLoop._loop()` after `before_iteration` returns. This design:

- Keeps `AgentHook.before_iteration` return type as `None` (consistent with other methods).
- Allows multiple hooks to request compaction (the flag is idempotent).
- Lets `AgentLoop` control the compaction flow (create `CompactContext`, call `on_compact`, reset flag).

### Why Sequential, Not Parallel?

Hooks run sequentially because:

- **Deterministic ordering** — Hook A's mutation should be visible to Hook B.
- **Simplicity** — Parallel dispatch would require locks or isolated copies.
- **Performance** — Hooks are typically lightweight (logging, display updates). Sequential execution overhead is negligible compared to LLM calls and tool execution.
