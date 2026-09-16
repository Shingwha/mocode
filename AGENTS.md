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
│   ├── agent.py     AgentConfig, LoopResult, AgentLoop (+ derive())
│   ├── builder.py   Agent — fluent builder
│   ├── hook.py      AgentHook, HookRunner, IterationContext, ToolCallContext, ToolTimingTracker
│   ├── prompt.py    Prompt, Section — section-based XML assembly
│   ├── provider.py  Provider protocol, Response/ToolCall/Usage, with_retry
│   └── tool.py      Tool, ToolError, ToolRegistry, ERROR_PREFIX/TIMEOUT_PREFIX/DENIED_PREFIX
├── app/
│   ├── config.py    Config, ProviderEntry, ModelEntry (+ plugins section)
│   ├── session.py   Session, SessionStore, SessionManager
│   ├── export.py    session → Markdown
│   ├── text.py      visible_width, ellipsize_*, terminal_width, count_visual_lines, decode_bytes
│   ├── io.py        read_json, write_json
│   ├── prompt.py    build_system_prompt(ctx)
│   ├── plugin/
│   │   ├── base.py      Plugin
│   │   ├── context.py   HostContext
│   │   ├── loader.py    discover / load_plugin / parse_frontmatter
│   │   ├── host.py      PluginHost, builtin_plugins
│   │   └── builtin/     filesystem, shell, skills, cli  ← the shipped plugins
│   └── cli/         app.py (CLIApp), display.py, hook.py, input.py, spinner.py,
│                    theme.py, dialogs.py, commands/{__init__,misc,model,session}.py
├── providers/openai.py  OpenAI-compatible provider (lazy SDK import, no streaming)
├── plugins/__init__.py  public SDK: Plugin, HostContext, Tool, AgentHook, Command, Section, ...
├── examples/plugins/    a complete example plugin
└── cli_args.py, main.py
```

### Kernel primitives that enable plugins

| Primitive | Where | Unlocks |
|---|---|---|
| `AgentLoop.derive(*, system_prompt, tools, hooks, config, model)` | `core/agent.py` | sub-agents, workflow-style nodes — any nested agent with narrower tools |
| `ModelSpec(name, context_window, max_output)` | `core/provider.py` | model facts as first-class data: the loop passes `max_output` to the provider (`None` = send no cap), plugins read `ctx.model.context_window` to budget context |
| Event channel: `await ctx.emit(event)` → `AgentHook.on_event` | `core/hook.py`, `core/agent.py` | a plugin telling the UI something happened without the kernel knowing the concept |
| Interception: `ctx.deny`, writable `ctx.tool_args` / `ctx.tool_result`, structured `ctx.status` | `core/hook.py` | permission gates, sandboxes, redaction, result post-processing |
| `Tool(tags=..., summary_key=...)`, `ToolRegistry.select(...)` | `core/tool.py` | capability scoping by tag instead of hard-coded name lists; display summaries without a central table |

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
ctx = HostContext(home=..., cwd=..., config=..., interactive=..., display=...)
agent = PluginHost(ctx).run(provider=..., config=AgentConfig(...))
```

`PluginHost.load()` → built-ins (fixed order) + `./.mocode/plugins/` + `~/.mocode/plugins/`, skipping reserved names. `build_all()` runs each `build(ctx)` inside try/except. `assemble()` builds the prompt from the finished tool/skill set, constructs the loop and assigns `ctx.agent`. A plugin needing the agent reads `ctx.agent` at call time — never capture it in `build()`.

## Hard Invariants

1. `core/` never imports `app/`, `providers/` or `plugins/`, and contains no specific tool name, feature name or config key beyond `AgentConfig`.
2. Exactly one way to build an agent: the `Agent` builder or `AgentLoop.derive()`. Never a third hand-rolled `AgentLoop(...)`.
3. Tools, commands, hooks and prompt sections are contributed only through `Plugin.build(ctx)` — the host hard-codes none.
4. No string-protocol parsing between layers: outcomes are carried by `ctx.status`, UI notifications by events.
5. No central tables of tool names to keep in sync — the `ToolRegistry` and `Tool` metadata are the source of truth.

## Code Conventions

- `from __future__ import annotations` in every module.
- **Dataclasses over Pydantic**; serialization is manual `to_dict()` / `from_dict()`. `Config.from_dict` passes through every declared field generically — adding a field to the dataclass is enough.
- **Async-first**: `AgentLoop.chat()` and all hooks are async. Sync tools run via `asyncio.to_thread`.
- **TYPE_CHECKING guards** for types used only in annotations.
- **No global state**: dependencies are constructor-injected; `CLIApp` is the composition root.
- **Tool params** are `dict[str, dict]` with `type`, `description`, optional `default` / `optional` — not JSON Schema.
- **Heavy imports stay lazy**: `openai` (inside `OpenAIProvider._ensure_client`), `questionary` (`app/cli/dialogs.py` resolves it on first use), `prompt_toolkit` (inside `Input` methods). Startup depends on this — keep the whole app importing and building under ~150 ms.
- **Standard library first**: `urllib.request` over `httpx`, `subprocess` over shell wrappers. Runtime deps are `openai`, `pyyaml`, `prompt-toolkit`, `questionary`, `pyperclip`, `wcwidth`.

## Testing Patterns

- **`MockProvider`** with canned `Response` objects drives deterministic loop tests (`tests/test_agent_loop.py`).
- **Plugin fixtures**: write a `PLUGIN.md` + `plugin.py` into `tmp_path` and point `PluginHost(ctx, plugin_dirs=[...])` at it (`tests/test_plugins.py`).
- **CLI tests** stub `mocode.app.cli.app.PluginHost.run` rather than assembling for real.
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
