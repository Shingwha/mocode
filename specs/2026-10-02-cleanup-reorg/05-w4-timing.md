# Spec W4-T · Timing-deterministic tests (wave W4)

> Branch `cleanup/w4-timing`; worktree `C:\Users\shifu\.worktrees\mocode\cleanup-w4-timing`.
> Prerequisite: W3 merged. Read first: `00-overview.md`.

## Goal

Remove wall-clock fragility from the test suite: no fixed sleeps as
synchronization, no wall-clock-duration assertions, no cancel races. Tests
must wait on *events* (process output, event-stream entries, flags) with
bounded timeouts. Coverage must not shrink — every scenario currently tested
stays tested.

## Scope (verified hotspots)

- `tests/test_shell_bg.py` — the worst: real `sleep 0.4/0.3/5` subprocess
  commands in several tests, `asyncio.sleep(0.4)` ×8, `sleep(3*0.3+0.2)`,
  `sleep(1.3)`, a wall-clock assertion (`elapsed < 0.8`), a polling deadline
  helper (~:432-443), `wait_for(..., 10)` ×9. Background-session tests may
  keep using short-lived real subprocesses (that is the feature under test)
  but readiness must be gated on observed output/events, not on assumed
  durations.
- `tests/test_agent_loop.py` — the "Stuck hook" cancel tests (~:173/183) race
  a `sleep(0.05)` against cancellation; `_until`/`_thread_started` poll at
  0.01s steps (~:1195-1204); a parametrized case with `time.sleep(1)` real
  work (~:275); `tool_timeout=1` tests that truly wait a second (~:1213,
  :1230, :1292, :1301).
- Scattered: `test_channel.py` (1), `test_conversations.py` (2),
  `test_display.py` (1) mild sleeps — replace with event/condition waits.

## Techniques (pick per case)

- Await the event stream / ring output / a `threading.Event` or
  `asyncio.Event` the test sets from a callback, inside `asyncio.wait_for`.
- Where a duration is the *subject* (e.g. timeout tests), shrink the
  duration aggressively (10-100ms) or make the wait synthetic; keep at most
  one integration-grade timeout if a test genuinely needs real time.
- For cancel-race tests: synchronize on "tool has started" (event set inside
  the tool body) before cancelling — never on a sleep of comparable length.

## Forbidden to touch

`mocode/` production code (if a test is unfixable without a production
change, stop and report), `tests/test_provider.py` and `tests/test_runtime.py`
(owned by W4-A), `docs/`, `examples/`. No test deletion except promote tests
already removed by W1-C — if you believe a test is redundant, report it, do
not delete.

## Gate

1. `uv sync`
2. `uv run pytest -q` full suite green; additionally run
   `uv run pytest tests/test_shell_bg.py tests/test_agent_loop.py -q` three
   times consecutively — all three runs must pass (flakiness check). Confirm
   each exit code independently.

## Final report format

Per-file list of timing fixes (before → after) / commit list / suite timing
before vs after (the baseline suite takes ~45s; report the new wall time) /
gate results with real exit codes / deviations & trade-offs / open questions.
Blocked = stop and report.
