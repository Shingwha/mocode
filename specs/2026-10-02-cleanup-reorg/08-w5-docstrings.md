✅ 2026-10-02 @ cleanup/w5-docstrings

# Spec W5-S · Docstring completion (wave W5)

> Branch `cleanup/w5-docstrings`; worktree `C:\Users\shifu\.worktrees\mocode\cleanup-w5-docstrings`.
> Prerequisite: W4 merged. Runs in parallel with W5-D — **file scopes are
> disjoint**: you own `mocode/**` source only; never edit README.md,
> AGENTS.md, `docs/`, `examples/`, `tests/`.

## Goal

Every public class, method and function in `mocode/` carries a docstring
that says what it does, in the project's existing prose style — **no**
`Args:`/`Returns:` structured sections (match the neighbors). This wave
closes the API-documentation gap from the inside; W5-D writes the map, you
write the terrain.

## Verified gaps (fill these first, re-verify against the tree)

- `mocode/core/tool.py` — `ToolRegistry.unregister/get/all/enable/disable`,
  `Tool.run_async`.
- `mocode/core/prompt.py` — `Prompt.register/unregister/get/enable/disable`.
- `mocode/core/hook.py` — the `HookRunner` fan-out wrappers (7 methods);
  one line each is fine, they delegate.
- `mocode/core/provider.py` — `StreamAccumulator.feed` and its
  `text/reasoning/tool_calls` properties.
- `mocode/core/channel.py` — `Subscription.closed`, `Subscription.unsubscribe`.
- `mocode/core/turn.py` — `Turn.done`.
- `mocode/core/agent.py` — `LoopResult` (class docstring missing).
- `mocode/host/config.py` — `Config` class, `from_dict/to_dict/current`.
- `mocode/host/session.py` — `SessionStore.save/load/delete`, `Session`
  field semantics if undocumented.
- `mocode/host/conversation.py` — the `tools/commands/model/provider`
  properties.
- `mocode/host/plugin/context.py` — `HostContext.spawn/subscribe` deserve
  real docstrings (keep-list APIs: spawn = convenience sub-agent via
  `derive()`, no inherited hooks, optional shared channel; subscribe =
  out-of-band event tap with backpressure caveats — describe actual
  behavior, read the code).
- `mocode/host/plugin/host.py` — `PluginHost.failures` (what lands there).
- `mocode/host/plugin/loader.py` — `namespace_dir`.
- `mocode/host/plugin/builtin/skills.py` — `SkillManager.register`.
- `mocode/testing/providers.py` — `MockProvider` public members incl. the
  W3 additions (exception scripting, `retriable`, `last_request`),
  `SlowProvider`, `response_to_chunks`.
- `mocode/host/runtime.py` — `MoCode.resume`, `MoCode.plugins_for`.
- Sweep with a quick AST pass for any public symbol still undocumented
  after the above (respect `__init__` documented via class docstring style).

## Bug fix (same commit as the relevant file)

`mocode/__init__.py` module docstring (~line 19) shows
`async for event in conv.chat(...)` — wrong; `chat()` returns `str`. Fix to
`conv.stream(...)` matching the pattern in `mocode/host/__init__.py`.

## Constraints

- Docstrings only: no logic changes, no renames, no signature changes.
  (Fixing a docstring that *lies* about behavior is allowed and encouraged —
  note each one in the report.)
- Keep the existing voice: concise prose, one idea per paragraph, code
  identifiers in double backticks.
- `IterationContext.emit` / `RequestContext.emit` fields: docstring them so
  the plugins.md promise is true in code.

## Gate

1. `uv sync`
2. `uv run pytest -q` — must stay green (docstring-only changes; fix any
   doctest-style breakage), exit code confirmed independently.

## Final report format

List of symbols documented (file → symbol) / commit list / docstrings that
were wrong about behavior and corrected / gate result with real exit code /
deviations & trade-offs / open questions. Blocked = stop and report.
