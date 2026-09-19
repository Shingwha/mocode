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
│   ├── agent.py     AgentConfig, AgentLoop (start/stream/chat/derive), LoopResult
│   ├── turn.py      Turn — one execution of the loop, addressable and watchable
│   ├── channel.py   EventChannel, Subscription — where a turn's events go
│   ├── events.py    Event + the eleven events a run emits
│   ├── state.py     RunState — the events folded into a live snapshot
│   ├── hook.py      AgentHook, HookRunner, IterationContext, ToolCallContext
│   ├── prompt.py    Prompt, Section — section-based XML assembly
│   ├── provider.py  Provider protocol, Chunk/ToolCallDelta/Response, StreamAccumulator, with_retry_stream
│   └── tool.py      Tool, ToolError, ToolRegistry, ERROR_PREFIX/TIMEOUT_PREFIX/DENIED_PREFIX
├── host/        the layer an application embeds — no terminal vocabulary
│   ├── runtime.py       MoCode — the process runtime: config, plugins, sessions
│   ├── conversation.py  Conversation — one project, one model, one history, one stream
│   ├── events.py        ConversationChanged — the host's own events
│   ├── command.py       Command, CommandRegistry, CommandResult, dispatch()
│   ├── config.py        Config, ProviderEntry, ModelEntry (+ plugins section)
│   ├── session.py       Session, SessionStore, export/import helpers
│   ├── export.py        session → Markdown
│   ├── text.py          decode_bytes
│   ├── io.py            read_json, write_json
│   ├── prompt.py        build_system_prompt(ctx)
│   └── plugin/
│       ├── base.py      Plugin (build/close)
│       ├── context.py   HostContext
│       ├── loader.py    discover / load_plugin / read_manifest / namespace_dir
│       ├── host.py      load_plugins, PluginHost, builtin_plugins
│       └── builtin/     filesystem, shell, skills  ← the host's shipped plugins
├── cli/         the terminal front-end — a consumer of host/
│   ├── app.py       CLIApp — REPL, dispatch, Ctrl-C
│   ├── plugin.py    CLIPlugin + this terminal's own commands (its namespace: mocode.cli)
│   ├── render.py    CLIRenderer — subscribes to the event stream
│   ├── lines.py     Line + builders — what a turn looks like, as data
│   ├── display.py   Display (terminal primitives), theme.py, text.py, input.py, dialogs.py
│   └── commands/    /quit /help /clear /copy /model /export /resume
├── providers/openai.py  OpenAI-compatible streaming provider (lazy SDK import)
├── plugins/__init__.py  public SDK: Plugin, HostContext, Tool, AgentHook, Command, Turn, ...
├── examples/plugins/    a complete example plugin (both surfaces)
└── cli_args.py, main.py
```

Dependencies point down only — `core ← host ← cli`. `core` and `providers` import nothing above them; `host` never imports `cli`.

### Kernel primitives that enable plugins

| Primitive | Where | Unlocks |
|---|---|---|
| `AgentLoop.start()` → `Turn` | `core/agent.py`, `core/turn.py` | a run that belongs to the conversation, not to the caller: addressable, cancellable, waitable, and watchable by several readers. `stream()` / `chat()` are views over it |
| `EventChannel` / `Subscription` | `core/channel.py` | one ordered stream per conversation, with `seq` that survives across turns: fan-out to N readers, replay after a reconnect, and a place for a message that has nothing to do with a run |
| `AgentLoop.derive(*, system_prompt, tools, hooks, config, model, channel)` | `core/agent.py` | sub-agents, workflow-style nodes — any nested agent with narrower tools; pass `channel=` to report into the parent's stream |
| `ModelSpec(name, context_window, max_output)` | `core/provider.py` | model facts as first-class data: the loop passes `max_output` to the provider (`None` = send no cap), plugins read `ctx.model.context_window` to budget context |
| `Provider.stream(...)` → `AsyncIterator[Chunk]` | `core/provider.py` | real incremental output; a non-streaming backend yields one chunk instead of a second code path |
| `await ctx.emit(event)` | `core/hook.py`, `core/agent.py` | a plugin telling the host something happened without the kernel knowing the concept — inside a run or between runs |
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
| `active_provider`, `active_model` | **the config file, as a default** | which model a *new* conversation starts on; a running conversation has its own, and only `MoCode.set_default_model()` writes the file |
| unknown top-level keys | **whoever wrote them** | preserved verbatim across load → save (`Config.foreign`) |

API keys resolve as `api_key` → `$<PROVIDER_KEY>_API_KEY` (see `config.env_var_for`); nothing to declare in the file. MoCode never writes a `max_tokens` it made up — an unset `max_output` means the request carries no cap.

### Assembly

```python
mc = MoCode(config=..., home=..., plugin_dirs=...)      # process: config, plugins, sessions
conv = mc.new_conversation(cwd=..., provider=..., model=..., commands=...)   # one conversation
```

`new_conversation` builds a fresh `HostContext` (`cwd` = the project), runs every loaded plugin's `build(ctx)` against it, assembles the system prompt from the finished tools and sections, and constructs the loop. Everything a conversation owns — tool instances, the shell session, the skill index, the command registry, the channel — is created here, which is what keeps two conversations in one process from sharing anything.

`load_plugins(plugin_dirs=..., config=...)` is the per-*project* half: discovery, import and the enabled check, cached by `MoCode.plugins_for()`/`plugin_sources_for()`. A plugin needing the agent reads `ctx.agent` at call time — never capture it in `build()`.

### The plugin layout is the standard's

A plugin directory follows [Agent Plugins](https://agent-plugins.org): `plugin.json` at the root, portable components (`skills/<n>/SKILL.md`, `mcp.json`) beside it, and client-specific code under a namespace directory named for whoever defines it.

| Path | Read by | Contributes |
|---|---|---|
| `plugin.json` | any client | identity — `name` (required), `version`, `description`, …; the schema is closed, so unknown top-level fields are reported and ignored |
| `skills/<n>/SKILL.md` | any client | skills, discovered by the `skills` plugin through `ctx.plugin_sources` |
| `mcp.json` | any client | MCP servers — recognised, not served yet |
| `mocode/plugin.py` | MoCode, every frontend | `Plugin.build(ctx)`: tools, prompt sections, hooks, shared commands |
| `mocode.cli/plugin.py` | the terminal only | `CLIPlugin.build(cli)`: chrome — picker commands, keybindings — reaching `cli.commands` / `cli.display` / `cli.input` / `cli.conversation` |

A single `<name>.py` file is the shortcut for a MoCode-only plugin. The host hands namespace directories over without reading them (`PluginSpec.directory`, `ctx.plugin_sources`), so `host/` still contains no terminal vocabulary.

## Hard Invariants

1. `core/` never imports `host/`, `cli/`, `providers/` or `plugins/`, and contains no specific tool name, feature name or config key beyond `AgentConfig`.
2. `host/` never imports `cli/`, and contains no terminal vocabulary — no ANSI, no prompt, no screen. An application sees a conversation only as events, and a plugin never learns what is drawing them.
3. Exactly one way to build an agent: the `AgentLoop` constructor or `AgentLoop.derive()`. No builder on top, no second assembly site.
4. Exactly one way to execute a turn: `AgentLoop.start()`. `stream()` and `chat()` are views over it, and nothing else gets its own path through the loop. One conversation runs one turn at a time; a second `start()` raises rather than interleaving two histories.
5. Observation goes through the event stream — the channel is the only way anything learns what a run did. Anything that must *answer* (rewrite messages or the system prompt, veto a call, redact a result) is an `AgentHook`. A hook's `on_event` is the in-band subscriber (the publisher waits for it); every other reader is out-of-band and cannot slow the run down.
6. What the model reads and what a UI shows are different channels: `ToolResult.content` vs `.details`, `ToolCallFinished.result` vs `.details`. Never make a frontend parse model-facing text to render something.
7. Contributions to the agent — tools, prompt sections, hooks, shared commands — are made only through `Plugin.build(ctx)`, and the host hard-codes none of them. Contributions to a *frontend* go through that frontend's own interface (`CLIPlugin`), never through the host: a frontend installing its renderer on the conversation it built is not a contribution, it is what a frontend does with the event stream.
8. No string-protocol parsing between layers: outcomes are carried by `ctx.status`, notifications by typed events.
9. No central tables of tool names to keep in sync — the `ToolRegistry` and `Tool` metadata are the source of truth.
10. No central registry of event types to keep in sync either — an event renders itself through `summary()`.
11. A plugin *instance* is stateless: `build(ctx)` runs once per conversation and is the only place to create state. State on `self` is shared by every conversation in the process.
12. Nothing writes config.json except `MoCode.set_default_model()` — a conversation switching models is a decision about that conversation.

## Code Conventions

- `from __future__ import annotations` in every module.
- **Dataclasses over Pydantic**; serialization is manual `to_dict()` / `from_dict()`. `Config.from_dict` passes through every declared field generically — adding a field to the dataclass is enough.
- **Async-first**: `AgentLoop.start()` and all hooks are async. Sync tools run via `asyncio.to_thread`; a tool that needs to stream must be async, because a worker thread cannot await `ctx.emit`.
- **TYPE_CHECKING guards** for types used only in annotations.
- **No global state**: dependencies are constructor-injected; `MoCode` is the composition root, `Conversation` is the unit an application holds, and `CLIApp` is a terminal in front of one.
- **Tool params** are `dict[str, dict]` with `type`, `description`, optional `default` / `optional` — not JSON Schema.
- **Heavy imports stay lazy**: `openai` (inside `OpenAIProvider._ensure_client`), `questionary` (`cli/dialogs.py` resolves it on first use), `prompt_toolkit` (inside `Input` methods), and `MoCode` itself (PEP 562 `__getattr__` in `mocode/__init__.py`). `import mocode` must stay under a millisecond.
- **Standard library first**: `urllib.request` over `httpx`, `subprocess` over shell wrappers. Runtime deps are `openai`, `pyyaml`, `prompt-toolkit`, `questionary`, `pyperclip`, `wcwidth`.

## Testing Patterns

- **`MockProvider`** (`tests/providers.py`) replays canned `Response` objects as chunk streams, splitting tool-call arguments across chunks the way a real API does. `chunk_size=1` makes the incremental path visible.
- **Plugin fixtures**: write a `plugin.json` + `mocode/plugin.py` (and, for the terminal, `mocode.cli/plugin.py`) into `tmp_path` and point `load_plugins(plugin_dirs=[...])` at it (`tests/test_plugins.py`, `tests/test_cli_plugin.py`).
- **Nothing touches the real `~/.mocode`** — `MoCode(home=tmp_path / "home")` puts the session store inside the test (`tests/test_conversations.py`, `tests/test_runtime.py`).
- **A conversation is cheap in a test**: `mc.new_conversation(cwd=tmp_path)`, then replace `conversation.agent.provider` with a `MockProvider`.
- **Commands publish, they do not print**: collect what a handler said by subscribing to the conversation and draining it (`tests/test_commands.py::_run`).
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
