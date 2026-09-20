# Embedding MoCode

MoCode is a Python library before it is a CLI. `MoCode` is a runtime you embed:
it holds the config, the plugin loading and the session store, and it opens
**conversations**. A conversation is one project, one model, one history and
one event stream — start as many as you like, at the same time. Nothing prints
and nothing knows about a terminal, so an editor, a web backend, a test
harness and the built-in CLI are all the same kind of consumer.

```python
from mocode import MoCode   # lazy — about a millisecond until you construct one
```

## Quick start

```python
import asyncio
from mocode import MoCode
from mocode.core import RunFinished, TextDelta, ToolCallFinished, ToolCallStarted


async def main():
    mc = MoCode()                    # ~/.mocode/config.json
    conv = mc.new_conversation()     # in the current directory

    async for event in conv.stream("what does tests/test_agent_loop.py cover?"):
        match event:
            case TextDelta(text=text):
                print(text, end="", flush=True)
            case ToolCallStarted(name=name):
                print(f"\n→ {name}")
            case ToolCallFinished(name=name, status=status):
                print(f"  {name}: {status}")
            case RunFinished(content=answer):
                print(f"\n\n{answer}")


asyncio.run(main())
```

`MoCode()` raises `ValueError` if there is no usable config at
`~/.mocode/config.json`; `Config.load()` is exported from `mocode.host` if you
want to check first.

### The runtime

```python
mc = MoCode(
    config: Config | None = None,             # None loads ~/.mocode/config.json
    home: Path | None = None,                 # plugins, skills, sessions
    plugin_dirs: Sequence[Path] | None = None, # default: <cwd>/.mocode/plugins, <home>/plugins
    freeze_interface: bool = True,            # pin each conversation's tool interface
)
```

`freeze_interface=False` hands the loop the live registry instead of a pinned
one: a tool switched off mid-conversation then changes the very next request,
at the cost of that request's prefix cache. The default pins — and the
`cache-protect` plugin announces every change as a `[context update]` notice,
so the model hears about it either way.

mc.config                            # the Config everything shares
mc.store                             # SessionStore: list(workdir) / list_all() / find(id)
mc.plugins_for(cwd)                  # the plugins a project loads (cached per project)
mc.provider_for(key, model)          # build a provider for a pair
mc.set_default_model(key, model)     # the only thing that writes config.json
```

### Opening a conversation

```python
conv = mc.new_conversation(
    cwd: Path | str | None = None,       # None: the process working directory
    provider: str | None = None,         # None: the config's active provider
    model: str | None = None,            # None: the config's active model
    commands: CommandRegistry | None = None,
    session: Session | None = None,      # continue a stored one
)
```

Everything the conversation needs follows from `cwd` and the provider/model:
its plugins (loaded once per project, built once per conversation), its tools
(relative paths resolve into that project, `bash` starts there), its system
prompt, and the session it will be saved as. Two conversations in one process
share the config, the plugin loading and the session store — and nothing else.
`mc.resume(session_id)` is the one-line way back into a stored conversation.

Opening is cheap by design: plugins *register* in `build()` and finish their
I/O in `prepare()`, and the request surface — system prompt plus offered tool
interface — is materialized once, at the top of the first turn (or, on a
resume, byte-identically from the session). The first `chat`/`stream`/`run`
does that on its own. An application that wants the surface sooner — to read
`conv.agent.system_prompt`, to inspect `conv.tools` as the model will be
offered them — awaits it explicitly:

```python
await conv.prepare()   # materialize now; idempotent, and what a turn does
```

## The event stream

A conversation has one stream, and every turn publishes into it. Events are
plain data: each has a `type` string, a `run_id`, a monotonic `seq`, and a
`to_dict()` that flattens it into JSON-ready values.

| Event | Carries | When |
|---|---|---|
| `RunStarted` | `model`, `tools` | the turn begins |
| `IterationStarted` | `iteration` | one LLM call is about to happen |
| `TextDelta` | `text` | a fragment of the answer |
| `ReasoningDelta` | `text` | a fragment of the model's reasoning trace |
| `IterationFinished` | `iteration`, `usage`, `stop_reason` | the response is complete |
| `ToolCallStarted` | `call_id`, `name`, `args` | a tool is about to run; args are final |
| `ToolOutput` | `call_id`, `text`, `stream` | a running tool produced output |
| `ToolCallFinished` | `call_id`, `name`, `status`, `result`, `error_code`, `duration` | a tool finished |
| `RunFinished` | `content`, `usage`, `iterations`, `tool_calls_made`, `cancelled` | the turn ended |
| `RunFailed` | `error`, `kind` | the turn ended on an unhandled error |
| `Notice` | `message`, `level` | a plugin or the host wants to say something |

The host adds one event of its own outside the kernel: `ConversationChanged`
(`mocode.host.events`) — the history was replaced, re-read the conversation.

Four rules make the stream safe to build on:

- **Text arrives in fragments; concatenate them in `seq` order.** If you only
  want the final answer, read `RunFinished.content` and ignore deltas.
- **A tool call is an object with an identity, not a return value.** All three
  tool events carry the same `call_id`, which correlates with the tool message
  in `conv.messages` (`tool_call_id`).
- **A turn ends with exactly one of `RunFinished` or `RunFailed`** — including
  a cancelled turn, which reports `cancelled=True`. Tool `status` is one of
  `ok` / `error` / `timeout` / `denied` / `not_found`.
- **`seq` keeps counting across turns.** A reader that remembers a number can
  always ask for what it missed.

### Reading it

```python
reader = conv.subscribe()                 # live from now on
async for event in reader:
    ...
```

`conv.subscribe(since=seq)` replays everything the channel still remembers
after that number before continuing live — that is how a reconnecting client
catches up, and `Subscription.dropped` (plus the gaps you can see in `seq`)
says honestly when it could not. A reader never slows the run down: it has a
bounded backlog and loses its oldest events rather than holding up the model.

A turn is its own window on the same stream:

```python
turn = conv.run("summarize the diff")     # raises if a turn is already running
async for event in turn.subscribe():      # this turn's events, replay included
    ...
terminal = await turn.wait()              # RunFinished or RunFailed
turn.cancel()                             # stop it — from any coroutine
```

`conv.run()` belongs to the conversation: a client that disconnects does not
kill it, and a second client can join with `conv.subscribe()` or
`turn.subscribe()` while it runs. `conv.stream()` is the scoped convenience —
stop reading, or be cancelled, and the turn stops with you — and
`await conv.chat(prompt)` consumes the stream and returns
`RunFinished.content`, raising on provider failure.

## Getting the work state

The stream is push. For pull — "what is happening right now" — read
`conv.state`, a `RunState` the loop keeps live by folding every event into it
as it is published:

```python
conv.state.status                 # "idle" | "running" | "done" | "failed" | "cancelled"
conv.state.iteration              # which LLM call
conv.state.content                # everything streamed this turn
conv.state.answer                 # the final answer ("" if the turn did not finish)
conv.state.usage                  # Usage, summed over the turn
conv.state.running_tool_calls     # [ToolCallState, ...] still executing
conv.state.failed_tool_calls      # those that did not end "ok"
conv.state.tool_calls["c1"]       # one call by id
```

Each `ToolCallState` has `name`, `args`, `status`, `result`, `details`,
`error_code`, `duration`, and `output` / `output_text` — everything the tool
streamed while it ran. `result` is what went to the model; `details` is
structured data for *you* — it never entered the conversation.

Because it is just a reducer over the events, you can hold your own:

```python
from mocode.core import RunState

mirror = RunState()
async for event in conv.subscribe():
    mirror.apply(event)             # identical to conv.state once the turn is done
```

Folding is guarded, so this is safe across reconnects: an event replayed from
a `seq` you had already folded is ignored, and events from another run sharing
the channel — a derived agent's — do not fold into yours. One `RunState` is one
run's view, not the channel's. `conv.state.to_dict()` gives the whole snapshot
as plain data, which is the shape to put behind an HTTP status endpoint.

## Many conversations at once

This is what the runtime exists for. Every conversation has its own project,
its own model, its own plugins (built for it, so its shell session and skill
index are its own), its own history and its own stream:

```python
frontend = mc.new_conversation(cwd="/srv/web", provider="intern", model="Atlas")
backend  = mc.new_conversation(cwd="/srv/api", provider="intern", model="Atlas")

await asyncio.gather(                       # both at once, one process
    frontend.chat("run the tests"),
    backend.chat("update the changelog"),
)
```

What a web backend does with that — keep your own dictionary keyed by
`conv.id`; the runtime deliberately keeps no registry of live conversations:

```python
live: dict[str, Conversation] = {}

@app.post("/conversations/{cid}/events")          # SSE
async def events(cid: str, since: int = 0):
    async for event in live[cid].subscribe(since=since or None):
        yield f"data: {json.dumps(event.to_dict())}\n\n"

@app.get("/conversations/{cid}/status")
async def status(cid: str):
    return live[cid].state.to_dict()

@app.post("/conversations/{cid}/stop")
async def stop(cid: str):
    live[cid].cancel()
```

The transport is yours; the host gives you an addressable run, a resumable
stream, a snapshot and a stop button. Two things to know: a conversation runs
**one turn at a time** (a second `run()` raises — queue it or cancel first),
and two conversations opened on the same session id would fight over one file.

## Conversations and sessions

`conv.messages` is the live conversation in OpenAI message format. Sessions
persist messages, not events — the message list is what the provider needs:

```python
conv.save(title="optional")       # persist to ~/.mocode/sessions/<hash>/<id>.json
await conv.new_session()          # save, then begin fresh in this project
await conv.new_session(messages)  # begin fresh, seeded from an export file
await conv.load_session(session)  # continue a stored one: history, id, model — and the
                                  # prompt and tool interface it ran with; drift arrives
                                  # as a history notice
conv.rebuild_prompt()             # re-render, re-pin the interface and re-freeze after
                                  # AGENTS.md changed on disk — the one deliberate cache loss
await conv.aclose()               # the full close: drain the turn, save, release, end
conv.close()                      # the sync emergency path — same, but does not wait

conv.list_sessions()              # [Session, ...] for this project, newest first
mc.store.list_all()               # every session in the store, across projects
mc.resume(session_id)             # open a stored session wherever its project was
```

Prefer `aclose()`: the turn's terminal event reaches every reader before the
channel closes, and plugins are released only after the run has stopped using
them. `close()` stays for `finally` blocks without a loop. A `Session` records
where it ran, which model it used, and when — which is what makes `resume()`
restore the model as well as the history.

## Swapping the model

```python
conv.set_model("intern", "Atria-Dawn-Preview")        # this conversation only
mc.set_default_model("intern", "Atria-Dawn-Preview")  # what new conversations start on
```

The two are deliberately separate: switching a conversation's model touches no
other conversation and writes no file; the only thing that writes config.json
is `set_default_model`. Unknown providers raise `ValueError` in both.

## Dispatching commands

Commands are a host concept, not a terminal one. `conv.commands` holds
whatever plugins registered — the skills plugin contributes `/skill:<name>`, a
third-party plugin can add more; the terminal's own `/help`, `/model`,
`/resume` live in *its* registry, because they need a picker or a clipboard.

```python
from mocode.host import dispatch, Kind

result = await dispatch("/skill:release", conversation=conv, commands=conv.commands)
if result.kind is Kind.PROMPT:
    async for event in conv.stream(result.prompt):
        ...
```

`dispatch()` is the shared resolver: a line that names a command runs it, and
anything else comes back as `Kind.PROMPT` for you to send as a user message.
`Kind.EXIT` means "end this interaction", which is yours to interpret. A
command handler gets the conversation, its arguments and the registry, and it
says things by publishing — `await ctx.conversation.notify("…")` — so it works
the same with a terminal, a browser or nothing at all watching.

## Observing and intercepting

**Observation goes through the stream.** Subscribe, or implement `on_event` on
a hook to be in-band — the loop waits for an in-band reader, so use it for
what must not be missed (a commit log, a gate); a subscription never holds the
loop up. The application-facing subscription is `conv.subscribe()`; a plugin
holds its `ctx` and calls `ctx.subscribe()` at call time, which is the same
stream.

Publish your own event with `await ctx.emit(...)`, from a hook, a tool, or
between runs through `await conv.notify("...")`. Give it a `Notice`, or
subclass `Event`: pick a `type` string as the discriminator, give it a
`summary()` so any frontend can show it without knowing the type, and `run_id`
and `seq` are stamped for you.

**Interception is separate, because it needs an answer.** Three hooks may
change what happens:

```python
class PermissionGate(AgentHook):
    async def before_iteration(self, ctx):
        ctx.system_prompt = BASE_PROMPT + today_context()

    async def on_tool_start(self, ctx):
        if ctx.tool_name == "bash" and "rm -rf" in ctx.tool_args.get("command", ""):
            ctx.deny = "destructive commands are not allowed here"

    async def on_tool_complete(self, ctx):
        if ctx.status == "ok":
            ctx.tool_result = redact(ctx.tool_result)
        ctx.tool_details["audited"] = True
```

`ctx.deny` vetoes the call — the string becomes the tool result, and the
stream reports `ToolCallFinished(status="denied")`. `ctx.tool_args`,
`ctx.tool_result` and `ctx.tool_details` are writable, as are `ctx.messages`
and `ctx.system_prompt` in `before_iteration`; a `system_prompt` rewrite lasts
for the rest of the run — the loop restores the prompt as it stood when the
turn ends, so readers between turns (an export, the next turn) see the
conversation's own prompt. Because a hook can
await, a gate that needs a *human* answer is just a hook that waits for one:

```python
class Approval(AgentHook):
    async def on_tool_start(self, ctx):
        if needs_approval(ctx.tool_name):
            ctx.deny = await ask_the_user(ctx.tool_name, ctx.tool_args)
```

A raising hook is logged and skipped; it never breaks the run. The full hook
list and the plugin-side view are in [plugins.md](plugins.md).

## Tools that report as they work, and facts the model doesn't need

A tool keeps the plain `(args) -> str` shape unless it declares a second
parameter, in which case it receives its `ToolCallContext` and can publish
output while it runs:

```python
async def slow_report(args, ctx):
    for i in range(args["steps"]):
        await asyncio.sleep(1)
        await ctx.emit(ToolOutput(call_id=ctx.tool_call_id, text=f"step {i}\n"))
    return "done"
```

Consumers get `ToolOutput` events live and can accumulate them from
`conv.state.tool_calls[call_id].output_text`. The built-in `bash` tool works
exactly this way. A tool that wants to stream must be async — a sync tool runs
in a worker thread, where it cannot await; for those, `ctx.cancelled` is the
cooperative signal that the loop stopped waiting (a timeout, or the turn being
cancelled) — check it in long loops.

When there is something *about* the result worth exposing besides the string
the model reads, return a `ToolResult`:

```python
def lint(args):
    issues = run_linter(args["path"])
    return ToolResult(
        content=render(issues),                   # the model reads this
        details={"issues": len(issues)},          # you read this
    )

tool = Tool("lint", "Lint a file",
            {"path": {"type": "string", "description": "File to lint"}},
            lint, summary_key="path", result_key="issues")
```

`details` reaches `ToolCallFinished.details` and `state.tool_calls[id].details`
and stops there — it is never sent to the model. `summary_key` / `result_key`
are display hints (`✓ lint  src/a.py · issues=3`); any consumer is free to
ignore them.

## Building a bare agent

`MoCode` is the batteries-included path. For the kernel alone — your own
provider, your own tools, no config file — construct the loop directly:

```python
from mocode.core import AgentConfig, AgentLoop, HookRunner, ModelSpec, ToolRegistry

agent = AgentLoop(
    provider=my_provider,
    system_prompt="You are a helpful assistant.",
    tools=ToolRegistry().register(tool_a).register(tool_b),
    hooks=HookRunner([my_hook]),
    config=AgentConfig(tool_timeout=60, max_iterations=20),
    model=ModelSpec(name="my-model", context_window=128_000),
)

turn = agent.start("hello")               # or: async for event in agent.stream("hello")
terminal = await turn.wait()
```

A sub-agent is `agent.derive(tools=..., system_prompt=...)` — an independent
agent with a fresh history. What it inherits by default are *copies*: the same
capability set in its own registry (a child disabling a tool reaches nothing
of the parent's) and its own policy object. Pass `provider=` (usually with
`model=`) to run it on a cheaper backend, and `channel=agent.channel` to have
it publish into the parent's timeline — the channel's subscribers see both
runs interleaved, while each agent's `Turn` views and `state` stay scoped to
its own run.

`examples/core/minimal.py` is this file, runnable end to end;
`examples/core/nested.py` is the derive() version of it.

### Serialising events

Events are already plain data, so crossing a process boundary is a loop:

```python
async for event in conv.subscribe(since=last_seq):
    queue.put(event.to_dict())    # {"type": "text_delta", "run_id": ..., "seq": ...}
```

That is all a JSON-lines CLI mode or an SSE endpoint needs; `since=seq` is the
reconnect protocol.

## What is deliberately not here

- **No server.** MoCode does not ship an HTTP or SSE transport. The event
  stream is the contract, and a transport is a thin adapter over it.
- **No conversation registry.** The runtime opens conversations and forgets
  them; an application keyed by its own ids is the only thing that knows what
  is live.
- **No callback per feature.** If you find yourself wanting the kernel to know
  about your feature, write a plugin or a hook — see [plugins.md](plugins.md)
  and [ARCHITECTURE.md](ARCHITECTURE.md).
