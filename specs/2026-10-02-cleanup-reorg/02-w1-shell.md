✅ 2026-10-02 @ cleanup/w1-shell

# Spec W1-C · Shell plugin cleanup and split (wave W1)

> Branch `cleanup/w1-shell`; worktree `C:\Users\shifu\.worktrees\mocode\cleanup-w1-shell`.
> Read first: `specs/2026-10-02-cleanup-reorg/00-overview.md`.

## Goal

`mocode/host/plugin/builtin/shell.py` (1117 lines, a quarter of the host layer)
loses its unreachable foreground-promotion machinery (~200 lines) and is split
into a package organized by responsibility. The public construction surface
used by tests and plugin authors keeps working.

## Background (verified)

- The promote mechanism (`BashSession.promote()` at ~:465-554 plus
  `_pick_foreground`, `_claim`, `_Foreground`, `_ForegroundSink`,
  `FG_RUNNING/PROMOTED/SETTLED`, `self._foreground`, `self._fg_seq` at ~:233-293)
  has **no production caller**. A historical spec planned a CLI Ctrl+B binding;
  it was never wired (`cli/input.py` key bindings contain no Ctrl+B) and the
  design is recoverable from `specs/` history if ever needed.
- `bash_tool()` factory (~:915-918) is used directly by `tests/test_tools.py`
  (4 sites) — keep a public factory with the same calling convention.
- Everything else in the file (BashSession background execution, `_Ring`
  output buffer, background-manager tool, ShellPlugin) is live code.

## Forbidden to touch

Anything outside `mocode/host/plugin/builtin/shell.py`,
`tests/test_shell_bg.py`, and `tests/test_tools.py` — specifically not `cli/`,
`core/`, other `host/` modules, `mocode/testing/`, `docs/`, `examples/`.
The other builtin plugins (filesystem, skills, …) are off-limits.

## Work order (one commit per task)

### T1 Delete the promote machinery
Remove all promote-related code from `shell.py`: the `BashSession.promote`
method, `_Foreground`/`_ForegroundSink` classes, `_pick_foreground`/`_claim`
helpers, `FG_*` state constants and registries (`self._foreground`,
`self._fg_seq`), and any promote-specific announcement/sink plumbing that
exists solely for it (e.g. `_announce_done` branches only reachable from
promote — verify each removal by grep before deleting).
Keep `_early_output_sink` and session-announce plumbing that the normal
background flow uses.

### T2 Sync `tests/test_shell_bg.py`
Delete the tests that exercise promote (grep the file for
`promote|_foreground|FG_|_claim`). Keep every background-execution test.
**Do not** touch timing/sleeps in the remaining tests — wave W4 owns that.
Run the file: `uv run pytest tests/test_shell_bg.py -q` must pass.

### T3 Split shell.py into a package
Convert `builtin/shell.py` into `builtin/shell/` with modules by
responsibility, e.g.:
- `session.py` — `BashSession` (background execution core)
- `ring.py` — the `_Ring` output buffer
- `tool.py` — bash/background-manager tool builders incl. the public
  `bash_tool()` / `bash_tool_for()` factories
- `plugin.py` — `ShellPlugin`
- `__init__.py` — re-exports so existing imports keep working:
  `from ...builtin.shell import ShellPlugin, bash_tool` must not break
  (grep the repo for `builtin.shell` / `from .shell` / `import shell` and
  make every existing import path resolve).

Constraints: no circular imports; public names unchanged; private
cross-module imports are fine within the package. The split is mechanical —
no behavior changes.

### T4 Verify `tests/test_tools.py`
The 4 direct `bash_tool(` constructions must still work unchanged (or with
trivial import adjustments only). Full suite green.

## Gate

1. `uv sync`
2. `uv run pytest -q` full suite, exit code confirmed independently.

## Final report format

Task status table / commit list / gate result with real exit code / module map
of the new package / deviations & trade-offs / open questions. Blocked = stop
and report.
