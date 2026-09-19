# TODO

Work that was identified and deliberately deferred, with the reason it was
deferred. Anything already decided belongs here rather than in someone's head;
anything not yet discussed belongs in an issue first.

Ordered roughly by what would be reached for next.

---

## 1. Sessions and concurrency

`MoCode` runs one turn at a time (`AgentLoop` shares `messages` and `state`),
and a second `MoCode` in the same process re-does all plugin discovery. Three
concrete problems, in the order they would bite:

- **No busy guard.** Two concurrent `chat()` calls on one `MoCode` interleave
  into one corrupted history. This is a bug even without concurrency as a
  feature — it should refuse, not corrupt.
- **`switch_provider` writes shared state.** It mutates `Config` (which every
  `MoCode` built from that config object shares) and calls `config.save()`, so
  switching a model for one conversation changes it for all of them and for the
  file. Model choice is per-conversation; persistence is a separate, deliberate
  act.
- **No session pool.** Each conversation pays for `PluginHost.load()` — the disk
  scan and module imports. `load()` is already separate from `build_all()`, so
  the fix is to let `MoCode` accept pre-loaded plugins: load once, build per
  conversation. The supporting invariant is already documented (plugin
  instances are stateless; conversation state is created in `build()`), and
  `extra_plugins` / `plugin_dirs` are the two parameters that carry it.

**Done looks like:** a process can hold N conversations whose tool state (bash
cwd/env, skill lookups) does not leak between them, and a turn that is already
running refuses a second one.

**Do not build yet:** a session registry or pool abstraction. A pool is a few
lines of `setdefault`; a framework around it would be speculation.

---

## 2. `Frontend` is a holding position

`HostContext.display` exists so a plugin can say something without knowing a
terminal exists. The intended destination is that **notifications become
events** and the protocol shrinks or disappears — `info` / `warn` / `error` are
the last place a frontend-shaped concept sits in the plugin contract.

The blocker is ordering, not design: an event needs a stream to travel on, and
event streams are per-run. A notification emitted between runs has nowhere to
go. Either the host grows a session-level channel, or notifications stay on the
protocol and only its vocabulary shrinks.

Related: `conversation_changed` is on `Frontend` for the same reason — it fires
between runs. It is arguably correct there (it is a *redraw instruction*, like
`prompt()`), but a plugin cannot observe a session switch at all. Making that
observable means **session hooks** with a defined lifecycle
(`before_switch` / `started` / `shutdown`), which is a real addition rather than
a re-shuffle. Not proposed until something needs it.

---

## 3. Interception is thin in two places

Hooks cover the loop's own decisions (rewrite messages or the system prompt,
veto a tool call, rewrite a result). Two gaps:

- **Provider requests.** Nothing sits between the loop and `provider.stream()`.
  To inspect or replace the payload — headers, an extra field, a rewritten
  message list — you must wrap the provider object. A `before_request` /
  `after_response` hook pair would be the shape.
- **User input.** `CLIApp._dispatch` decides what a typed line means and lives
  in the terminal, so a plugin cannot intercept or rewrite a prompt before it
  reaches the agent.

---

## 4. Reasoning and the answer look identical without colour

Reasoning is dim and unmarked; the answer is default-weight and unmarked. With
ANSI they are distinguishable. Redirected to a file, or in a terminal without
colour, they are the same text.

A single leading character on reasoning fixes it. It was removed on request —
revisit only if plain-text transcripts matter.

---

## 5. ~~A slow, silent tool shows nothing~~ — resolved

Removing the spinner removed the "something is running" signal, and a tool's
header used to be printed lazily, so a tool that was both slow *and* quiet
showed nothing until it finished.

Resolved by claiming the row eagerly and rewriting it in place: a call shows as
a dim `· name  args…` from the moment it starts, and that same row becomes the
verdict when it ends. Eager feedback with no extra line, and a parallel batch
keeps one row per call in call order. The cost is that a call's own output is no
longer printed live — the model still reads all of it.

---

## 6. Not built, and why

Kept here so the reasoning is not rediscovered:

- **Plugin hot reload.** The seam exists — `load()` / `build_all()` / `assemble()`
  are separate and `MoCode` merely calls them in order. It is not built because
  MoCode is not a self-extensible agent; the value would be low against the
  risk of a live tool set changing under a running turn.
- **A transport layer** (HTTP/SSE, or a JSON-lines CLI mode). Every event has
  `to_dict()` and a `run_id`/`seq`, and `RunState.to_dict()` is the status
  endpoint — so this is a thin adapter, deliberately left until something needs
  it. Designing a wire format with only one frontend produces the wrong one.
- **UI component registration** (plugins contributing widgets). Deliberately
  absent: it is the door back to the host knowing about a terminal.

---

## 7. Test gaps

- `cli/input.py` — the paste store, the slash completer and the keybindings have
  no tests. They are the last interactive code without coverage.
- `run_oneshot` is not driven end to end with a real display; only its
  construction is asserted.
