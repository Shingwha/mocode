✅ 2026-10-02 @ cleanup/w4-assertions

# Spec W4-A · Behavior-level assertions (wave W4)

> Branch `cleanup/w4-assertions`; worktree `C:\Users\shifu\.worktrees\mocode\cleanup-w4-assertions`.
> Prerequisite: W3 merged. Runs in parallel with W4-T — **file scopes are
> disjoint**: you own `tests/test_provider.py`, `tests/test_runtime.py`, and
> a sweep for private-member assertions everywhere *except*
> `test_shell_bg.py` and `test_agent_loop.py` (owned by W4-T).

## Goal

Tests should assert public behavior, not private implementation. Convert
private-member assertions to public-API equivalents; leave intentional
text-contract assertions (capsys on user-visible output) alone — those are
documented as keepers.

## Known targets (verified)

- `tests/test_provider.py:226,237` — asserts on
  `OpenAIProvider._normalize_messages` (private). Rewrite at behavior level:
  drive `OpenAIProvider.stream` with a canned history (fake the underlying
  HTTP/client layer the same way the existing tests do) and assert on what
  actually goes out / comes back, not on the helper's return shape.
  Note: W2 may have already rewritten part of this — build on whatever is
  there; your job is behavior-level coverage, whatever remains private.
- `tests/test_runtime.py:54` — asserts `mc.store._base_dir` (private). Assert
  through the public surface instead (e.g. a session saved via
  `conversation.save()` resolves under the configured home; or via
  `MoCode(home=...)` + `resume` round-trip). Choose the assertion that best
  pins the *intended* behavior.
- Sweep: grep tests for `._` attribute access in assertions
  (`\._[a-z]` in assert lines / `assert .*\.[a-z_]+\._`). Retry-policy tests
  that assert on a *locally defined* class's private attribute are fine.
  Report anything ambiguous instead of rewriting blindly.

## Explicit non-goals

- No capsys text-coupling cleanup — user-visible copy assertions stay.
- No timing changes (W4-T's scope).
- No production changes. If a private assertion has no public equivalent,
  report it rather than forcing one.

## Gate

1. `uv sync`
2. `uv run pytest tests/test_provider.py tests/test_runtime.py -q` then
   `uv run pytest -q` full suite; confirm each exit code independently.

## Final report format

List of converted assertions (file:line, before → after) / commit list /
gate results with real exit codes / deviations & trade-offs / open questions.
Blocked = stop and report.
