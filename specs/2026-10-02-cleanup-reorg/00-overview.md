# Cleanup & Reorganization — Overview

> Group: `specs/2026-10-02-cleanup-reorg/`
> Baseline (master @ W0): **713 passed, 0 failed, 0 skipped, ~45s**, exit 0.
> Three pre-existing `PytestUnraisableExceptionWarning` ("I/O operation on
> closed pipe") from subprocess pipes — known noise, not a gate failure.
> Do not attempt to "fix" them in this group unless a wave makes them worse.

## Goal

The codebase grew by feature waves without a consolidation pass. This group:
removes dead/outdated code (frozen list only), unifies the message-format
knowledge that is currently spread over four files, splits the 1117-line
shell builtin, consolidates test boilerplate into `mocode.testing` +
conftest, de-flakes timing-dependent tests, and rewrites the documentation
set (including a new curated `docs/api.md` and complete public docstrings).

## Non-goal

No behavior changes beyond the frozen delete list. No new features. Old
spec groups under `specs/` and `.zcode/plans/` are historical records —
leave untouched.

## The one rule of deletion

**"No in-repo caller" is NOT a deletion reason.** Delete only what is
outdated / purely internal / redundant with another mechanism / unreachable
in practice. Public API that external users or plugin authors can use or
customize stays even when only tests or docs call it. Both lists below are
frozen — agents must not enlarge or shrink them.

## Frozen delete list (W1 waves execute; every item cited)

| # | Symbol | Location | Why |
|---|--------|----------|-----|
| 1 | `_rmtree` `else`/`onerror` branch | `host/plugin/install.py` | Unreachable (requires-python ≥ 3.12) |
| 2 | `ERROR_PREFIXES` | `core/tool.py:18` | Zero refs; cli keeps its own `_STATUS_BY_PREFIX` |
| 3 | `AgentLoop.iteration/last_usage/total_usage` | `core/agent.py:676-693` | Redundant with `agent.state.*` |
| 4 | `RunState.running_tool_calls/failed_tool_calls` | `core/state.py:203,213` | Zero readers |
| 5 | `SessionStore.load()` | `host/session.py:173-182` | Superseded by `find()` on the resume path |
| 6 | `Conversation.provider` property | `host/conversation.py:177-178` | Legacy; `agent.provider` is the path |
| 7 | `Prompt.context()` | `core/prompt.py:121` | Legacy; `get()` covers it |
| 8 | `HookRunner.all()` | `core/hook.py:220` | Internal fan-out view, one test-only use |
| 9 | `Tool.returns` (param + field) | `core/tool.py:313` | Stored, never read |
| 10 | `Subscription.pending()` | `core/channel.py:109` | Backpressure internals |
| 11 | promote machinery (~200 lines: `BashSession.promote`, `_Foreground`, `_ForegroundSink`, `_pick_foreground`, `_claim`, `FG_*`, `_foreground`, `_fg_seq`) | `host/plugin/builtin/shell.py` | Never wired (no Ctrl+B in cli/input.py); design recoverable from specs history |

## Frozen keep list (W1 must NOT delete; W5 documents)

`AgentLoop.run_with_messages`/`LoopResult` · `HostContext.spawn`/`subscribe`
· `SkillManager.register` · `MoCode.resume`/`plugins_for` ·
`SessionStore.delete` · `Session.metadata` · `Display.info` ·
`bash_tool()` public factory (or equivalent after the shell split) ·
`Config.current` · `Subscription.lagging` · `EventChannel.closed` ·
`SlowProvider` · `IterationContext.emit`/`RequestContext.emit` ·
`namespace_dir()` · `PluginHost.failures`.

## Wave table

| Wave | Branch | Agent | Writes |
|------|--------|-------|--------|
| W1 | `cleanup/w1-dead-code` | 01 | `mocode/{core,host,cli,providers}` minus shell.py/testing + affected tests |
| W1 | `cleanup/w1-shell` | 02 | `builtin/shell.py` → `builtin/shell/`, `test_shell_bg.py`, `test_tools.py` |
| W2 | `cleanup/w2-transcript` | 03 | new `core/transcript.py`, agent/lines/export/openai + broken tests |
| W3 | `cleanup/w3-test-infra` | 04 | `pyproject.toml`, `mocode/testing/`, `tests/conftest.py`, suite-wide mechanical |
| W4 | `cleanup/w4-timing` | 05 | `test_shell_bg.py`, `test_agent_loop.py`, scattered sleeps |
| W4 | `cleanup/w4-assertions` | 06 | `test_provider.py`, `test_runtime.py`, private-assertion sweep |
| W5 | `cleanup/w5-docs` | 07 | `README.md`, `AGENTS.md`, `docs/`, `examples/` |
| W5 | `cleanup/w5-docstrings` | 08 | `mocode/**` docstrings only |

Parallel waves have file-disjoint scopes; serial order: W1(A∥C) → W2 → W3 →
W4(T∥A) → W5(D∥S). Branches are created by the lead from latest master at
dispatch time; merge order follows the table.

## Global invariants

1. Every commit independently passes `uv run pytest -q` — full suite, exit
   code confirmed separately (`uv run pytest -q; echo $?`), never piped away.
2. Lead merges with `--no-ff`, running the gate before and after each merge.
   Red → whole branch returned to its agent; lead does not hand-fix beyond
   trivial cross-module reconciliation.
3. Write-scope discipline: zero diff outside your wave's files. Forbidden
   lists are per-work-order. Blocked = stop and report, never improvise
   beyond scope.
4. No `push`, no tags, no version bumps.
5. Language: English for comments, docstrings, docs, commits. Commit style:
   imperative one-liner (`Remove the promote machinery`).
6. Agents do not modify specs, do not merge, do not touch the main checkout
   or other worktrees. Completion signal = final report + branch commits.
7. A removed shim with no consumer is not replaced by a compat patch.

## Git / worktree protocol

Worktrees live outside the repo: `C:\Users\shifu\.worktrees\mocode\<branch>`.
Lead creates/destroys worktrees. Agents: `cd` into your worktree as step 0,
`uv sync`, never `git checkout master`, never merge/push. Branch state is
the progress record; the spec is the definition, not a kanban.

## Environment facts

- Windows 10, Git Bash, all paths ASCII (`C:\Users\shifu\...`).
- Python via `uv` (requires-python ≥ 3.12). Always `uv run pytest`.
- Full suite baseline ~45s (after W4 it should get faster; report timings).
- No browser needed anywhere in this group.

## Final reports

Every agent ends with: task status table / commit list (hash + message) /
gate results with real exit codes / deviations & trade-offs / open questions.
"Done" without command output and exit codes counts as not done.
