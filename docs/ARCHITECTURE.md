# Architecture

MoCode is a small agent kernel plus a plugin host. The organising rule is simple:

> **A concept that could be written as a plugin does not belong in the kernel.**

Workflows, sub-agents, context compaction, web fetch, virtual file systems — all of these are capabilities, so none of them live in `core/`. The kernel knows how to run an LLM loop with tools, hooks and a prompt; everything else arrives through the plugin API.

```
mocode/
├── core/                the kernel — mechanism only, no app dependencies
│   ├── agent.py         AgentConfig, LoopResult, AgentLoop (incl. stream/derive)
│   ├── events.py        Event + the eleven events a run emits
│   ├── state.py         RunState — the events folded into a live snapshot
│   ├── builder.py       Agent — fluent builder, the bare-metal entry point
│   ├── hook.py          AgentHook, HookRunner, contexts — the interception channel
│   ├── prompt.py        Prompt, Section — section-based XML assembly
│   ├── provider.py      Provider protocol, Chunk/Response DTOs, with_retry_stream
│   └── tool.py          Tool, ToolError, ToolRegistry
├── host/                the layer an application embeds
│   ├── runtime.py       MoCode — the runtime an application drives
│   ├── command.py       Command, CommandRegistry, CommandResult — invocable actions
│   ├── frontend.py      Frontend protocol — what a plugin may ask of a display
│   ├── config.py        Config, ProviderEntry, ModelEntry
│   ├── session.py       Session, SessionStore, SessionManager
│   ├── export.py        Session → Markdown
│   ├── text.py io.py    text helpers, JSON I/O
│   ├── prompt.py        build_system_prompt(ctx) — framework sections + plugin sections
│   └── plugin/          Plugin, HostContext, PluginHost, loader
│       └── builtin/     the plugins MoCode ships: filesystem, shell, skills
├── cli/                 the terminal front-end — a consumer of host/
│   ├── app.py           CLIApp — the REPL, dispatch and Ctrl-C
│   ├── plugin.py        the terminal's own plugin: its commands
│   ├── hook.py          CLIDisplayHook — renders the event stream
│   ├── lines.py         Line + builders — what a turn looks like, as data
│   ├── display.py       Display (primitives + Frontend), theme, input, dialogs
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
| `cli/` contributes only through `Plugin.build(ctx)`, like any third party | the terminal is a frontend, not a privileged layer |
| There is exactly one way to execute a turn — `AgentLoop.stream()`; `chat()` is a wrapper over it | every consumer sees the same event stream, so no feature needs its own hook into the loop |
| There is exactly one way to construct an agent — the `Agent` builder or `AgentLoop.derive()` | three hand-rolled construction sites was how the old code drifted |
| Tools, commands, hooks and prompt sections are all contributed through `Plugin.build(ctx)` | the host hard-codes none of them |
| A component that prints is a consumer of the event stream, never a special case in the loop | the terminal is one renderer among several |

### How the terminal stays out of the host

Two seams make `host/` frontend-agnostic:

- **`Frontend`** — a four-method protocol (`info` / `warn` / `error` /
  `conversation_changed`). A plugin with something to say asks this, and the
  host never learns that a terminal exists. It also gets `ctx.display = None`,
  which is the only signal for "headless".
- **`extra_plugins`** — the CLI's own plugin renders a terminal, so the terminal
  passes it in rather than `builtin_plugins()` knowing about it. The CLI's
  contributions reach the host through the same channel a third-party plugin's
  do.

## The kernel

`AgentLoop.stream()` runs one turn and yields an ordered `Event` per thing that happens: text as it arrives from the provider, tool calls as they start and finish, usage per iteration, and a terminal `RunFinished` or `RunFailed`. `chat()` drains the same stream and returns the final answer.

Dependencies (`provider`, `system_prompt`, `tools`, `hooks`, `config`) are constructor-injected. `provider`, `system_prompt`, `messages` and `state` are public and mutable.

Four primitives make features-as-plugins possible:

- **The event stream** — one typed, serialisable contract for everything the loop does. A renderer, a plugin, a test or an embedding application all consume the same stream; nobody needs a private callback. `RunState` is the same stream folded into a queryable snapshot.
- **`AgentLoop.derive(*, system_prompt, tools, hooks, config)`** — creates an independent agent sharing this one's provider. This is the whole basis of sub-agents, workflow nodes, or any "run a nested agent with narrower tools" idea.
- **Interceptable hooks** — `on_tool_start` may rewrite `ctx.tool_args` or set `ctx.deny` to veto a call; `on_tool_complete` may rewrite `ctx.tool_result`. `ctx.status` records the outcome (`ok` / `error` / `timeout` / `denied` / `not_found`) so no one parses result strings.
- **Tool metadata** — `Tool(tags=..., summary_key=...)` plus `ToolRegistry.select(...)`. Capability scoping is by tag (`exclude_tags={"delegation"}`), never by a hard-coded list of tool names.

## Events and hooks are different jobs

They are deliberately not unified, because they run in opposite directions:

| | Events | Hooks |
|---|---|---|
| direction | one-way notification | request → response |
| purpose | say what happened | decide what happens |
| who consumes | any number of observers | the loop itself |
| cost of a slow consumer | it falls behind | the loop waits |

So observation always goes through the stream, and anything that must *answer* — rewrite messages, veto a call, redact a result — is a hook. A hook's decision is observable anyway, because it shows up in the events it caused: a vetoed call is a `ToolCallFinished` with `status="denied"`.

Ordering is fixed so a consumer never sees a stale view: `on_tool_start` runs first, and `ToolCallStarted` is published with the *final* arguments; `on_tool_complete` runs before `ToolCallFinished`.

## Run lifecycle

```
stream(input)
  └─ loop
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
      └─ RunFinished | RunFailed
```

`HookRunner` fans out with per-hook error isolation: a raising hook is logged and skipped, never fatal.

Cancelling the task that consumes the stream tears the turn down and leaves the conversation replayable — every assistant tool call still gets an answer.

## The host

`MoCode` (`host/runtime.py`) is the composition root an application embeds:

```python
from mocode import MoCode

mc = MoCode()                       # config → plugins → agent → session
async for event in mc.chat("..."):
    ...
mc.state                            # live snapshot
```

`PluginHost` discovers the built-in plugins plus anything in `./.mocode/plugins/` and `~/.mocode/plugins/`, runs each `build(ctx)` inside a try/except, builds the system prompt from the now-complete tool/skill set, and constructs the agent. `ctx.agent` is assigned last — plugins that need the agent keep the context and read `ctx.agent` at call time, which is what removes the old two-phase assembly.

`ctx.display` is the only signal for "a frontend is attached", and it is typed as the `Frontend` protocol rather than as a terminal. There is no separate `interactive` flag: tools, prompt sections and commands are worth having in a headless run too.

**The host contributes no commands of its own.** `MoCode.commands` starts empty and fills with whatever plugins register — `/skill:<name>` from the skills plugin, anything a third-party plugin adds. `/help`, `/model`, `/resume` and the rest need a picker, a clipboard or a screen, so they are the terminal's, registered by `cli/plugin.py`.

The system prompt is assembled from framework sections (`guidelines`, `agents`, `environment`, `tools`) plus `ctx.prompt_sections` contributed by plugins, rendered in `(priority, insertion order)` order so stable content stays in front of volatile content for prefix caching.

`CLIApp` is `MoCode` plus a terminal: a REPL, input, slash-command dispatch and Ctrl-C, plus `cli/plugin.py` for its commands and its own `CLIDisplayHook`, installed on the agent it built rather than contributed by a plugin. `CLIApp` holds no logic the host needs: commands reach the runtime directly through `CommandContext.app`.

`CLIDisplayHook` is a pure event consumer — it implements `on_event` and no interception hooks at all. It owns only the state of the run *as it is being drawn*: which calls are in flight, and which row on screen each of them owns. Every shape it draws comes from `cli/lines.py`, as data — so the live renderer and history replay cannot drift apart, and neither needs a terminal to be tested.

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

Everything starts at column 0 and the first character says what the line is. There is no indentation, because a terminal has no hanging indent — a long line wraps back to column 0 regardless, and it does so most often on the content that needs it least. The answer is unmarked and left at the default foreground, so it is the brightest thing on screen. A rule closes each turn, drawn when the turn ends rather than when the next prompt arrives, with what the turn cost on the line above it.

A tool call claims a row the moment it starts and keeps it: a dim `· name  args…` placeholder that is rewritten in place with the verdict. A slow, quiet tool therefore shows that it is running without costing a line, and a parallel batch stays one row per call in the order the calls were made rather than the order they finish — which is also why a call's own output is not printed. `ToolOutput` still reaches the model and every other consumer; the terminal just does not draw it.

Rewriting a row is only sound while the block is the last thing on screen, so `Display` guards it rather than trusting it: block lines are clamped to one terminal row (`clamp_visible`), any other output freezes the block for good, and a block taller than the screen stops claiming rows. Off a terminal (`Display.live`) the whole mechanism is off and a call appends its verdict when it finishes. The terminal's live path is in `display.py`, so everything above holds for `lines.py` regardless.

`render` decides whether a frontend is attached, and it can only ask for one, never remove one — `interactive` already implies it. `main.py` passes `sys.stdout.isatty()`, so `mocode -p "…"` draws its turn on a terminal and stays a plain, escape-free pipe when redirected.

## Data flow

```
user input → CLIApp._dispatch ─┬─ slash command → handler(CommandContext) → CommandResult
                               └─ prose → MoCode.chat() → AgentLoop.stream()
                                                            ├─ provider.stream
                                                            ├─ events ───┬─ CLIDisplayHook → Display
                                                            │            └─ your application
                                                            └─ hook interception
```

The same path serves an embedding application, minus the terminal:

```
your app → MoCode.chat() → AgentLoop.stream() → events → your consumer
```

## Testing

- `tests/providers.py` holds `MockProvider`, which replays canned `Response` objects as chunk streams — arguments split across chunks, exactly as a real API sends them. `chunk_size=1` makes the incremental path visible.
- `tests/test_agent_loop.py` covers the loop; `tests/test_events.py` covers the event contract and `RunState` on their own, with no loop involved.
- `tests/test_plugins.py` covers discovery, enable/disable, extra plugins, and error isolation; plugin fixtures are written to `tmp_path`.
- `tests/test_runtime.py` drives `MoCode` against a `tmp_path` session store and a mock provider, and checks that a `CLIApp` is that runtime plus the terminal's own plugin.
- `tests/test_display.py` runs a whole turn through `CLIDisplayHook` and asserts what reached the screen, so the renderer is covered without a TTY.
