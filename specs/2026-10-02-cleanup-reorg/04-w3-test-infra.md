✅ 2026-10-02 @ cleanup/w3-test-infra

# Spec W3 · Test infrastructure consolidation (wave W3)

> Branch `cleanup/w3-test-infra`; worktree `C:\Users\shifu\.worktrees\mocode\cleanup-w3-test-infra`.
> Prerequisite: W2 merged. Read first: `00-overview.md`.

## Goal

Kill the repetitive boilerplate across `tests/` and upgrade `mocode.testing`
so local copies of helpers disappear: one way to build a wired conversation,
one way to scaffold a plugin directory, one event-drain helper, zero
cross-test-file imports.

## Work order (one commit per task)

### T1 `asyncio_mode = "auto"`
Add `asyncio_mode = "auto"` under `[tool.pytest.ini_options]` in
`pyproject.toml`, then delete every `@pytest.mark.asyncio` marker in `tests/`
(~297). Grep before and after (`grep -rc "pytest.mark.asyncio" tests/`) to
prove zero remain. Suite must stay green with the same test count.

### T2 Upgrade `mocode/testing/providers.py`
- `say(text)` gains `finish_reason="stop"` by default (add a keyword param so
  callers can override). This makes the local `_plain_answer`/`_answer`
  copies in test files redundant.
- **Exception scripting**: `MockProvider(responses=[...])` entries may be
  `Exception` instances — when popped, `stream()` raises it *after* recording
  the call. Add a `retriable: Callable[[Exception], bool] | None = None`
  constructor param used by `is_retriable` (default stays `lambda e: False`).
  This must subsume the three local classes in `tests/test_retry.py`
  (`_MockProvider`, `_PolicyProvider`, `_BurningProvider`).
- Request assertion helpers: add `last_request` property (last recorded call
  dict, or None). Keep `calls` as-is (widely used).
- Keep `SlowProvider` (public testing SDK).
- Update `mocode/testing/__init__.py` exports and docstrings.

### T3 Expand `tests/conftest.py`
Add factories/fixtures (names below are yours to refine, keep them obvious):
- `make_mc(**overrides)` — unified `MoCode(config=..., home=tmp_path/"home",
  plugin_dirs=[])` factory. Migrate all ~14 direct `MoCode(...)`
  instantiations in test files to it (incl. the inconsistent sites in
  test_conversations.py and the CLIApp-mixed sites in test_runtime.py — keep
  CLIApp construction where the test is genuinely about the CLI app).
- `wired(responses=None)` — factory returning `(conversation, provider)`:
  `mc.new_conversation(cwd=tmp_path)` with `MockProvider` swapped in.
  Migrate the ~18 `X.agent.provider = MockProvider(...)` sites.
- `write_plugin(...)` — one plugin-scaffolding helper absorbing
  `_write_plugin` (tests/test_plugins.py:77), `_install` (tests/test_cli_plugin.py:57)
  and `_make_skill_dir` (tests/test_tools.py:189), covering plugin.json +
  namespace plugin.py + optional SKILL.md frontmatter. Migrate all three plus
  the ~14 inline `plugin.json` writes where it is strictly an improvement
  (keep inline JSON where the test deliberately writes malformed/minimal
  content — those are the test's subject).
- `plugin_host(...)` — absorbs the `BuildContext(...) + host.build_all() +
  host.assemble(...)` assemblies (6+ sites across test_plugins.py,
  test_plugin_message.py, test_dispatch.py).
- `notices(events)` — event/message filter absorbing the two local copies
  (test_commands.py:60, test_cache_protect.py:29).
- Move the byte-identical `_echo_tool`/`_make_agent`/`_plain_answer`
  (test_agent_loop.py:38,52,70 and test_dispatch.py:32,61,57) and the near-
  duplicate `_loop` (test_core.py:24, test_testing.py:29) into conftest as
  shared fixtures/factories; delete the local copies.

### T4 Delete cross-test-file imports
Eliminate these private imports (replace with conftest fixtures/helpers):
- `test_plugin_install.py`, `test_plugin_env.py` → from `test_plugins`
- `test_cli_plugin.py` internal pattern if it imports across files (check)
- `test_cache_protect.py` → from `test_conversations`
- `test_commands.py` → from `test_builtin_plugins` (check; also any others —
  grep `^from \.test_` and `^from tests\.test_` across tests/).

### T5 Adopt the public drain helpers
Replace hand-rolled drains with `mocode.testing.collect` /
`events_of_type` / `terminal` where the local copy is equivalent
(`_events` in test_agent_loop.py:74, `_drain` in test_conversations.py:32,
the `while (event := reader.take())` loops in test_commands.py:42-57 and
duplicates elsewhere). Behavior must not change — these are mechanical.

### T6 Recycle `tests/test_retry.py`
Replace its three local provider classes with the upgraded `MockProvider`
(exception scripting + `retriable` predicate). Same coverage, less code.

## Forbidden to touch

`mocode/` except `mocode/testing/`, `docs/`, `examples/`, `pyproject.toml`
(T1 only). `tests/test_shell_bg.py` — mechanical marker/fixture changes only;
**all timing and sleeps belong to wave W4, do not "improve" them here.**

## Gate

1. `uv sync`
2. `uv run pytest -q` — same pass count as the W2-merge baseline (713 minus
   tests deleted by earlier waves — the number from the W2 merge report),
   exit code confirmed independently.

## Final report format

Task status table / commit list / final conftest.py API / gate result with
real exit code / deviations & trade-offs / open questions.
Blocked = stop and report.
