# Architecture

MoCode is a small agent kernel plus a plugin host. The organising rule is simple:

> **A concept that could be written as a plugin does not belong in the kernel.**

Workflows, sub-agents, context compaction, web fetch, virtual file systems — all of these are capabilities, so none of them live in `core/`. The kernel knows how to run an LLM loop with tools, hooks and a prompt; everything else arrives through the plugin API.

```
mocode/
├── core/                the kernel — mechanism only, no app dependencies
│   ├── agent.py         AgentConfig, LoopResult, AgentLoop (incl. derive())
│   ├── builder.py       Agent — fluent builder, the embedding entry point
│   ├── hook.py          AgentHook, HookRunner, contexts
│   ├── prompt.py        Prompt, Section — section-based XML assembly
│   ├── provider.py      Provider protocol, Response/ToolCall/Usage, with_retry
│   └── tool.py          Tool, ToolError, ToolRegistry
├── app/                 the host — config, persistence, CLI, plugin framework
│   ├── config.py        Config, ProviderEntry, ModelEntry
│   ├── session.py       Session, SessionStore, SessionManager
│   ├── export.py        Session → Markdown
│   ├── text.py io.py    terminal text helpers, JSON I/O
│   ├── prompt.py        build_system_prompt(ctx) — framework sections + plugin sections
│   ├── plugin/          Plugin, HostContext, PluginHost, loader
│   │   └── builtin/     the four plugins MoCode ships: filesystem, shell, skills, cli
│   └── cli/             REPL, Display, Input, Spinner, Theme, dialogs, commands
├── providers/openai.py  OpenAI-compatible provider (streaming is not implemented)
├── plugins/__init__.py  the public SDK third-party plugins import
├── cli_args.py main.py  argument parsing and process entry
```

## Layering rules

| Rule | Why |
|---|---|
| `core/` never imports `app/`, `providers/` or `plugins/` | the kernel stays embeddable and testable without a CLI |
| `core/` contains no tool names, no feature names, no config keys beyond `AgentConfig` | anything feature-specific is a plugin's business |
| There is exactly one way to construct an agent — the `Agent` builder or `AgentLoop.derive()` | three hand-rolled construction sites was how the old code drifted |
| Tools, commands, hooks and prompt sections are all contributed through `Plugin.build(ctx)` | the host hard-codes none of them |

## The kernel

`AgentLoop` runs the conversation loop: call the provider, run any tool calls in parallel, append results, repeat until the model stops asking for tools. Dependencies (`provider`, `system_prompt`, `tools`, `hooks`, `config`) are constructor-injected; `provider`, `system_prompt` and `messages` are public and mutable, everything else is read through properties.

Four primitives make features-as-plugins possible:

- **`AgentLoop.derive(*, system_prompt, tools, hooks, config)`** — creates an independent agent sharing this one's provider. This is the whole basis of sub-agents, workflow nodes, or any "run a nested agent with narrower tools" idea.
- **An event channel** — hooks call `await ctx.emit(event)`. The loop only transports events and never inspects their types; a host-side renderer decides how they look. This is how a plugin tells the UI something happened without the kernel knowing what compacting is.
- **Interceptable hooks** — `on_tool_start` may rewrite `ctx.tool_args` or set `ctx.deny` to veto a call; `on_tool_complete` may rewrite `ctx.tool_result`. `ctx.status` records the outcome (`ok` / `error` / `timeout` / `denied` / `not_found`) so no one parses result strings.
- **Tool metadata** — `Tool(tags=..., summary_key=...)` plus `ToolRegistry.select(...)`. Capability scoping is by tag (`exclude_tags={"delegation"}`), never by a hard-coded list of tool names.

## Hook lifecycle

```
chat(input)
  └─ loop
      ├─ before_iteration(ctx)          ctx.messages is writable; ctx.emit(...)
      ├─ provider.call(...)             retried by with_retry
      ├─ on_response(ctx)               usage, reasoning, final content
      ├─ per tool call (parallel):
      │     on_tool_start(ctx)          rewrite args / deny
      │     [execute]
      │     on_tool_complete(ctx)       rewrite result, read status
      ├─ after_tools(ctx)               after the batch
      └─ after_iteration(ctx)           when the loop ends
```

`HookRunner` fans out with per-hook error isolation: a raising hook is logged and skipped, never fatal.

## The host

`CLIApp` is a thin composition root:

```python
ctx = HostContext(home=..., cwd=..., config=..., interactive=..., display=...)
agent = PluginHost(ctx).run(provider=..., config=AgentConfig(...))
```

`PluginHost` discovers the built-in plugins plus anything in `./.mocode/plugins/` and `~/.mocode/plugins/`, runs each `build(ctx)` inside a try/except, builds the system prompt from the now-complete tool/skill set, and constructs the agent. `ctx.agent` is assigned last — plugins that need the agent keep the context and read `ctx.agent` at call time, which is what removes the old two-phase assembly.

The system prompt is assembled from framework sections (`guidelines`, `agents`, `environment`, `tools`) plus `ctx.prompt_sections` contributed by plugins, rendered in `(priority, insertion order)` order so stable content stays in front of volatile content for prefix caching.

The `cli` plugin is the only interactive-only one: it adds `CLIDisplayHook` and the `/model`, `/resume`, `/export`, `/clear`, `/copy`, `/help`, `/quit` commands. A non-interactive run never imports questionary or prompt_toolkit.

## Data flow

```
user input → CLIApp._dispatch ─┬─ slash command → handler → CommandResult
                               └─ prose → AgentLoop.chat()
                                            ├─ provider.call (with_retry)
                                            ├─ HookRunner hooks
                                            ├─ tool execution (asyncio.gather)
                                            └─ events → Display renderers
```

## Testing

- `MockProvider` with canned `Response` objects drives deterministic loop tests (`tests/test_agent_loop.py`).
- `tests/test_plugins.py` covers discovery, enable/disable, and error isolation for plugins; plugin fixtures are written to `tmp_path`.
- CLI tests stub `PluginHost.run` instead of reaching into the assembler.
