# MoCode — Development Guide

For contributors to the framework itself. Users are in
[README.md](README.md); plugin authors are in [docs/plugins.md](docs/plugins.md);
embedders are in [docs/embedding.md](docs/embedding.md).

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
   `chat()` are views over it, `run_with_messages()` is its convenience
   wrapper for a pre-built message list, and nothing else gets its own path
   through the loop. One conversation runs one turn at a time; a second
   `start()` raises rather than interleaving two histories.
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

## The plugin lifecycle

`build(ctx)` registers — cheap, synchronous, no I/O. `prepare(ctx)` (async,
optional) finishes — discovery, connections, anything slow — before the request
surface (system prompt + offered tool interface) is materialized once, at the
top of the first turn or byte-identically from a resumed session.
`PluginHost.materialize()` is the one place that surface is written. The full
contract, the two context types and the escape hatches are
[docs/plugins.md](docs/plugins.md#the-two-stages-buildctx-and-preparectx).

## Where config values belong

The table of which value is owned by which entry — and what an absent value
means — is the Configuration section of
[README.md](README.md#configuration); provider authors keep their own copy of
the keys they read in [docs/providers.md](docs/providers.md). The
contributor-relevant part is one line: the `agent` block *is* the core
`AgentConfig` (nested-serialized; unknown subkeys ignored), so a field is
configurable the moment it exists — no mapping to keep in sync.

## Code Conventions

- `from __future__ import annotations` in every module.
- Dataclasses over Pydantic; serialization is manual `to_dict()` / `from_dict()`.
- Async-first: `start()` and all hooks are async; sync tools run via
  `asyncio.to_thread` — a tool that needs to stream must be async, and
  `tool_timeout` is cooperative for sync tools (they check `ctx.cancelled`).
- `TYPE_CHECKING` guards for types used only in annotations.
- No global state: dependencies are constructor-injected. `MoCode` is the
  composition root, `Conversation` is the unit an application holds.
- Tool arguments are declared as a JSON Schema object node (`Tool(schema=...)`),
  validated by the built-in dependency-free checker (unknown keywords pass).
- Standard library first; runtime deps are `openai`, `pyyaml`,
  `prompt-toolkit`, `questionary`, `pyperclip`, `wcwidth`.

## Adding a hook point

Nothing enforces that these stay in sync — that is exactly why the list is
here. A new interception point on `AgentHook` touches four places, in order:

1. the method on `AgentHook` (`core/hook.py`) — signature plus a docstring
   saying what may be rewritten;
2. the wrapper on `HookRunner` (`core/hook.py`) that fans it out with error
   isolation;
3. the export in `mocode/plugins/__init__.py` — a plugin author writes
   against the SDK, not against `mocode.core`;
4. the hook table in [docs/plugins.md](docs/plugins.md#hooks).

Run the checklist both directions: adding without exporting hides the hook
from every plugin author; exporting without documenting leaves it unusable.

## Adding an event class

Same discipline, its own four places:

1. the subclass in `core/events.py` — a unique `type` discriminator and a
   `summary()`, so any frontend can show it without knowing the type;
2. the exports — `mocode/core/__init__.py` (the kernel's surface) and
   `mocode/plugins/__init__.py` (the plugin SDK);
3. the event table in [docs/embedding.md](docs/embedding.md#the-event-stream);
4. the `events.py` line in the module map of
   [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), which counts them, and the
   run-lifecycle diagram if the event is part of the turn's shape.

## Adding a builtin plugin

1. the class in `host/plugin/builtin/<name>.py`, with a module-level `PLUGIN`
   — `build()` cheap, `prepare()` for the I/O, `close()` for what you acquired;
2. the entry in `builtin_plugins()` (`host/plugin/host.py`) — the fixed,
   prompt-stable order, and the name every reserved-name check reads;
3. the reserved-name list in
   [docs/plugins.md](docs/plugins.md#rules-of-the-road) and the built-in
   table in [README.md](README.md#built-ins), so the names stay visible to
   plugin authors and users.

## Testing Patterns

Tests script the model: `MockProvider` (`mocode/testing/providers.py`) replays
canned `Response` objects as chunk streams, splitting tool-call arguments the
way a real API does — and its **last response repeats forever**, so a script
that ends on a tool call never finishes; end on a `say(...)`. `tests/conftest.py`
supplies the fixtures (`make_mc`, `wired`, `write_plugin`, `plugin_host`,
`run_command`, …), and nothing touches the real `~/.mocode`. The details, the
readers and the conventions are in
[docs/testing.md](docs/testing.md).

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
| [docs/testing.md](docs/testing.md) | testing a plugin against a scripted model |
| [docs/api.md](docs/api.md) | the public surface, layer by layer |
| [examples/core/](examples/core) | agents built from `core/` alone, runnable |
| [examples/plugins/git-status/](examples/plugins/git-status) | a complete plugin, both surfaces |
| [examples/plugins/json-validate/](examples/plugins/json-validate) | a plugin with its own environment (a dependency via uv) |
| [examples/plugins/multi-file/](examples/plugins/multi-file) | a plugin organized as a package, submodules included |
