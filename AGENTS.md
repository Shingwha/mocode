# MoCode — Development Guide

## Dev Commands

```bash
# Install dependencies (uses uv + hatchling) — always use uv for Python
uv sync

# Run all tests
uv run pytest

# Single test file / class / method
uv run pytest tests/test_agent_loop.py
uv run pytest tests/test_plugins.py::TestPluginHost

# Ad-hoc check
uv run python -c "from mocode.core import Tool; t = Tool('x','d',{'p':{'type':'string','description':'p'}}, lambda a:'ok'); print(t.to_schema())"
```

## The One Rule

> **A concept that could be written as a plugin does not belong in `core/`.**

Everything below follows from it. Workflows, sub-agents, context compaction, web fetch and the virtual file system were all removed from this codebase because they are capabilities, not mechanism. If you are tempted to add an `if` for a specific tool, hook or feature inside `core/`, write a plugin instead.

## Architecture

```
mocode/
├── core/        kernel — mechanism only, zero app dependencies
│   ├── agent.py     AgentConfig, LoopResult, AgentLoop (stream/derive)
│   ├── events.py    Event + the eleven events a run emits
│   ├── state.py     RunState — the events folded into a live snapshot
│   ├── builder.py   Agent — fluent builder
│   ├── hook.py      AgentHook, HookRunner, IterationContext, ToolCallContext
│   ├── prompt.py    Prompt, Section — section-based XML assembly
│   ├── provider.py  Provider protocol, Chunk/ToolCallDelta/Response, StreamAccumulator, with_retry_stream
│   └── tool.py      Tool, ToolError, ToolRegistry, ERROR_PREFIX/TIMEOUT_PREFIX/DENIED_PREFIX
├── host/        the layer an application embeds — no terminal vocabulary
│   ├── runtime.py   MoCode — the runtime an application drives
│   ├── command.py   Command, CommandRegistry, CommandResult, Kind (invocable actions)
│   ├── frontend.py  Frontend protocol: info / warn / error / conversation_changed
│   ├── config.py    Config, ProviderEntry, ModelEntry (+ plugins section)
│   ├── session.py   Session, SessionStore, SessionManager
│   ├── export.py    session → Markdown
│   ├── text.py      visible_width, ellipsize_*, terminal_width, count_visual_lines, decode_bytes
│   ├── io.py        read_json, write_json
│   ├── prompt.py    build_system_prompt(ctx)
│   └── plugin/
│       ├── base.py      Plugin
│       ├── context.py   HostContext
│       ├── loader.py    discover / load_plugin / parse_frontmatter
│       ├── host.py      PluginHost, builtin_plugins
│       └── builtin/     filesystem, shell, skills  ← the host's shipped plugins
├── cli/         the terminal front-end — a consumer of host/
│   ├── app.py       CLIApp — REPL, dispatch, Ctrl-C
│   ├── plugin.py    the terminal's plugin (its commands)
│   ├── hook.py      CLIDisplayHook — renders the event stream
│   ├── lines.py     Line + builders — what a turn looks like, as data
│   ├── display.py   Display (primitives + Frontend), theme.py, input.py, dialogs.py
│   └── commands/    /quit /help /clear /copy /model /export /resume
├── providers/openai.py  OpenAI-compatible streaming provider (lazy SDK import)
├── plugins/__init__.py  public SDK: Plugin, HostContext, Tool, AgentHook, Command, Section, ...
├── examples/plugins/    a complete example plugin
└── cli_args.py, main.py
```

Dependencies point down only — `core ← host ← cli`. `core` and `providers` import nothing above them; `host` never imports `cli`.

### Kernel primitives that enable plugins

| Primitive | Where | Unlocks |
|---|---|---|
| `AgentLoop.stream()` → `AsyncIterator[Event]` | `core/agent.py`, `core/events.py` | one output contract for every consumer: terminal renderer, plugin, test, embedding app. `RunState` is the same stream folded into a queryable snapshot |
| `AgentLoop.derive(*, system_prompt, tools, hooks, config, model)` | `core/agent.py` | sub-agents, workflow-style nodes — any nested agent with narrower tools |
| `ModelSpec(name, context_window, max_output)` | `core/provider.py` | model facts as first-class data: the loop passes `max_output` to the provider (`None` = send no cap), plugins read `ctx.model.context_window` to budget context |
| `Provider.stream(...)` → `AsyncIterator[Chunk]` | `core/provider.py` | real incremental output; a non-streaming backend yields one chunk instead of a second code path |
| Event channel: `await ctx.emit(event)` → the stream + `AgentHook.on_event` | `core/hook.py`, `core/agent.py` | a plugin telling the host something happened without the kernel knowing the concept |
| `Event.summary()` | `core/events.py` | a frontend shows any event — including one a plugin defined later — with no renderer registry to keep in sync |
| `ToolResult(content, details)` | `core/tool.py` | the model's channel and everyone else's kept apart: `content` enters the conversation, `details` reaches `ToolCallFinished` and `RunState` and stops there |
| Interception: `ctx.deny`, writable `ctx.tool_args` / `ctx.tool_result` / `ctx.tool_details`, `ctx.messages` / `ctx.system_prompt`, structured `ctx.status` | `core/hook.py` | permission gates, sandboxes, redaction, result enrichment, per-application personas |
| `Tool(tags=..., summary_key=..., result_key=...)`, `ToolRegistry.select(...)` | `core/tool.py` | capability scoping by tag instead of hard-coded name lists; display summaries without a central table |

### Where config values belong

| Value | Owner | Why |
|---|---|---|
| `context_window`, `max_output`, `extra_body` | **model** (`providers.<p>.models.<m>`) | physical properties of the model; absence means "unknown", never a guessed default |
| `tool_timeout`, `max_iterations` | **`agent` block** | host execution policy, identical whatever model is loaded |
| `tool_result_limit` | **`AgentConfig` in core** | a loop-internal safety valve, deliberately not user-facing (default 50k chars) |
| `plugins.<name>` | **plugin** | read through `ctx.plugin_config(name)` |
| unknown top-level keys | **whoever wrote them** | preserved verbatim across load → save (`Config.foreign`) |

API keys resolve as `api_key` → `$<PROVIDER_KEY>_API_KEY` (see `config.env_var_for`); nothing to declare in the file. MoCode never writes a `max_tokens` it made up — an unset `max_output` means the request carries no cap.

### Assembly

```python
ctx = HostContext(home=..., cwd=..., config=..., display=...)
agent = PluginHost(ctx, extra_plugins=[...]).run(provider=..., config=AgentConfig(...))
```

`display` is the frontend, or `None` when headless — the only signal for "something is watching". It is typed as the `Frontend` protocol, never as a terminal. There is no separate `interactive` flag.

`extra_plugins` is how a frontend brings its own contributions: the CLI renders a terminal, so it passes `cli/plugin.py` in rather than `builtin_plugins()` knowing about one.

`PluginHost.load()` → built-ins (fixed order) + `extra_plugins` + `./.mocode/plugins/` + `~/.mocode/plugins/`, skipping reserved names. `build_all()` runs each `build(ctx)` inside try/except. `assemble()` builds the prompt from the finished tool/skill set, constructs the loop and assigns `ctx.agent`. A plugin needing the agent reads `ctx.agent` at call time — never capture it in `build()`.

## Hard Invariants

1. `core/` never imports `host/`, `cli/`, `providers/` or `plugins/`, and contains no specific tool name, feature name or config key beyond `AgentConfig`.
2. `host/` never imports `cli/`, and contains no terminal vocabulary — no ANSI, no prompt, no screen. A plugin reaches a user-facing display only through `Frontend` + events.
3. Exactly one way to build an agent: the `Agent` builder or `AgentLoop.derive()`. Never a third hand-rolled `AgentLoop(...)`.
4. Exactly one way to execute a turn: `AgentLoop.stream()`. `chat()` is a wrapper over it, and nothing else gets its own path through the loop.
5. Observation goes through the event stream; anything that must *answer* (rewrite messages or the system prompt, veto a call, redact a result) is an `AgentHook`. Do not add a second way to watch the loop, and do not make a notification wait for a reply.
6. What the model reads and what a UI shows are different channels: `ToolResult.content` vs `.details`, `ToolCallFinished.result` vs `.details`. Never make a frontend parse model-facing text to render something.
7. Tools, commands, hooks and prompt sections are contributed only through `Plugin.build(ctx)` — the host hard-codes none, and the terminal is not an exception. A frontend installing its own renderer on the agent it built is not a contribution: drawing a terminal is what the frontend does with the event stream, never an extension point (`CLIApp` adds `CLIDisplayHook` itself).
8. No string-protocol parsing between layers: outcomes are carried by `ctx.status`, notifications by typed events.
9. No central tables of tool names to keep in sync — the `ToolRegistry` and `Tool` metadata are the source of truth.
10. No central registry of event types to keep in sync either — an event renders itself through `summary()`.

## Code Conventions

- `from __future__ import annotations` in every module.
- **Dataclasses over Pydantic**; serialization is manual `to_dict()` / `from_dict()`. `Config.from_dict` passes through every declared field generically — adding a field to the dataclass is enough.
- **Async-first**: `AgentLoop.stream()` and all hooks are async. Sync tools run via `asyncio.to_thread`; a tool that needs to stream must be async, because a worker thread cannot await `ctx.emit`.
- **TYPE_CHECKING guards** for types used only in annotations.
- **No global state**: dependencies are constructor-injected; `MoCode` is the composition root and `CLIApp` is a terminal in front of it.
- **Tool params** are `dict[str, dict]` with `type`, `description`, optional `default` / `optional` — not JSON Schema.
- **Heavy imports stay lazy**: `openai` (inside `OpenAIProvider._ensure_client`), `questionary` (`cli/dialogs.py` resolves it on first use), `prompt_toolkit` (inside `Input` methods), and `MoCode` itself (PEP 562 `__getattr__` in `mocode/__init__.py`). `import mocode` must stay under a millisecond.
- **Standard library first**: `urllib.request` over `httpx`, `subprocess` over shell wrappers. Runtime deps are `openai`, `pyyaml`, `prompt-toolkit`, `questionary`, `pyperclip`, `wcwidth`.

## Testing Patterns

- **`MockProvider`** (`tests/providers.py`) replays canned `Response` objects as chunk streams, splitting tool-call arguments across chunks the way a real API does. `chunk_size=1` makes the incremental path visible.
- **Plugin fixtures**: write a `PLUGIN.md` + `plugin.py` into `tmp_path` and point `PluginHost(ctx, plugin_dirs=[...])` at it (`tests/test_plugins.py`).
- **Nothing touches the real `~/.mocode`** — point `SessionStore` at `tmp_path` (`tests/test_runtime.py` does this with `monkeypatch.setattr`).
- **Display capture**: override `display.print` with a list append.
- **Async tests**: `@pytest.mark.asyncio` (no bare `async def test_`).
- **Plain pytest classes**, no `unittest.TestCase`.

## Things that are deliberately absent

Do not re-add these to `core/` without re-reading the rule at the top:

- Context compaction — belongs in a hook that rewrites `ctx.messages` and emits an event (see `docs/plugins.md` for a worked example).
- Sub-agents — a tool built on `derive()`.
- Virtual filesystem — skills return their real `base_dir`; the filesystem tools read it.
- Workflow / DAG engine — a plugin.
- Permission prompts — an `on_tool_start` hook that sets `ctx.deny`.
