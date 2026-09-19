# MoCode — Development Guide

## Commands

```bash
uv sync                    # install (uv + hatchling) — always uv for Python
uv run pytest              # all tests
uv run pytest tests/test_agent_loop.py               # one file
uv run pytest tests/test_plugins.py::TestPluginHost  # one class
```

## The One Rule

> **A concept that could be written as a plugin does not belong in `core/`.**

`core/` is mechanism: run an LLM loop with tools, hooks and a prompt, and make
it observable. Everything else — workflows, sub-agents, context compaction, web
fetch, the virtual file system — is a capability and has been removed from this
codebase for that reason. If you are tempted to add an `if` for a specific
tool, hook or feature inside `core/`, write a plugin instead.

## Layering

Dependencies point down only — `core ← host ← cli`.

- `core/` — the kernel. Imports nothing above it; contains no tool name,
  feature name or config key beyond `AgentConfig`.
- `host/` — the layer an application embeds: runtime, conversations, config,
  sessions, plugins. Never imports `cli/`; contains no terminal vocabulary —
  no ANSI, no prompt, no screen.
- `cli/` — the terminal front-end, a consumer of `host/` like any other.
  Its renderer is a subscription, not a special case in the loop.
- `providers/` — Provider implementations. `plugins/` (the package) — the
  public SDK third-party plugins import.

The module map and the reasoning per layer are in
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Hard Invariants

1. Exactly one way to build an agent: the `AgentLoop` constructor or
   `AgentLoop.derive()`. No builder on top, no second assembly site.
2. Exactly one way to execute a turn: `AgentLoop.start()`. `stream()` and
   `chat()` are views over it, and nothing else gets its own path through the
   loop. One conversation runs one turn at a time; a second `start()` raises
   rather than interleaving two histories.
3. Observation goes through the event stream — the channel is the only way
   anything learns what a run did. Anything that must *answer* (rewrite
   messages or the system prompt, veto a call, redact a result) is an
   `AgentHook`. A hook's `on_event` is the in-band subscriber (the publisher
   waits for it); every other reader is out-of-band and cannot slow the run.
4. What the model reads and what a UI shows are different channels:
   `ToolResult.content` vs `.details`, `ToolCallFinished.result` vs `.details`.
   Never make a frontend parse model-facing text to render something.
5. Contributions to the agent — tools, prompt sections, hooks, shared commands
   — are made only through `Plugin.build(ctx)`, and the host hard-codes none of
   them. Contributions to a *frontend* go through that frontend's own interface
   (`CLIPlugin`), never through the host.
6. No string-protocol parsing between layers: outcomes are carried by
   `ctx.status` and typed events.
7. No central tables to keep in sync: the `ToolRegistry` and `Tool` metadata
   are the source of truth for tools; an event renders itself through
   `summary()`, so there is no renderer registry either.
8. A plugin *instance* is stateless: `build(ctx)` runs once per conversation
   and is the only place to create state. State on `self` is shared by every
   conversation in the process.
9. Nothing writes config.json except `MoCode.set_default_model()` — a
   conversation switching models is a decision about that conversation.
10. `import mocode` stays under a millisecond (PEP 562 `__getattr__`); heavy
    imports (`openai`, `questionary`, `prompt_toolkit`) resolve on first use.

## Where config values belong

| Value | Owner |
|---|---|
| `type` | provider entry (`providers.<p>`) — which implementation builds it; registered via `MoCode.register_provider_type()`, absent means the built-in `openai` |
| `context_window`, `max_output`, `extra_body` | model entry (`providers.<p>.models.<m>`) — physical properties; absence means "unknown", never a guessed default |
| `tool_timeout`, `max_iterations` | `agent` block — host execution policy, identical whatever model is loaded |
| `tool_result_limit` | `AgentConfig` in core — a loop-internal safety valve, deliberately not user-facing (default 50k chars) |
| `plugins.<name>` | the plugin, read through `ctx.plugin_config(name)` |
| `active_provider`, `active_model` | the config file, as a *default* for new conversations; only `MoCode.set_default_model()` writes the file |
| unknown top-level keys | whoever wrote them — preserved verbatim across load → save (`Config.foreign`) |

API keys resolve as `api_key` → `$<PROVIDER_KEY>_API_KEY` (`config.env_var_for`);
nothing to declare in the file. An unset `max_output` means the request carries
no output cap — MoCode never invents one.

## Code Conventions

- `from __future__ import annotations` in every module.
- Dataclasses over Pydantic; serialization is manual `to_dict()` / `from_dict()`.
- Async-first: `start()` and all hooks are async; sync tools run via
  `asyncio.to_thread` — a tool that needs to stream must be async, and
  `tool_timeout` is cooperative for sync tools (they check `ctx.cancelled`).
- `TYPE_CHECKING` guards for types used only in annotations.
- No global state: dependencies are constructor-injected. `MoCode` is the
  composition root, `Conversation` is the unit an application holds.
- Tool params are `dict[str, dict]` with `type`, `description`, optional
  `default` / `optional` — not JSON Schema.
- Standard library first; runtime deps are `openai`, `pyyaml`,
  `prompt-toolkit`, `questionary`, `pyperclip`, `wcwidth`.

## Testing Patterns

- `MockProvider` (`tests/providers.py`) replays canned `Response` objects as
  chunk streams, splitting tool-call arguments the way a real API does.
  Remember: its **last response repeats forever** — end a tool-call script
  with a plain answer or the turn never finishes.
- Plugin fixtures: write `plugin.json` + `mocode/plugin.py` (and optionally
  `mocode.cli/plugin.py`) into `tmp_path`, point `load_plugins(plugin_dirs=[...])`
  at it.
- Nothing touches the real `~/.mocode` — `MoCode(home=tmp_path / "home")`.
- A conversation is cheap: `mc.new_conversation(cwd=tmp_path)`, then replace
  `conversation.agent.provider` with a `MockProvider`.
- Commands publish, they do not print: collect what a handler said by
  subscribing to the conversation and draining it.
- Async tests need `@pytest.mark.asyncio`; plain pytest classes, no
  `unittest.TestCase`; display capture overrides `display.print` with a list
  append.

## Deliberately absent from core

Do not re-add these without re-reading the rule at the top — each belongs in a
plugin, and [docs/plugins.md](docs/plugins.md) shows the worked version:

- context compaction — a hook that rewrites `ctx.messages` and emits an event;
- sub-agents — a tool built on `derive()`;
- virtual filesystem — skills return their real `base_dir`;
- workflow / DAG engine — a plugin;
- permission prompts — an `on_tool_start` hook that sets `ctx.deny`.

## The rest of the documentation

| Doc | For |
|---|---|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | the module map and how the layers reason |
| [docs/embedding.md](docs/embedding.md) | embedding MoCode in an application |
| [docs/plugins.md](docs/plugins.md) | writing a plugin |
| [docs/providers.md](docs/providers.md) | the Provider protocol, writing a provider |
| [examples/core/](examples/core) | agents built from `core/` alone, runnable |
| [examples/plugins/git-status/](examples/plugins/git-status) | a complete plugin, both surfaces |
