# Embedding MoCode

MoCode is a Python library before it is a CLI. `MoCode` gives you a configured
agent and one thing to consume: a stream of events. It prints nothing and knows
nothing about a terminal, so an editor, a web backend, a test harness and the
built-in CLI are all the same kind of consumer.

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
    mc = MoCode()                       # ~/.mocode/config.json, current directory

    async for event in mc.chat("what does tests/test_agent_loop.py cover?"):
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
`~/.mocode/config.json`; `Config.load()` and the path constant are exported
from `mocode.host` if you want to check first.

### Constructor

```python
MoCode(
    config: Config | None = None,     # None loads ~/.mocode/config.json
    home: Path | None = None,         # ~/.mocode — plugins, skills, sessions
    cwd: Path | None = None,          # Path.cwd() — project plugins, AGENTS.md
    display: Frontend | None = None,  # something to show the run, or None
    commands: CommandRegistry | None = None,
    plugin_dirs: list[Path] | None = None,
    extra_plugins: list[Plugin] | None = None,
)
```

`display` is the only thing that makes a run look different. Everything else —
which plugins load, which tools are registered, where sessions go — is
identical either way. A `Frontend` is anything with `info` / `warn` / `error` /
`conversation_changed`; the built-in `Display` is one, and so is a class you
write yourself.

`MoCode` lives in `mocode.host.runtime`; `from mocode import MoCode` is a lazy
alias for it. Everything else in this document is exported from `mocode.host`
and `mocode.core`.

---

## The event stream

Every turn emits one ordered stream. Events are plain data: each has a `type`
string, a `run_id`, a monotonic `seq`, and a `to_dict()` that flattens it (and
any nested `Usage` or argument dicts) into JSON-ready values.

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
| `RunFinished` | `content`, `usage`, `iterations`, `tool_calls_made`, `had_error` | the turn ended |
| `RunFailed` | `error`, `kind` | the turn ended on an unhandled error |
| `Notice` | `message`, `level` | a plugin wants to say something |

Three rules make the stream safe to build on:

**Text arrives in fragments; concatenate them in `seq` order.** A `TextDelta`
is not a whole message. If you only want the final answer, read
`RunFinished.content` instead and ignore deltas entirely.

**A tool call is an object with an identity, not a return value.** All three
tool events carry the same `call_id`, which correlates with the tool message in
`mc.messages` (`tool_call_id`), so a UI can match events to history.

**A turn ends with exactly one of `RunFinished` or `RunFailed`** — except a
turn that was cancelled outright, which emits neither because nothing is left
to read it. `status` is one of `ok` / `error` / `timeout` / `denied` /
`not_found`.

### Running one turn without a loop

```python
answer = await mc.agent.chat("hello")          # ⟵ drains the same stream
```

`chat()` is a convenience wrapper: it consumes the stream and returns
`RunFinished.content`. Provider failures raise, exactly as they would mid-stream.

---

## Getting the work state

The stream is push. For pull — "what is happening right now" — read
`mc.state`, a `RunState` that the loop keeps live by folding every event into it
as it is published:

```python
mc.state.status                 # "idle" | "running" | "done" | "failed" | "cancelled"
mc.state.iteration              # which LLM call
mc.state.content                # everything streamed this turn
mc.state.answer                 # the final answer ("" if the turn did not finish)
mc.state.usage                  # Usage, summed over the turn
mc.state.tool_calls_made        # how many tool calls started
mc.state.running_tool_calls     # [ToolCallState, ...] still executing
mc.state.failed_tool_calls      # those that did not end "ok"
mc.state.tool_calls["c1"]       # one call by id
```

Each `ToolCallState` has `name`, `args`, `status`, `result`, `details`,
`error_code`, `duration`, and `output` / `output_text` — everything the tool
streamed while it ran.

`result` is what went to the model. `details` is whatever structured data the
tool attached for *you* — it never entered the conversation. A `read` call
reports `{"lines": 412, "total_lines": 412}`, `bash` reports its exit code:

Because it is just a reducer over the events, you can hold your own:

```python
from mocode.core import RunState

mirror = RunState()
async for event in mc.chat("..."):
    mirror.apply(event)         # identical to mc.state once the turn is done
```

`mc.state.to_dict()` gives the whole snapshot as plain data, which is the shape
to put behind an HTTP status endpoint.

Polling while a turn runs:

```python
async def watch(mc):
    while mc.state.status == "running":
        running = ", ".join(c.name for c in mc.state.running_tool_calls)
        print(f"\riteration {mc.state.iteration}: {running or 'thinking'}", end="")
        await asyncio.sleep(0.25)
```

---

## Cancelling

Cancel the task that is consuming the stream. Pending work is torn down and the
conversation is left replayable — every assistant tool call still has an answer,
so the history can be sent back to the model on the next turn.

```python
task = asyncio.create_task(consume(mc.chat("...")))
...
task.cancel()
```

The turn's state ends as `cancelled`, and whatever had streamed so far is still
in `mc.state.content`.

---

## Conversations and sessions

`mc.messages` is the live conversation in OpenAI message format — read it, or
hand it back elsewhere.

```python
mc.save_session(title="optional")     # persist to ~/.mocode/sessions/<hash>/<id>.json
mc.start_session()                    # save, then begin a fresh one
mc.start_session(messages)            # begin fresh, seeded from elsewhere
mc.use_session(session)               # continue an existing one, same identity
mc.replace_messages(messages)         # swap the conversation in place
mc.rebuild_prompt()                   # re-read AGENTS.md after a change on disk

mc.sessions.list()                    # [Session, ...] for this working directory
mc.sessions.get_active()
```

Conversations are stored as messages, not as events: the message list is what
the provider needs, and it is the thing worth persisting. Replay a saved
conversation into a UI with `Display.conversation_changed(messages, mc.tools)` if
you want the terminal's rendering, or just read `mc.messages` yourself.

---

## Swapping the model

```python
mc.switch_provider("intern", "Atria-Dawn-Preview")
```

That writes the choice back to config, rebuilds the provider and updates
`mc.model` (the `ModelSpec` plugins read). Failures raise `ValueError`.

---

## Dispatching commands

Commands are a host concept, not a terminal one. `mc.commands` holds whatever
plugins registered — the skills plugin contributes `/skill:<name>`, a plugin of
yours can add more, and the terminal's own `/help`, `/model`, `/resume` are just
its plugin's contribution.

The runtime itself contributes none, so a headless app starts with an empty
registry and dispatches whatever is there:

```python
from mocode.host import CommandContext, Kind

cmd = mc.commands.get("/skill:release")
if cmd is not None:
    result = await cmd.handler(CommandContext(app=mc, args="", frontend=None))
    if result.kind is Kind.PROMPT:
        async for event in mc.chat(result.prompt):
            ...
```

`Kind.PROMPT` means "send this text to the agent instead" — that is how a skill
or a template command works. `Kind.EXIT` means "end this interaction", which is
yours to interpret; a web backend might close a socket, a script might stop
looping. `Kind.CONTINUE` means the command did whatever it was going to do.

Some commands need a screen — `/resume` picks from a list, `/export` says where
it wrote the file. Those live with the terminal and are not in your registry.
Anything a plugin contributes works headless, which is the point of keeping the
contract in the host.

## Observing and intercepting

Attach an `AgentHook` to `mc.ctx.hooks` before the run, or pass hooks when you
build your own agent with the `Agent` builder.

**Observation goes through the stream.** A hook that only wants to watch
implements `on_event` and sees everything, including events other plugins
publish:

```python
from mocode.core import AgentHook


class Audit(AgentHook):
    async def on_event(self, event):
        log.write(json.dumps(event.to_dict()) + "\n")
```

Publish your own event with `await ctx.emit(...)`. Give it a `Notice`, or
subclass `Event`. Pick a `type` string as the discriminator, give it a
`summary()` so any frontend can show it without knowing the type, and the
`run_id` and `seq` are stamped for you:

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
the rest of the run.
All of it is observable downstream, because a hook's decision shows up in the
events it caused.

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
`mc.state.tool_calls[call_id].output_text`. The built-in `bash` tool works
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

The built-in `bash` tool works this way: its stdout and stderr arrive as
`ToolOutput` while the command is still running.

---

## Building a bare agent

`MoCode` is the batteries-included path. If you want the kernel alone — your own
provider, your own tools, no config file — use the builder:

```python
from mocode.core import Agent, AgentConfig, ModelSpec

agent = (
    Agent()
    .provider(my_provider)
    .prompt("You are a helpful assistant.")
    .tools([tool_a, tool_b])
    .hooks([my_hook])
    .config(AgentConfig(tool_timeout=60, max_iterations=20))
    .model(ModelSpec(name="my-model", context_window=128_000))
    .build()
)

async for event in agent.stream("hello"):
    ...
```

A sub-agent is `agent.derive(tools=agent.tool_registry.select(include_tags={"read"}), system_prompt=...)`
— an independent agent with a narrower tool set sharing the same provider.

---

## Serialising events

Events are already plain data, so crossing a process boundary is a loop:

```python
async for event in mc.chat(prompt):
    queue.put(event.to_dict())          # {"type": "text_delta", "run_id": ..., ...}
```

That is all a JSON-lines CLI mode or an SSE endpoint needs; neither requires a
change in the kernel. `RunState.to_dict()` gives the same treatment to a status
snapshot.

---

## What is deliberately not here

- **No server.** MoCode does not ship an HTTP or SSE transport. The event stream
  is the contract, and a transport is a thin adapter over it.
- **No callback per feature.** If you find yourself wanting the kernel to know
  about your feature, write a plugin or a hook — see
  [plugins.md](plugins.md) and [ARCHITECTURE.md](ARCHITECTURE.md).
