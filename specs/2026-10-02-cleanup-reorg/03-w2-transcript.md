# Spec W2 · Transcript format unification (wave W2)

> Branch `cleanup/w2-transcript`; worktree `C:\Users\shifu\.worktrees\mocode\cleanup-w2-transcript`.
> Prerequisite: W1-A and W1-C merged. Read first: `00-overview.md`.

## Goal

The OpenAI-flavored message-dict format is currently known by four places:
`core/agent.py` writes history (~:624-651 `_tool_call_dicts`/`_assistant_msg`),
`cli/lines.py` replays it (~:260-320), `host/export.py` exports it (~:76-179),
`providers/openai.py` normalizes it (~:147-175). After this wave the format
lives in exactly one module — `mocode/core/transcript.py` — and the four
consumers delegate to it. A message-format evolution then touches one file.

## Work order (one commit per task)

### T1 Create `mocode/core/transcript.py`
Survey all four sites first and design the API from real needs. It must cover:
- **Construction**: assistant message with optional text + tool_calls
  (absorbs `_assistant_msg`/`_tool_call_dicts` from agent.py). Check whether
  tool-result (`role: "tool"`) messages are constructed anywhere besides the
  loop; if so that constructor belongs here too.
- **Reading**: text extraction that handles both plain-string and multimodal
  `content` lists; iteration over content parts; tool_calls access;
  role/tool-result discrimination.
Give every public function a docstring stating the shape it expects/produces.
`core/__init__.py` exports it.

### T2 Migrate the writers and readers
- `core/agent.py` — build history through transcript helpers.
- `cli/lines.py` — `_grouped`/`_replay_calls`/`_load_args`/`_flatten`
  re-implemented on transcript readers. Terminal-specific presentation
  (status mapping, layout) stays in cli. `_STATUS_BY_PREFIX` stays local
  to cli.
- `host/export.py` — `_turns`/`_assistant`/`_tool_call`/`_tool_result`/`_text`
  re-implemented on transcript readers.
- `providers/openai.py` — `_normalize_messages` keeps its dialect-specific
  cleaning (what the API accepts) but uses transcript readers for structure
  instead of raw `msg.get(...)` matching.

### T3 Unify the small duplicates
- Multimodal image placeholder: one canonical string. Pick `[image]`; use it
  in both cli/lines.py (`_flatten`, currently `"[image]"`) and host/export.py
  (`_text`, currently `"[image attached]"`). If tests assert the old string,
  update them.
- Single-line truncation: three hand-rolled versions exist —
  `cli/lines.py:220` (80 cols, `"..."`), `host/plugin/builtin/skills.py:182-185`
  (`_one_line`, 100 chars, `"…"`), `host/export.py:167-179` (`_short_args`,
  30 chars, `"..."`). Introduce **one** helper in `host/text.py`
  (`one_line(text, limit, ellipsis=...)`) used by skills.py and export.py.
  The cli 80-column truncation is terminal layout — it may keep its local
  implementation or call the helper; your call, but the string-truncation
  semantics must exist exactly once.

### T4 Update tests broken by the refactor
Run the full suite; expected touch points: `tests/test_lines.py` (replay
parsing), the `_normalize_messages` private-method assertions in
`tests/test_provider.py` — rewrite those against behavior (drive
`provider.stream` with a history and assert on the outgoing request payload)
rather than asserting the private helper's output shape. Do not touch other
test files unless the suite proves you must (report if so).

## Forbidden to touch

`mocode/testing/`, `mocode/host/plugin/builtin/shell/` (freshly split, owned
by W1-C — do not edit), `tests/` except as T4 requires, `docs/`, `examples/`,
`mocode/plugins/__init__.py` unless adding a transcript export is warranted
(decide: yes — plugin authors writing hooks that rewrite `ctx.messages` need
the readers; export them and note it in the report).

## Gate

1. `uv sync`
2. `uv run pytest -q` full suite, exit code confirmed independently.

## Final report format

Task status table / commit list / the final transcript.py API surface /
gate result with real exit code / deviations & trade-offs / open questions.
Blocked = stop and report.
