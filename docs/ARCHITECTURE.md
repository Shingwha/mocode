# Architecture

MoCode is a small agent kernel, a runtime that holds conversations, and a plugin
host. The organising rule is simple:

> **A concept that could be written as a plugin does not belong in the kernel.**

Workflows, sub-agents, context compaction, web fetch, virtual file systems — all of these are capabilities, so none of them live in `core/`. The kernel knows how to run an LLM loop with tools, hooks and a prompt; everything else arrives through the plugin API.

```
mocode/
├── core/                the kernel — mechanism only, no app dependencies
│   ├── agent.py         AgentConfig, AgentLoop (start/stream/chat/derive), Turn
│   ├── channel.py       EventChannel, Subscription — the run's event stream
│   ├── events.py        Event + the eleven events a run emits
│   ├── state.py         RunState — the events folded into a live snapshot
│   ├── builder.py       Agent — fluent builder, the bare-metal entry point
│   ├── hook.py          AgentHook, HookRunner, contexts — the interception channel
│   ├── prompt.py        Prompt, Section — section-based XML assembly
│   ├── provider.py      Provider protocol, Chunk/Response DTOs, with_retry_stream
│   └── tool.py          Tool, ToolError, ToolRegistry
├── host/                the layer an application embeds
│   ├── runtime.py       MoCode — the process runtime: config, plugins, sessions
│   ├── conversation.py  Conversation — one project, one model, one history
│   ├── events.py        ConversationChanged — what the conversation announces
│   ├── command.py       Command, CommandRegistry, CommandResult, dispatch()
│   ├── config.py        Config, ProviderEntry, ModelEntry
│   ├── session.py       Session, SessionStore, export/import
│   ├── export.py        Session → Markdown
│   ├── text.py io.py    byte decoding, JSON I/O
│   ├── prompt.py        build_system_prompt(ctx)
│   └── plugin/          Plugin, HostContext, loader, PluginHost
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
├── cli_args.py main.py  argument parsing and process entry
```

## Layering rules

Dependencies only ever point down: `core ← host ← cli`. `core` and `providers`
import nothing above them; `host` never imports `cli`.

| Rule | Why |
|---|---|
| `core/` never imports `host/`, `cli/`, `providers/` or `plugins/` | the kernel stays embeddable and testable without an application |
| `core/` contains no tool names, no feature names, no config keys beyond `AgentConfig` | anything feature-specific is a plugin's business |
| `host/` contains no terminal vocabulary — no ANSI, no prompt, no screen | an application can embed it without inheriting a terminal |
| There is exactly one way to execute a turn — `AgentLoop.start()`; `stream()` and `chat()` are views over it | every consumer sees the same event stream, so no feature needs its own hook into the loop |
| There is exactly one way to construct an agent — the `Agent` builder or `AgentLoop.derive()` | three hand-rolled construction sites was how the old code drifted |
| Tools, hooks, prompt sections and shared commands are contributed only through `Plugin.build(ctx)` | the host hard-codes none of them |
| A component that prints is a subscriber to the event stream, never a special case in the loop | the terminal is one reader among several |

## The kernel

A conversation runs one turn at a time, and `AgentLoop.start()` begins one:

```python
turn = agent.start("list the tests")     # raises if a turn is already running
async for event in turn.subscribe():
    ...
terminal = await turn.wait()             # RunFinished or RunFailed
turn.cancel()                            # stop it from anywhere
```

`stream()` and `chat()` are conveniences over that: they run a turn and scope it
to the caller — stop reading, or be cancelled, and the turn stops with you. A
turn started with `start()` belongs to the conversation instead, which is what a
server needs: a client that disconnects does not kill the run, and a client that
reconnects can pick it up.

**Every event goes into the conversation's channel** (`core/channel.py`): one
ordered stream per conversation, with a `seq` that keeps counting across turns.
That single decision is what gives an application

* **many readers** — a renderer, a status page, a logger, a test;
* **replay** — `subscribe(since=seq)` hands a returning reader everything it
  missed, and a gap in `seq` says honestly when it could not;
* **no back-pressure from a slow reader** — a subscription has a bounded backlog
  and drops its oldest events rather than holding up the model;
* **a place for a message that has nothing to do with a run** — a plugin saying
  something between turns publishes into the same channel.

Two delivery policies, one publish path: a hook's `on_event` is an *inline*
subscription (the publisher waits for it, because a hook must see the event
before the run moves on), everything else is buffered and nobody waits.

`RunState` is the same stream folded into a snapshot, for callers that want to
ask "what is happening now" — `conversation.state.to_dict()` is the shape to put
behind an HTTP status endpoint.

Four primitives make features-as-plugins possible:

- **The event stream** — one typed, serialisable contract for everything the loop does. A renderer, a plugin, a test or an embedding application all consume the same stream; nobody needs a private callback.
- **`AgentLoop.derive(*, system_prompt, tools, hooks, config, model, channel)`** — creates an independent agent sharing this one's provider. This is the whole basis of sub-agents, workflow nodes, or any "run a nested agent with narrower tools" idea; pass `channel=` to have the sub-agent report into the parent's stream.
- **Interceptable hooks** — `on_tool_start` may rewrite `ctx.tool_args` or set `ctx.deny` to veto a call; `on_tool_complete` may rewrite `ctx.tool_result`. `ctx.status` records the outcome (`ok` / `error` / `timeout` / `denied` / `not_found`) so no one parses result strings.
- **Tool metadata** — `Tool(tags=..., summary_key=...)` plus `ToolRegistry.select(...)`. Capability scoping is by tag (`exclude_tags={"delegation"}`), never by a hard-coded list of tool names.

## Events and hooks are different jobs

They are deliberately not unified, because they run in opposite directions:

| | Events | Hooks |
|---|---|---|
| direction | one-way notification | request → response |
| purpose | say what happened | decide what happens |
| who consumes | any number of readers | the loop itself |
| cost of a slow consumer | it falls behind (and is told so) | the loop waits |

So observation always goes through the stream, and anything that must *answer* —
rewrite messages, veto a call, redact a result — is a hook. A hook's decision is
observable anyway, because it shows up in the events it caused: a vetoed call is
a `ToolCallFinished` with `status="denied"`.

Ordering is fixed so a consumer never sees a stale view: `on_tool_start` runs
first, and `ToolCallStarted` is published with the *final* arguments;
`on_tool_complete` runs before `ToolCallFinished`.

## Run lifecycle

```
start(prompt) → Turn
  └─ the run, published into the conversation's channel
      ├─ RunStarted                     model + visible tools
      ├─ per iteration:
      │   ├─ before_iteration(ctx)      intercept: ctx.messages is writable
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

`HookRunner` fans out with per-hook error isolation: a raising hook is logged and
skipped, never fatal. A cancelled turn still ends with `RunFinished`, so no
reader is left waiting for an ending that never comes; the history is left
replayable, with an answer for every assistant tool call.

## The host

`MoCode` is the process runtime an application embeds; `Conversation` is the
unit it works with.

```python
from mocode import MoCode

mc = MoCode()                                       # config → plugins → store
conv = mc.new_conversation(cwd="/srv/proj-a")       # one conversation
async for event in conv.chat("..."):
    ...
conv.state.to_dict()                                # live snapshot
conv.save()                                         # → ~/.mocode/sessions/…
```

**Everything that differs between two conversations lives on the conversation**,
not on the runtime: which directory the tools work in, which provider the model
calls go to, which plugins were built (and therefore which shell session, skill
index and tool instances exist), what has been said, and which session id it
will be saved as. A conversation's plugins are *built* for it —
`load_plugins()` is the per-project half (discovery, import, enable/disable,
cached by `MoCode`), `PluginHost.build_all()` is the per-conversation half.

The runtime keeps no registry of live conversations: the identity a conversation
has in an application — a route, a tab, a socket — is that application's
business. `mc.store` is the session store (`list(workdir)`, `list_all()`,
`find(session_id)`), and `mc.resume(session_id)` opens a stored one wherever its
project was.

**The host contributes no commands of its own.** `conversation.commands` starts
empty and fills with whatever plugins register — `/skill:<name>` from the skills
plugin, anything a third-party plugin adds. `/help`, `/model`, `/resume` and the
rest need a picker or a clipboard, so they belong to the terminal, which
registers them through its own plugin interface (`cli/plugin.py`).

The system prompt is assembled from framework sections (`guidelines`, `agents`,
`environment`, `tools`) plus `ctx.prompt_sections` contributed by plugins,
rendered in `(priority, insertion order)` order so stable content stays in front
of volatile content for prefix caching.

## Plugins

A plugin directory follows the [Agent Plugins](https://agent-plugins.org)
standard: the root holds what any compatible client understands, and everything
client-specific lives under a directory named for the namespace that defines it.

```
git-status/
├── plugin.json              the manifest: name, version, description
├── skills/commit/SKILL.md   portable skills
├── mcp.json                 MCP servers (recognised, not served yet)
├── mocode/plugin.py         contributions to the agent — every frontend
└── mocode.cli/plugin.py     contributions to the terminal — this frontend only
```

Two surfaces, and the difference is who can use the result:

| | `mocode/plugin.py` | `mocode.cli/plugin.py` |
|---|---|---|
| interface | `Plugin.build(ctx)` | `CLIPlugin.build(cli)` |
| contributes | tools, prompt sections, hooks, shared commands | chrome: picker commands, keybindings |
| reaches | `ctx` — home, cwd, config, model, tools, commands, hooks, `plugin_sources`, the agent | the terminal — `commands`, `display`, `input`, `conversation` |
| works in | every MoCode frontend | this one |

The host hands the namespace directory over without reading it
(`PluginSpec.directory` → `ctx.plugin_sources`), so `host/` still knows nothing
about terminals, and a plugin written for the terminal travels to a web frontend
that simply does not read `mocode.cli`.

## The terminal

`CLIApp` is `MoCode` plus one `Conversation` plus a terminal: a REPL, input,
slash-command dispatch and Ctrl-C. It contributes nothing to the host — its
commands are registered through its own plugin interface, and its rendering is a
subscription.

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

**What a turn looks like** is a vocabulary, not a layout engine:

```
❯ 用 bash 数一下 mocode 下有多少个 py 文件

The user wants to count the .py files. Let me run find.
· bash  find mocode -name '*.py' | wc -l…          ← while it runs, dim
✓ bash  find mocode -name '*.py' | wc -l · exit_code=0 · 0.1s   ← the same row
mocode/ 下共有 47 个 .py 文件。
↑1,234 ↓567 tokens
────────────────────────────────────────
```

Everything starts at column 0 and the first character says what the line is.
There is no indentation, because a terminal has no hanging indent — a long line
wraps back to column 0 regardless, and it does so most often on the content that
needs it least. The answer is unmarked and left at the default foreground, so it
is the brightest thing on screen. A rule closes each turn, drawn when the turn
ends rather than when the next prompt arrives, with what the turn cost on the
line above it.

A tool call claims a row the moment it starts and keeps it: a dim `· name  args…`
placeholder that is rewritten in place with the verdict. A slow, quiet tool
therefore shows that it is running without costing a line, and a parallel batch
stays one row per call in the order the calls were made rather than the order
they finish — which is also why a call's own output is not printed. `ToolOutput`
still reaches the model and every other consumer; the terminal just does not
draw it.

Rewriting a row is only sound while the block is the last thing on screen, so
`Display` guards it rather than trusting it: block lines are clamped to one
terminal row (`clamp_visible`), any other output freezes the block for good, and
a block taller than the screen stops claiming rows. Off a terminal
(`Display.live`) the whole mechanism is off and a call appends its verdict when
it finishes. The terminal's live path is in `display.py`, so everything above
holds for `lines.py` regardless.

`render` decides whether a frontend is attached, and it can only ask for one,
never remove one — `interactive` already implies it. `main.py` passes
`sys.stdout.isatty()`, so `mocode -p "…"` draws its turn on a terminal and stays
a plain, escape-free pipe when redirected. A modal dialog is drawn only where
there is someone to answer it: `dialogs.select` returns `None` when either end is
not a terminal, and the commands already treat that as "nothing was chosen".

## Data flow

```
user input → CLIApp._dispatch ─┬─ slash command → handler(CommandContext) → CommandResult
                               └─ prose → conversation.run() → AgentLoop.start()
                                                            ├─ provider.stream
                                                            └─ events → EventChannel
                                                                         ├─ Subscription → CLIRenderer → Display
                                                                         └─ Subscription → your application
```

The same path serves an embedding application, minus the terminal:

```
your app → conversation.run() → Turn → channel → your subscription
```

## Testing

- `tests/providers.py` holds `MockProvider`, which replays canned `Response` objects as chunk streams — arguments split across chunks, exactly as a real API sends them. `chunk_size=1` makes the incremental path visible.
- `tests/test_agent_loop.py` covers the loop; `tests/test_channel.py` covers the channel on its own (ordering, replay, lagging readers, closing); `tests/test_events.py` covers the event contract and `RunState` with no loop involved.
- `tests/test_conversations.py` covers what the runtime exists for: N conversations at once, in different projects, on different models, sharing nothing.
- `tests/test_plugins.py` covers the layout, the manifest rules, namespaces, and error isolation; `tests/test_cli_plugin.py` covers the terminal's surface. Plugin fixtures are written to `tmp_path`.
- `tests/test_commands.py` runs the terminal's commands against a real conversation and asserts the notices they published, which is what a user would have seen.
- `tests/test_display.py` runs a whole turn through `CLIRenderer` and asserts what reached the screen, so the renderer is covered without a TTY.
