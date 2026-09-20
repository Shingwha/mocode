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
│   ├── events.py        Event + the eleven events a run emits
│   ├── state.py         RunState — the events folded into a live snapshot
│   ├── hook.py          AgentHook, HookRunner, contexts — the interception channel
│   ├── prompt.py        Prompt, Section — section-based XML assembly
│   ├── provider.py      Provider protocol, Chunk/Response DTOs, with_retry_stream
│   └── tool.py          Tool, ToolError, ToolRegistry
├── host/                the layer an application embeds
│   ├── runtime.py       MoCode — the process runtime: config, plugins, sessions
│   ├── conversation.py  Conversation — one project, one model, one history
│   ├── events.py        ConversationChanged — the host's own event
│   ├── command.py       Command, CommandRegistry, CommandResult, dispatch()
│   ├── config.py        Config, ProviderEntry, ModelEntry
│   ├── session.py       Session, SessionStore
│   ├── export.py        Session → Markdown
│   ├── prompt.py        build_system_prompt(ctx)
│   └── plugin/          Plugin, HostContext, loader, PluginHost, env (a plugin's
│                        own uv environment), install (plugin install/sync/list/remove)
│       └── builtin/     the plugins MoCode ships: filesystem, shell, skills
├── cli/                 the terminal front-end — a consumer of host/
│   ├── app.py           CLIApp — the REPL, dispatch and Ctrl-C
│   ├── plugin.py        CLIPlugin — the terminal's own extension surface
│   ├── render.py        CLIRenderer — the event stream, as terminal lines
│   ├── lines.py         Line + builders — what a turn looks like, as data
│   ├── display.py       Display — terminal primitives; theme, text, input, dialogs
│   └── commands/        /quit /help /clear /copy /model /export /resume
├── providers/openai.py  OpenAI-compatible streaming provider
├── plugins/__init__.py  the public SDK third-party plugins import
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

`RunState` is the same stream folded into a snapshot, for callers that want to
ask "what is happening now" — `conversation.state.to_dict()` is the shape to
put behind an HTTP status endpoint. Folding is guarded (by `seq`, by `run_id`),
so a reconnect replay never double-counts and another run sharing the channel
never folds into yours.

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
  prompt rewrite is scoped to the run — restored when the turn ends).
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
`on_tool_complete` runs before `ToolCallFinished`.

## Run lifecycle

```
start(prompt) → Turn
  └─ the run, published into the conversation's channel
      ├─ RunStarted                     model + visible tools
      ├─ per iteration:
      │   ├─ before_iteration(ctx)      intercept: messages / system_prompt writable
      │   ├─ IterationStarted
      │   ├─ provider.stream(...)       retried by with_retry_stream
      │   │   └─ TextDelta / ReasoningDelta as chunks arrive
      │   ├─ IterationFinished          usage + stop reason
      │   └─ per tool call, in parallel:
      │       ├─ on_tool_start(ctx)     intercept: rewrite args / deny
      │       ├─ ToolCallStarted        with the final args
      │       ├─ ToolOutput             as a streaming tool produces output
      │       ├─ on_tool_complete(ctx)  intercept: rewrite result
      │       └─ ToolCallFinished       status, result, duration
      └─ RunFinished (cancelled=True when stopped) | RunFailed
```

Every turn ends with exactly one terminal event — including one that was
cancelled, and including one that died on a `BaseException`: the failure is
parked on `turn.failure` and the readers still get their `RunFailed`.
`HookRunner` fans out with per-hook error isolation: a raising hook is logged
and skipped, never fatal. A cancelled turn leaves the history replayable, with
an answer for every assistant tool call.

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

**The host contributes no commands of its own.** `conversation.commands`
starts empty and fills with whatever plugins register — `/skill:<name>` from
the skills plugin, anything a third-party plugin adds. `/help`, `/model`,
`/resume` and the rest need a picker or a clipboard, so they belong to the
terminal, which registers them through its own plugin interface
(`cli/plugin.py`).

The system prompt is assembled from framework sections (`guidelines`,
`agents`, `environment`, `tools`) plus `ctx.prompt_sections` contributed by
plugins, rendered in `(priority, insertion order)` order so stable content
stays in front of volatile content for prefix caching. Framework sections win
a name collision.

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
  `tests/test_display.py` — a whole turn through `CLIRenderer`, no TTY.
- `tests/providers.py` — `MockProvider` and the chunk-replay helpers.
