✅ 2026-10-02 @ cleanup/w1-dead-code

# Spec W1-A · Dead-code removal (wave W1)

> Branch `cleanup/w1-dead-code`; worktree `C:\Users\shifu\.worktrees\mocode\cleanup-w1-dead-code`.
> Read first: `specs/2026-10-02-cleanup-reorg/00-overview.md` (frozen lists are law).

## Goal

Remove exactly the symbols on the frozen **delete list** — nothing more, nothing
less. Every removal keeps `uv run pytest` green. Public behavior is otherwise
unchanged.

## Delete list (10 items, evidence cited)

1. `mocode/host/plugin/install.py` — in `_rmtree`, the `else` branch
   (`onerror=` handler). `pyproject.toml` declares `requires-python >= 3.12`,
   so the `sys.version_info >= (3, 12)` guard is always true and the `else` is
   unreachable. Collapse to the py3.12+ path.
2. `mocode/core/tool.py:18` — `ERROR_PREFIXES` tuple. Zero references anywhere;
   `mocode/cli/lines.py:227-231` keeps its own `_STATUS_BY_PREFIX` (that one
   stays — wave W2 may consolidate).
3. `mocode/core/agent.py:676-693` — `AgentLoop.iteration`, `AgentLoop.last_usage`,
   `AgentLoop.total_usage` properties. Redundant with `agent.state.*`; no
   callers, no docs.
4. `mocode/core/state.py:203,213` — `RunState.running_tool_calls`,
   `RunState.failed_tool_calls`. State-tracking fields with zero readers.
5. `mocode/host/session.py:173-182` — `SessionStore.load()`. Session restore
   goes through `MoCode.resume()` → `store.find()`; no callers, no docs.
6. `mocode/host/conversation.py:177-178` — `Conversation.provider` property.
   Legacy convenience; everything uses `conversation.agent.provider`.
7. `mocode/core/prompt.py:121` — `Prompt.context()`. Legacy query API,
   superseded by `Prompt.get()`.
8. `mocode/core/hook.py:220` — `HookRunner.all()`. Internal fan-out view used
   only by one test (see T2).
9. `mocode/core/tool.py:313` — `Tool.returns` constructor parameter and
   attribute. Stored, never read ("never enters the request" per its own
   docstring).
10. `mocode/core/channel.py:109` — `Subscription.pending()` method. Backpressure
    internals; `lagging` and `EventChannel.closed` stay (observability API).

## Forbidden to touch

`mocode/host/plugin/builtin/shell.py` (owned by W1-C), `mocode/testing/`
(W3), `tests/test_shell_bg.py` (W1-C/W4), `docs/`, `examples/`, `specs/`,
`mocode/plugins/__init__.py` exports unless a deleted symbol is exported there
(check: if `ERROR_PREFIXES` or similar is re-exported, remove the re-export).

**Do NOT delete** (frozen keep list — do not "tidy" these even though they have
no in-repo production callers): `AgentLoop.run_with_messages`/`LoopResult`,
`HostContext.spawn`/`subscribe`, `SkillManager.register`, `MoCode.resume`,
`MoCode.plugins_for`, `SessionStore.delete`, `Session.metadata`,
`Display.info`, `Config.current`, `Subscription.lagging`, `EventChannel.closed`,
`IterationContext.emit`, `RequestContext.emit`, `namespace_dir`,
`PluginHost.failures`, `SlowProvider`.

## Work order (one commit per task)

### T1 Remove the ten symbols
Delete each symbol above, including docstrings/`__all__` entries/re-exports.
Run the full suite; fix what breaks *only* in ways listed in T2 (anything else
that breaks = stop and report).

### T2 Adjust tests bound to deleted symbols
Adjust exactly these (verify by grep first — line numbers may drift):
- `tests/test_dispatch.py` — the test using `HookRunner.all()` (~line 616):
  rewrite the assertion against public behavior (e.g. assert the hook's effect
  on events/messages rather than enumerating the runner's list).
- `tests/test_core.py` — tests touching `Prompt.context()` (~line 162) and
  `Tool.returns` (~line 387): delete or rewrite against remaining public API.
- `tests/test_channel.py` — test(s) calling `Subscription.pending()`.
- `tests/test_session.py` — test(s) calling `SessionStore.load()`.
- `tests/test_config.py` — untouched (`Config.current` stays).
- Grep the suite for `.iteration`, `.last_usage`, `.total_usage`,
  `conversation.provider`, `ERROR_PREFIXES` and fix any remaining references.

## Gate (run in worktree)

1. `uv sync` (first command)
2. `uv run pytest -q` — full suite green; confirm exit code independently
   (`echo $?`), do not pipe away the status.

## Final report format

Task status table / commit list (hash + message) / gate result with real exit
code / deviations & trade-offs / open questions. If anything outside the delete
list breaks, stop and report instead of fixing beyond T2.
