# Embedding MoCode

MoCode is a Python library before it is a CLI. `MoCode` is a runtime you embed: it
holds the config, the plugin loading and the session store, and it opens
**conversations**. A conversation is one project, one model, one history and one
event stream — start as many as you like, in as many projects, at the same time.
Nothing prints and nothing knows about a terminal, so an editor, a web backend, a
test harness and the built-in CLI are all the same kind of consumer.

```python
from mocode import MoCode
```

That import is lazy — it costs about a millisecond, and pulling in the
application layer is deferred until you actually construct one.

---

## Quick start

```python
import asyncio
from mocode import MoCode
from mocode.core import TextDelta, ToolCallStarted, ToolCallFinished, RunFinished


async def main():
    mc = MoCode()                        # ~/.mocode/config.json
    conv = mc.new_conversation()         # in the current directory

    async for event in conv.chat("what does tests/test_agent_loop.py cover?"):
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
`~/.mocode/config.json`; `Config.load()` and the path constant are exported from
`mocode.host` if you want to check first.

### The runtime

```python
MoCode(
    config: Config | None = None,          # None loads ~/.mocode/config.json
    home: Path | None = None,              # ~/.mocode — plugins, skills, sessions
    plugin_dirs: Sequence[Path] | None = None,   # default: <cwd>/.mocode/plugins, <home>/plugins
)

mc.config        # the Config everything shares
mc.home          # where plugins, skills and sessions live
mc.store         # SessionStore: list(workdir) / list_all() / find(session_id)
mc.plugins_for(cwd)          # the plugins a project loads (loaded once, cached)
mc.plugin_sources_for(cwd)   # the directories they were loaded from
mc.provider_for(key, model)  # build a provider for a pair
mc.set_default_model(key, model)   # the only thing that writes config.json
```

### Opening a conversation

```python
conv = mc.new_conversation(
    cwd: Path | str | None = None,     # None: the process working directory
    provider: str | None = None,       # None: the config's active provider
    model: str | None = None,          # None: the config's active model
    commands: CommandRegistry | None = None,
    session: Session | None = None,    # continue a stored one
)
```

Everything the conversation needs follows from `cwd` and the provider/model:
its plugins (loaded once per project, built once per conversation), its tools
(relative paths resolve into that project, `bash` starts there), its system
prompt, and the session it will be saved as. Two conversations in one process
share the config, the plugin loading and the session store, and nothing else.

`mc.resume(session_id)` is the one-line way back into a stored conversation: it
finds the session wherever its project was and opens it on the model it used.

---

## The event stream

A conversation has one stream, and every turn publishes into it. Events are plain
data: each has a `type` string, a `run_id`, a monotonic `seq`, and a `to_dict()`
that flattens it (and any nested `Usage` or argument dicts) into JSON-ready
values.

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
| `ConversationChanged` | — | the history was replaced; re-read the conversation |

Four rules make the stream safe to build on:

**Text arrives in fragments; concatenate them in `seq` order.** A `TextDelta` is
not a whole message. If you only want the final answer, read
`RunFinished.content` instead and ignore deltas entirely.

**A tool call is an object with an identity, not a return value.** All three tool
events carry the same `call_id`, which correlates with the tool message in
`conv.messages` (`tool_call_id`), so a UI can match events to history.

**A turn ends with exactly one of `RunFinished` or `RunFailed`** — including a
turn that was cancelled, which reports `cancelled=True`. `status` is one of
`ok` / `error` / `timeout` / `denied` / `not_found`.

**`seq` keeps counting across turns.** A reader that remembers a number can
always ask for what it missed.

### Watching, from anywhere

```python
reader = conv.subscribe()                 # live from now on
async for event in reader:
    ...
```

`conv.subscribe(since=seq)` replays everything the channel still remembers after
that number before continuing live — that is how a reconnecting client catches
up, and `Subscription.dropped` (plus the gaps you can see in `seq`) says honestly
when it could not. A reader never slows the run down: it has a bounded backlog
and loses its oldest events rather than holding up the model.

A turn is its own window on the same stream:

```python
turn = conv.run("summarize the diff")     # raises if a turn is already running
async for event in turn.subscribe():      # this turn's events, replay included
    ...
terminal = await turn.wait()              # RunFinished or RunFailed
turn.cancel()                             # stop it — from any coroutine
```

`conv.run()` belongs to the conversation: a client that disconnects does not kill
it, and a second client can join with `conv.subscribe()` or `turn.subscribe()`
while it runs. `conv.stream()` / `conv.chat()` are the scoped conveniences —
stop reading, or be cancelled, and the turn stops with you.

### Running one turn and waiting

```python
answer = await conv.chat("hello")          # drains the same stream
```

`chat()` is a convenience wrapper: it consumes the stream and returns
`RunFinished.content`. Provider failures raise, exactly as they would mid-stream.

---

## Getting the work state

The stream is push. For pull — "what is happening right now" — read
`conv.state`, a `RunState` that the loop keeps live by folding every event into it
as it is published:

```python
conv.state.status                 # "idle" | "running" | "done" | "failed" | "cancelled"
conv.state.iteration              # which LLM call
conv.state.content                # everything streamed this turn
conv.state.answer                 # the final answer ("" if the turn did not finish)
conv.state.usage                  # Usage, summed over the turn
conv.state.tool_calls_made        # how many tool calls started
conv.state.running_tool_calls     # [ToolCallState, ...] still executing
conv.state.failed_tool_calls      # those that did not end "ok"
conv.state.tool_calls["c1"]       # one call by id
```

Each `ToolCallState` has `name`, `args`, `status`, `result`, `details`,
`error_code`, `duration`, and `output` / `output_text` — everything the tool
streamed while it ran.

`result` is what went to the model. `details` is whatever structured data the
tool attached for *you* — it never entered the conversation. A `read` call
reports `{"lines": 412, "total_lines": 412}`, `bash` reports its exit code.

Because it is just a reducer over the events, you can hold your own:

```python
from mocode.core import RunState

mirror = RunState()
async for event in conv.subscribe():
    mirror.apply(event)             # identical to conv.state once the turn is done
```

`conv.state.to_dict()` gives the whole snapshot as plain data, which is the shape
to put behind an HTTP status endpoint.

Polling while a turn runs:

```python
async def watch(conv):
    while conv.state.status == "running":
        running = ", ".join(c.name for c in conv.state.running_tool_calls)
        print(f"\riteration {conv.state.iteration}: {running or 'thinking'}", end="")
        await asyncio.sleep(0.25)
```

---

## Many conversations at once

This is what the runtime exists for. Every conversation has its own project, its
own model, its own plugins (built for it, so its shell session and skill index
are its own), its own history and its own stream.

```python
mc = MoCode()

frontend = mc.new_conversation(cwd="/srv/web", provider="intern", model="Atlas")
backend  = mc.new_conversation(cwd="/srv/api", provider="intern", model="Atlas")

# both at the same time — different projects, different shells, one process
await asyncio.gather(
    frontend.chat("run the tests"),
    backend.chat("update the changelog"),
)
```

What a web backend does with that:

```python
live: dict[str, Conversation] = {}

@app.post("/conversations")
async def create(project: str, provider: str = "", model: str = ""):
    conv = mc.new_conversation(cwd=project, provider=provider or None, model=model or None)
    live[conv.id] = conv
    return {"id": conv.id}

@app.get("/conversations/{cid}/events")            # SSE
async def events(cid: str, since: int = 0):
    conv = live[cid]
    async for event in conv.subscribe(since=since or None):
        yield f"data: {json.dumps(event.to_dict())}\n\n"

@app.get("/conversations/{cid}/status")
async def status(cid: str):
    return live[cid].state.to_dict()

@app.post("/conversations/{cid}/stop")
async def stop(cid: str):
    live[cid].cancel()

@app.get("/sessions")
async def sessions():
    return [s.to_dict() for s in mc.store.list_all()]   # every project
```

The transport is yours; the host gives you an addressable run, a resumable
stream, a snapshot and a stop button. Keep the application's own dictionary
keyed by `conv.id` — the runtime deliberately keeps no registry of live
conversations, because the identity a conversation has in an application is that
application's business.

Two things to know: a conversation runs **one turn at a time** (a second `run()`
raises rather than interleaving two histories — queue it or cancel first), and
two conversations opened on the same session id would fight over one file, so key
your dictionary by id.

---

## Conversations and sessions

`conv.messages` is the live conversation in OpenAI message format — read it, or
hand it back elsewhere.

```python
conv.save(title="optional")     # persist to ~/.mocode/sessions/<hash>/<id>.json
await conv.new_session()        # save, then begin a fresh session in this project
await conv.new_session(messages)  # begin fresh, seeded from an export file
await conv.load_session(session)  # continue a stored session: history, id, and model
conv.rebuild_prompt()           # re-read AGENTS.md after a change on disk
conv.close(save=True)           # stop the turn, save, release plugins, end the stream

conv.list_sessions()            # [Session, ...] for this project, newest first
mc.store.delete(workdir, id)    # remove one; the store is the manager here
mc.store.list_all()             # every session in the store, across projects
mc.resume(session_id)           # open a stored session wherever it lives
```

Conversations are stored as messages, not as events: the message list is what
the provider needs, and it is the thing worth persisting. `Session` records where
it ran, which model it used, and when — which is what makes `resume()` restore
the model as well as the history, and what lets a UI group sessions by project.

---

## Swapping the model

```python
conv.set_model("intern", "Atria-Dawn-Preview")     # this conversation only
mc.set_default_model("intern", "Atria-Dawn-Preview")  # what new conversations start on
```

The two are deliberately separate. Switching a conversation's model touches no
other conversation and writes no file; the only thing that writes `config.json`
is `set_default_model`. Unknown providers raise `ValueError` in both.

---

## Dispatching commands

Commands are a host concept, not a terminal one. `conv.commands` holds whatever
plugins registered — the skills plugin contributes `/skill:<name>`, a third-party
plugin can add more, and the terminal's own `/help`, `/model`, `/resume` live in
*its* registry.

```python
from mocode.host import dispatch, Kind

result = await dispatch("/skill:release", conversation=conv, commands=conv.commands)
if result.kind is Kind.PROMPT:
    async for event in conv.chat(result.prompt):
        ...
```

`dispatch()` is the shared resolver: a line that names a command runs it, and
anything else comes back as `Kind.PROMPT` for you to send as a user message.
`Kind.EXIT` means "end this interaction", which is yours to interpret; a web
backend might close a socket, a script might stop looping.

A command handler gets the conversation (`ctx.conversation`), its arguments, and
the registry. It says things by publishing — `await ctx.conversation.notify("…")`
— so it works the same with a terminal, a browser or nothing at all watching.

Some commands need a screen: `/resume` picks from a list and `/copy` uses the
clipboard. Those live with the terminal and are not in your registry.

---

## Observing and intercepting

**Observation goes through the stream.** Subscribe, or implement `on_event` on a
hook if you want to be in-band:

```python
from mocode.core import AgentHook


class Audit(AgentHook):
    async def on_event(self, event):
        log.write(json.dumps(event.to_dict()) + "\n")
```

A hook's `on_event` is called inline — the loop waits for it — so use it for what
must not be missed (a commit log, a gate). A subscription never holds the loop
up; use it for anything that can fall behind.

Publish your own event with `await ctx.emit(...)`, from a hook, a tool, or
between runs through `ctx.conversation.notify()`. Give it a `Notice`, or subclass
`Event`. Pick a `type` string as the discriminator, give it a `summary()` so any
frontend can show it without knowing the type, and the `run_id` and `seq` are
stamped for you:

```python
from dataclasses import dataclass
from mocode.core import Event


@dataclass
class Compacted(Event):
    type = "compacted"
    before: int = 0
    after: int = 0

    def summary(self) -> str:
        return f"compacted {self.before} → {self.after}"
```

**Interception is separate, because it needs an answer.** Three hooks may change
what happens:

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

`ctx.deny` vetoes the call — the string becomes the tool result, and the stream
reports `ToolCallFinished(status="denied")`. `ctx.tool_args`, `ctx.tool_result`
and `ctx.tool_details` are writable, as are `ctx.messages` and
`ctx.system_prompt` in `before_iteration`. A `system_prompt` rewrite sticks for
the rest of the run. All of it is observable downstream, because a hook's
decision shows up in the events it caused.

Because a hook can await, a gate that needs a *human* answer is just a hook that
waits for one:

```python
class Approval(AgentHook):
    async def on_tool_start(self, ctx):
        if needs_approval(ctx.tool_name):
            ctx.deny = await ask_the_user(ctx.tool_name, ctx.tool_args)
```

A raising hook is logged and skipped; it never breaks the run.

---

## Writing a tool that reports progress

A tool keeps the plain `(args) -> str` shape unless it declares a second
parameter, in which case it receives its `ToolCallContext` and can publish
output as it works:

```python
from mocode.core import Tool, ToolOutput


async def slow_report(args, ctx):
    for i in range(args["steps"]):
        await asyncio.sleep(1)
        await ctx.emit(
            ToolOutput(call_id=ctx.tool_call_id, text=f"step {i}\n")
        )
    return "done"


tool = Tool(
    "slow_report",
    "Do something slowly and say so",
    {"steps": {"type": "integer", "description": "how many steps"}},
    slow_report,
)
```

Consumers get `ToolOutput` events live and can accumulate them from
`conv.state.tool_calls[call_id].output_text`. The built-in `bash` tool works
exactly this way — its stdout and stderr are published while the command is
still running. (The terminal shows only that a call is running and how it
ended, so it does not draw these; a frontend that wants to watch a command work
is free to.)

Tools that want to stream are async — a sync tool runs in a worker thread, where
it cannot await anything. Emit whole lines: a terminal renders each `ToolOutput`
line by line.

## Returning facts the model doesn't need

A tool's return value is a string the model reads. When there is also something
about the *result* worth exposing, return a `ToolResult`:

```python
from mocode.core import Tool, ToolResult


def lint(args):
    issues = run_linter(args["path"])
    return ToolResult(
        content=render(issues),                   # the model reads this
        details={"issues": len(issues)},          # you read this
    )


tool = Tool(
    "lint", "Lint a file",
    {"path": {"type": "string", "description": "File to lint"}},
    lint,
    summary_key="path",      # which argument appears in an activity line
    result_key="issues",     # which detail appears next to it
)
```

`details` reaches `ToolCallFinished.details` and `state.tool_calls[id].details`
and stops there — it is never sent to the model. `result_key` is a display hint:
the terminal renders `✓ lint  src/a.py · issues=3`, and a tool that declares no
`result_key` renders exactly as it did before. Any consumer is free to ignore
both and read whatever keys it wants.

---

## Building a bare agent

`MoCode` is the batteries-included path. If you want the kernel alone — your own
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

A sub-agent is `agent.derive(tools=agent.tool_registry.select(include_tags={"read"}), system_prompt=...)`
— an independent agent with a narrower tool set sharing the same provider. Pass
`channel=agent.channel` to have it publish into the parent's stream.

---

## Serialising events

Events are already plain data, so crossing a process boundary is a loop:

```python
async for event in conv.subscribe(since=last_seq):
    queue.put(event.to_dict())          # {"type": "text_delta", "run_id": ..., "seq": ...}
```

That is all a JSON-lines CLI mode or an SSE endpoint needs; neither requires a
change in the kernel. `RunState.to_dict()` gives the same treatment to a status
snapshot, and `since=seq` is the reconnect protocol.

---

## What is deliberately not here

- **No server.** MoCode does not ship an HTTP or SSE transport. The event stream
  is the contract, and a transport is a thin adapter over it — the channel
  already provides fan-out, replay and a snapshot to hand it.
- **No conversation registry.** The runtime opens conversations and forgets them;
  an application keyed by its own ids is the only thing that knows what is live.
- **No callback per feature.** If you find yourself wanting the kernel to know
  about your feature, write a plugin or a hook — see [plugins.md](plugins.md) and
  [ARCHITECTURE.md](ARCHITECTURE.md).
