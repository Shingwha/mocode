# Architecture

MoCode is a small agent kernel, a runtime that holds conversations, and a
plugin host. The organising rule:

> **A concept that could be written as a plugin does not belong in the kernel.**

The kernel knows how to run an LLM loop with tools, hooks and a prompt.
Everything else — sub-agents, compaction, workflows — arrives through the
plugin API. [AGENTS.md](../AGENTS.md) states the rules a contributor works
under; this document explains the shape.

```
mocode/
├── core/                the kernel — mechanism only, no app dependencies
│   ├── agent.py         AgentConfig, AgentLoop (start/stream/chat/derive), Turn
│   ├── channel.py       EventChannel, Subscription — the run's event stream
│   ├── dispatch.py      ToolDispatcher — the one execution path for a tool call
│   ├── events.py        Event + the eleven events a run emits
│   ├── state.py         RunState — the events folded into a live snapshot
│   ├── hook.py          AgentHook, HookRunner, contexts — the interception channel
│   ├── prompt.py        Prompt, Section — section-based XML assembly
│   ├── provider.py      Provider protocol, Chunk/Response DTOs, with_retry_stream
│   └── tool.py          Tool, ToolPolicy, ToolRegistry, the JSON-Schema checker
├── host/                the layer an application embeds
│   ├── runtime.py       MoCode — the process runtime: config, plugins, sessions
│   ├── conversation.py  Conversation — one project, one model, one history
│   ├── events.py        ConversationChanged — the host's own event
│   ├── command.py       Command, CommandRegistry (register + dispatch), CommandResult
│   ├── config.py        Config, ProviderEntry, ModelEntry
│   ├── session.py       Session, SessionStore
│   ├── export.py        Session → Markdown
│   ├── prompt.py        build_system_prompt(ctx) — render sections, nothing else
│   └── plugin/          Plugin, BuildContext/HostContext, loader, PluginHost,
│                        env (a plugin's own uv environment), install (install/sync/list/remove)
│       └── builtin/     the plugins MoCode ships: filesystem, shell, skills,
│                         default-prompts, session, help, cache-protect
├── cli/                 the terminal front-end — a consumer of host/
│   ├── app.py           CLIApp — the REPL, dispatch and Ctrl-C
│   ├── plugin.py        CLIPlugin — the terminal's own extension surface
│   ├── render.py        CLIRenderer — the event stream, as terminal lines
│   ├── lines.py         Line + builders — what a turn looks like, as data
│   ├── display.py       Display — terminal primitives; theme, text, input, dialogs
│   └── commands.py      the commands that need a terminal: /quit /copy /model /resume
├── providers/openai.py  OpenAI-compatible streaming provider
├── plugins/__init__.py  the public SDK third-party plugins import
├── testing/             the public test kit — a scripted model, no network
│   ├── __init__.py      collect / terminal / events_of_type — reading a turn
│   └── providers.py     MockProvider, SlowProvider, say/call_tool, chunk replay
└── cli_args.py main.py  argument parsing and process entry
```

Dependencies only ever point down: `core ← host ← cli`. `core` and `providers`
import nothing above them; `host` never imports `cli`. The layering rules and
what they forbid are listed in [AGENTS.md](../AGENTS.md#layering); the point of
all of them is the same three properties:

- the kernel is embeddable and testable with no application around it;
- an application can embed `host/` without inheriting a terminal;
- every consumer — terminal, web backend, test — sees the same event stream,
  so no feature needs a private path into the loop.

## The kernel

A conversation runs one turn at a time. `AgentLoop.start()` begins one:

```python
turn = agent.start("list the tests")     # raises if a turn is already running
async for event in turn.subscribe():
    ...
terminal = await turn.wait()             # RunFinished or RunFailed
turn.cancel()                            # stop it from anywhere
```

`stream()` and `chat()` are conveniences over that: they run a turn and scope
it to the caller — stop reading, or be cancelled, and the turn stops with you.
A turn started with `start()` belongs to the conversation instead, which is
what a server needs: a client that disconnects does not kill the run, and a
client that reconnects picks it up.

**Every event goes into the conversation's channel** (`core/channel.py`): one
ordered stream per conversation, with a `seq` that keeps counting across turns.
That single decision gives an application

- **many readers** — a renderer, a status page, a logger, a test;
- **replay** — `subscribe(since=seq)` hands a returning reader what it missed,
  and a gap in `seq` says honestly when it could not;
- **no back-pressure** — a subscription has a bounded backlog and drops its
  oldest events rather than holding up the model;
- **a place for a message that has nothing to do with a run** — a plugin
  saying something between turns publishes into the same channel.

Two delivery policies, one publish path: a hook's `on_event` is an *inline*
subscription (the publisher waits, because a hook must see the event before
the run moves on); everything else is buffered and nobody waits.

Both bounds are constructor parameters (`EventChannel(replay=…, backlog=…)`);
the constants in `core/channel.py` are only the defaults. A terminal running
one conversation at a time keeps 1000/1000 — a renderer that reconnects wants
the whole turn it missed. An embedding with many conversations in one process
lowers *replay* (≈100): a turn emits deltas fast, and replaying a thousand of
them at every reconnect is its own storm — a reader that was away longer
resyncs from state instead. A slow reader is never an error: it reports
`dropped > 0`, the gap in `seq` says the same without trusting the counter,
and the model was never held back. What was missed is recovered from
`RunState`, not from the stream.

`RunState` is the same stream folded into a snapshot, for callers that want to
ask "what is happening now" — `conversation.state.to_dict()` is the shape to
put behind an HTTP status endpoint. Folding is guarded (by `seq`, by `run_id`),
so a reconnect replay never double-counts and another run sharing the channel
never folds into yours. `content` is a bounded window rather than a ledger:
past `content_limit` (200k characters by default, constructor-only — the same
internal-valve standing as `tool_result_limit`) the fold keeps the newest text
and marks at the front how much was elided, so a runaway turn cannot grow
every held snapshot without bound.

Four primitives make features-as-plugins possible:

- **The event stream** — one typed, serialisable contract for everything the
  loop does. A renderer, a plugin, a test and an embedding application all
  consume the same stream; nobody needs a private callback.
- **`AgentLoop.derive(*, system_prompt, tools, hooks, config, model,
  provider, channel)`** — a nested agent inheriting *copies* of the parent's
  capability set and policy (a child's registry changes never reach the
  parent), optionally on another provider. This is the whole basis of
  sub-agents and workflow nodes; pass `channel=` to have the child publish
  into the parent's timeline.
- **Interceptable hooks** — `on_tool_start` may rewrite `ctx.tool_args` or set
  `ctx.deny` to veto a call; `on_tool_complete` may rewrite `ctx.tool_result`;
  `before_iteration` may rewrite `ctx.messages` and `ctx.system_prompt` (the
  prompt rewrite is scoped to the run — restored when the turn ends);
  `before_request` / `after_response` bracket each provider call — the last
  look at the payload about to be sent, and a usage-correction point after the
  response.
  `ctx.status` records the outcome as one of the `TOOL_*` constants in
  `core/events.py` (`ok` / `error` / `timeout` / `denied` / `not_found`) so no
  one parses result strings.
- **Tool metadata** — `Tool(tags=...)` plus `ToolRegistry.select(...)`:
  capability scoping by tag, never by a hard-coded list of tool names.

## Events and hooks are different jobs

They are deliberately not unified, because they run in opposite directions:

| | Events | Hooks |
|---|---|---|
| direction | one-way notification | request → response |
| purpose | say what happened | decide what happens |
| who consumes | any number of readers | the loop itself |
| cost of a slow consumer | it falls behind (and is told so) | the loop waits |

So observation always goes through the stream, and anything that must *answer*
is a hook. A hook's decision is observable anyway, because it shows up in the
events it caused: a vetoed call is a `ToolCallFinished` with `status="denied"`.

Ordering is fixed so a consumer never sees a stale view: `on_tool_start` runs
first, and `ToolCallStarted` is published with the *final* arguments;
`on_tool_complete` runs before `ToolCallFinished`; `before_request` runs after
`before_iteration` and immediately before the provider call; `after_response`
runs once a response is fully received, and a `usage` rewrite there is what
`IterationFinished` reports and the turn's totals add up.

**Provider retries do not run the request hooks.** The retry window closes
before the first chunk arrives, and it belongs to the retry orchestration, not
to request interception — a retried attempt is not a new request a hook should
see again.

## Event attribution

Which stream an event belongs to is the `run_id` it carries, and who put it
there decides how far it travels. One table, the whole contract:

| Published by | Stamped with | Folds into `RunState` | In a `Turn` view |
|---|---|---|---|
| the loop (`_publish`): run lifecycle, deltas | its `run_id` | yes | yes |
| the dispatcher, `origin="model"` | the run's id | yes | yes |
| the dispatcher, `origin="program"` | the run it belongs to | no | yes — observable, auditable, never counted |
| `ctx.emit` during a turn | the turn's id, unless the publisher claimed one | no | yes |
| `ctx.emit` between turns | no run id | no | no — the conversation stream only |

So a turn's readers see everything that happened while it ran — including
what plugins said and what program-origin calls did — while `RunState` (and
`tool_calls_made`, and `messages`) stays the model's side of the story. A
publisher that needs a different attribution sets `event.run_id` itself before
emitting; the host never overwrites a claimed id.

## The dispatcher

Executing a tool is policy, not orchestration, and the policy is public:
`core/dispatch.py`'s `ToolDispatcher` is the whole pipeline around one call —
hook interception, the visibility check, timeout with cooperative
cancellation, status mapping, failure prefixes, truncation, and the
Started/Finished events. `AgentLoop` keeps only orchestration: parse the
provider's argument JSON, batch the calls, assemble the tool message from the
`DispatchResult`. The loop's `dispatcher` attribute *is* the component — there
is no second assembly site — and a bare-core embedder can construct one
directly over its own registry, hooks and config.

Anything that runs tools of its own — a sub-agent tool, a workflow node, a
codemode-style orchestrator — calls the same dispatcher with
`origin="program"` and gets the identical pipeline: same hooks, same
visibility rules (a tool marked for the other audience is refused exactly like
a switched-off one), same truncation, same events. The **program-origin
contract**: those calls' events reach the channel stamped with the run they
belong to, so every reader can observe and audit them, but they never enter
`messages` and never fold into the turn's `tool_calls_made` count — the
conversation stays what the model said and was answered, and the live state
stays the model's side of the story.

**Execution policy resolves in one order**: call-level
(`dispatcher.run(..., timeout=...)`) over tool-level (the tool's
`ToolPolicy` — a static override, or a callable reading the call's arguments,
which is how `bash` maps its `timeout` argument) over config
(`AgentConfig`). The effective timeout and result limit land on the
`ToolCallContext` before the tool runs, so the tool and the completion hooks
see exactly what the call ran under.

## Tool arguments are JSON Schema

A tool declares its arguments as a JSON Schema object node; `to_schema()`
passes it through as the request's `parameters`, so what the model is offered
and what the arguments are checked against are the same document — and nested
object/array shapes, the MCP ecosystem's basic currency, are expressible. The
checker is dependency-free and deliberately small: it enforces the common
keywords (`type`, `required`, `properties`, `items`, `enum`, `anyOf`/`oneOf`,
`default`) and passes everything else, logged at debug — forward compatibility
beats false rejections in a kernel that must stay dependency-free. A
violation is a `ToolError` (`missing_param` or `invalid_type`) flowing through
the ordinary `error:` result pipeline, never breaking the turn. Defaults are
filled at the top level and at every nested level the check reaches, and a
Python `bool` is never accepted as an `integer` or `number` — it is an `int`
subclass at home, not a number on the wire.

## Two provenance axes

`source` and `origin` answer different questions about a tool, and neither can
stand in for the other:

| axis | answers | decided | lives |
|---|---|---|---|
| `source` | who this tool *belongs to* — static attribution, a channel-prefixed name | once, at registration — the path stamps it, a self-reported value never survives | `Tool.source` |
| `origin` | who *asked for this call* — the model in a turn, or program code | every call | `ToolCallContext.origin`, `ToolCallStarted/Finished.origin` |

`source` does not enter events: the registry is state, the event stream is a
flow of facts — a consumer that wants to know who owns a tool reads
`registry.get(name).source` when it needs to. `origin` never enters the
registry: the same tool is callable from both sides, and which side did call
is a fact about the call, not the tool.

## Run lifecycle

```
start(prompt) → Turn
  └─ the run, published into the conversation's channel
      ├─ RunStarted                     model + visible tools
      ├─ per iteration:
      │   ├─ before_iteration(ctx)      intercept: messages / system_prompt writable
      │   ├─ IterationStarted
      │   ├─ before_request(ctx)        intercept: the request about to be sent
      │   ├─ provider.stream(...)       retried by with_retry_stream
      │   │   └─ TextDelta / ReasoningDelta as chunks arrive
      │   ├─ after_response(ctx)        intercept: usage / finish reason
      │   ├─ IterationFinished          usage + stop reason
      │   └─ per tool call, in parallel:
      │       ├─ on_tool_start(ctx)     intercept: rewrite args / deny
      │       ├─ ToolCallStarted        with the final args
      │       ├─ ToolOutput             as a streaming tool produces output
      │       ├─ on_tool_complete(ctx)  intercept: rewrite result
      │       └─ ToolCallFinished       status, result, duration
      └─ RunFinished                    stop_reason says how it ended | RunFailed
```

Every turn ends with exactly one terminal event — including one that was
cancelled, and including one that died on a `BaseException`: the failure is
parked on `turn.failure` and the readers still get their `RunFailed`.
`HookRunner` fans out with per-hook error isolation: a raising hook is logged
and skipped, never fatal. A cancelled turn leaves the history replayable, with
an answer for every assistant tool call.

`RunFinished.stop_reason` says how the turn ended: `completed` when the last
iteration produced the answer; `max_iterations`, `max_tool_calls` or
`time_budget` when an `AgentConfig` budget cut it (all per turn, 0 =
unlimited, all checked before each provider call — a cut is an ending, not a
failure: the history stays replayable because every issued tool call keeps
its answer); `cancelled` when the turn was stopped. `chat()` raises
`IterationLimit` for the iteration cap rather than returning an empty string
a caller could mistake for the model's answer. The wall-clock budget is not
checked inside retry backoff: a turn mid-backoff may overshoot it by one
backoff interval, by design.

## The host

`MoCode` is the process runtime an application embeds; `Conversation` is the
unit it works with.

```python
from mocode import MoCode

mc = MoCode()                                   # config → plugins → store
conv = mc.new_conversation(cwd="/srv/proj-a")   # one conversation
async for event in conv.stream("..."):
    ...
conv.state.to_dict()                            # live snapshot
conv.save()                                     # → ~/.mocode/sessions/…
```

**Everything that differs between two conversations lives on the
conversation**, not on the runtime: which directory the tools work in, which
provider the model calls go to, which plugins were built (and therefore which
shell session, skill index and tool instances exist), what has been said, and
which session id it will be saved as. A conversation's plugins are *built* for
it — `load_plugins()` is the per-project half (discovery, import, enabled
check, cached by `MoCode`), `PluginHost.build_all()` is the per-conversation
half.

The runtime keeps no registry of live conversations: the identity a
conversation has in an application — a route, a tab, a socket — is that
application's business. `mc.store` is the session store, and
`mc.resume(session_id)` opens a stored one wherever its project was.

**Every command is a plugin contribution.** `conversation.commands` starts
empty and fills with whatever plugins register. The host's built-in plugins
supply the ones any frontend can honour — `/export`, `/clear` (`session`),
`/help` (`help`), `/skill:<name>` (`skills`) — and each is disabled with
`plugins.<name>.enabled = false` like any other. The commands that need an
interactive picker or the clipboard (`/model`, `/resume`, `/copy`, `/quit`)
belong to the terminal, which registers them through its own plugin interface
(`cli/plugin.py`, `cli/commands.py`).

**The prompt is assembled from plugin sections alone.** `host/prompt.py`
renders `ctx.prompt_sections` as XML in `(priority, insertion order)` order —
stable content first, volatile last, for prefix caching — and contributes no
content of its own. The default sections (`guidelines`, `agents`,
`environment`, `time`) come from the `default-prompts` builtin plugin, and a
name collision is won by the last section registered, the same rule every
other contribution follows. Tool schemas travel with every request, so the
prompt never repeats them.

**A session's request prefix is pinned; the world's changes are announced.**
Both halves of what a request carries — the system prompt and the tool
interface — are held still for a session's lifetime, so the provider's prefix
cache survives turn after turn. The host owns the pinning (the prompt is a
plain attribute it freezes and reinstates; `ToolRegistry.freeze()` holds the
tool projection, opt-in per runtime, off meaning live); the `cache-protect`
builtin plugin owns the telling: at a resume and at the start of every turn
it diffs what the model was last told against the world as it stands and
appends one `[context update]` notice — unified diffs for the prompt and for
a changed schema, state lines for a tool switched on or off — just before the
next user message, with the cached prefix above it untouched. A change
reverted before the turn that would announce it is never announced. The
plugin's baselines live in `ctx.plugin_state(name)`, the host's generic slot
for per-conversation plugin state that travels with the session; a
`rebuild_prompt()` re-freezes both halves and clears them, accepting the
cache loss in one deliberate act.

## Plugins

A plugin directory follows the [Agent Plugins](https://agent-plugins.org)
standard: the root holds what any compatible client understands, and
everything client-specific lives under a directory named for the namespace
that defines it. The layout, the manifest rules and the worked examples are in
[plugins.md](plugins.md); the shape of it:

```
git-status/
├── plugin.json              the manifest: name, version, description
├── skills/commit/SKILL.md   portable skills
├── mcp.json                 MCP servers (recognised, not served yet)
├── mocode/plugin.py         contributions to the agent — every frontend
└── mocode.cli/plugin.py     contributions to the terminal — this frontend only
```

The host hands the namespace directory over without reading it
(`PluginSpec.directory` → `ctx.plugin_sources`), so `host/` still knows nothing
about terminals, and a plugin written for the terminal travels to a web
frontend that simply does not read `mocode.cli`.

## The terminal

`CLIApp` is `MoCode` plus one `Conversation` plus a terminal: a REPL, input,
slash-command dispatch and Ctrl-C. It contributes nothing to the host — its
commands go through its own plugin interface, and its rendering is a
subscription:

```python
subscription = conversation.subscribe()
turn = conversation.run(prompt)
async for event in turn.subscribe():
    renderer.draw(event)          # → mocode.cli.lines → Display
```

`CLIRenderer` is a pure consumer: no hooks, no interception. It owns only the
state of the turn *as it is being drawn* — which tool calls are in flight, and
which row on screen each of them owns. Every shape it draws comes from
`cli/lines.py`, as data, so the live renderer and history replay cannot drift
apart and neither needs a terminal to be tested.

What a turn looks like, in one picture:

```
❯ 用 bash 数一下 mocode 下有多少个 py 文件

The user wants to count the .py files. Let me run find.
· bash  find mocode -name '*.py' | wc -l…          ← while it runs, dim
✓ bash  find mocode -name '*.py' | wc -l · exit_code=0 · 0.1s   ← the same row
mocode/ 下共有 47 个 .py 文件。
↑1,234 ↓567 tokens
────────────────────────────────────────
```

Everything starts at column 0; the first character says what the line is.
There is no indentation, because a terminal has no hanging indent. The answer
is unmarked and left at the default foreground, so it is the brightest thing
on screen. A rule closes each turn, with what the turn cost on the line above.

A tool call claims a row the moment it starts and keeps it: a dim placeholder
that is rewritten in place with the verdict. A parallel batch stays one row per
call in the order the calls were made rather than the order they finish —
which is also why a call's own `ToolOutput` is not printed; it still reaches
the model and every other consumer, the terminal just does not draw it.

Rewriting a row is only sound while the block is the last thing on screen, so
`Display` guards it rather than trusting it: block lines are clamped to one
terminal row, any other output freezes the block for good, and off a terminal
(`Display.live`) the whole mechanism is off and a call appends its verdict
when it finishes. `main.py` passes `sys.stdout.isatty()`, so `mocode -p "…"`
draws its turn on a terminal and stays a plain, escape-free pipe when
redirected. A modal dialog is drawn only where there is someone to answer it:
`dialogs.select` returns `None` when either end is not a terminal.

## Data flow

```
user input → CLIApp._dispatch ─┬─ slash command → handler(CommandContext) → CommandResult
                               └─ prose → conversation.run() → AgentLoop.start()
                                                            ├─ provider.stream
                                                            └─ events → EventChannel
                                                                         ├─ Subscription → CLIRenderer → Display
                                                                         └─ Subscription → your application
```

An embedding application is the same path minus the terminal:
`your app → conversation.run() → Turn → channel → your subscription`.

## Where the tests live

- `tests/test_agent_loop.py` — the loop, tools, interception, derive;
  `tests/test_channel.py` — ordering, replay, lagging readers, closing;
  `tests/test_events.py` — the event contract and `RunState`, no loop involved.
- `tests/test_conversations.py` — what the runtime exists for: N conversations
  at once, in different projects, on different models, sharing nothing.
- `tests/test_plugins.py` — layout, manifest rules, namespaces, error
  isolation; `tests/test_cli_plugin.py` — the terminal's surface.
- `tests/test_commands.py` — the terminal's commands against a real
  conversation, asserting the notices they published;
  `tests/test_builtin_plugins.py` — the host's built-in plugins the same way;
  `tests/test_cache_protect.py` — the pinned request prefix and the diff
  notices that announce its drift; `tests/test_display.py` — a whole turn
  through `CLIRenderer`, no TTY.
- `mocode.testing` — the public test kit every plugin test is written with:
  `MockProvider` and the chunk-replay helpers, `say` / `call_tool` script
  builders, `collect` / `terminal` / `events_of_type` readers. Its own
  contract is pinned in `tests/test_testing.py`; `tests/test_state.py` pins
  `RunState`'s bounded content window.
