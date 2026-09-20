# TODO

Work that was identified and deliberately deferred, with the reason it was
deferred. Anything already decided belongs here rather than in someone's head;
anything not yet discussed belongs in an issue first.

Ordered roughly by what would be reached for next.

---

## 1. ~~Sessions and concurrency~~ — resolved

`MoCode` used to *be* one conversation: one cwd, one agent, one history, and no
guard against two turns at once. What replaced it:

- **A turn belongs to the conversation.** `AgentLoop.start()` returns a `Turn`
  that can be watched, waited for and cancelled by anyone; `stream()` and `chat()`
  are views over it. A second `start()` while one runs raises — immediately, since
  `start()` is a plain call — instead of interleaving two histories.
- **Events go to a channel, not to the caller.** One `EventChannel` per
  conversation, `seq` monotonic across turns, fan-out to any number of readers,
  `subscribe(since=seq)` for replay, and a bounded backlog per reader so a slow
  one falls behind instead of holding up the model.
- **A conversation is the unit:** `MoCode.new_conversation(cwd=..., provider=...,
  model=...)` gives it its own project, tools, plugins build, history, model and
  stream. The runtime keeps no registry of them.
- **Model choice is per conversation.** `conversation.set_model()` touches nothing
  else and writes no file; `MoCode.set_default_model()` is the only writer of
  `config.json`.
- **Tools work in the conversation's project.** `bash` starts in `ctx.cwd` and
  `restart` returns there; the filesystem tools resolve relative paths against it.
- **Plugins load once per project** (`MoCode.plugins_for`) and build once per
  conversation (`PluginHost.build_all`) — the invariant being that a plugin
  instance is stateless.

**Not built:** a *live conversation registry*. An application keyed by its own id
is the only thing that knows what is open, which is deliberate — the identity a
conversation has in an application is that application's business.

---

## 2. ~~`Frontend` was a holding position~~ — resolved

The protocol is gone. `info` / `warn` / `error` are `Notice` events and
`conversation_changed` is `ConversationChanged`, both published into the
conversation's channel — which exists between runs, so a message that has nothing
to do with a turn now has somewhere to go. A frontend is a reader and nothing
else; `host/` no longer has a field a plugin can reach a user through.

---

## 3. Interception is thin in one place

**Provider requests.** Nothing sits between the loop and `provider.stream()`. To
inspect or replace the payload — headers, an extra field, a rewritten message
list — you must wrap the provider object. A `before_request` / `after_response`
hook pair would be the shape.

The other half of the old gap — user input — closed itself: input resolution is
`host.command.dispatch(text, *, conversation, commands)`, a plain function any
frontend calls (and can wrap), and what a frontend does out of `Kind.PROMPT` is
that frontend's business.

---

## 4. Reasoning and the answer look identical without colour

Reasoning is dim and unmarked; the answer is default-weight and unmarked. With
ANSI they are distinguishable. Redirected to a file, or in a terminal without
colour, they are the same text.

A single leading character on reasoning fixes it. It was removed on request —
revisit only if plain-text transcripts matter.

---

## 5. ~~A slow, silent tool shows nothing~~ — resolved

Resolved by claiming the row eagerly and rewriting it in place: a call shows as a
dim `· name  args…` from the moment it starts, and that same row becomes the
verdict when it ends. Eager feedback with no extra line, and a parallel batch
keeps one row per call in call order. The cost is that a call's own output is no
longer printed live — the model still reads all of it.

---

## 6. Not built, and why

Kept here so the reasoning is not rediscovered:

- **MCP servers.** A plugin's `mcp.json` is recognised and ignored *by this
  repository*: the client (stdio and Streamable HTTP) is a capability, so it
  lives outside — `mocode-plugins/mcp` is that plugin, and the lifecycle now
  supports it (`Plugin.prepare` is exactly the async discovery pass it needs).
  The portable *other* half of the standard — `skills/` inside a plugin — is
  already read.
- **A transport layer** (HTTP/SSE, or a JSON-lines CLI mode). Every event has
  `to_dict()` and a `run_id`/`seq`, `RunState.to_dict()` is the status endpoint,
  and `conversation.subscribe(since=seq)` is the reconnect protocol — so this is
  a thin adapter, deliberately left until something needs it. Designing a wire
  format with only one frontend produces the wrong one.
- **A live conversation registry.** See §1. Two conversations opened on the same
  session id would also write the same file, and nothing in the host prevents it:
  the application keys its dictionary by id, which is the same place the guard
  belongs.
- **Plugin hot reload.** The seam exists — `load_plugins()` / `build_all()` /
  `assemble()` are separate, and `MoCode.plugins_for()` caches per project — but
  the cache would have to be invalidated, and a live tool set changing under a
  running turn is a real hazard. Not built because MoCode is not a
  self-extensible agent.
- **UI component registration.** Plugins contributing widgets is the door back to
  the host knowing about a screen. A frontend's own namespace
  (`mocode.cli/plugin.py`) is where that belongs, and it is the frontend that
  decides what a namespace may contain.

---

## 7. Test gaps

- `cli/input.py` — the paste store, the slash completer and the keybindings have
  no tests. They are the last interactive code without coverage.
- `cli/dialogs.py` — `usable()` (the no-TTY guard) is exercised only indirectly,
  by the commands that treat a `None` answer as "nothing chosen".
- The channel's lag path is covered by unit tests; nothing drives a slow reader
  through a real turn to assert the resync story end to end.
