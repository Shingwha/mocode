# Testing your plugin

> `mocode.testing` — the scripted model and the reading helpers, so a plugin
> is tested against a run that says exactly what the test wants it to say.

A plugin test does not need a network, an API key, or luck. It needs the
model to do something specific — call the tool, answer in text — and it needs
to read back what the run did. Both halves are in `mocode.testing`, and the
repository's own tests are written with the same tools.

## The scripted model

`MockProvider` replays a list of `Response` objects as chunk streams, in
order, and records every request it receives:

```python
from mocode.testing import MockProvider, call_tool, say

provider = MockProvider([call_tool("greet", {"who": "world"}), say("greeted")])
conversation.agent.provider = provider        # the swap every test makes
```

It is a real provider as far as the loop knows, and it is deliberately
awkward in the ways a real API is awkward:

* **tool-call arguments arrive in fragments** — the JSON argument string is
  split into 8-character chunks (`ARG_FRAGMENT`), so the stream accumulator
  gets exercised instead of bypassed;
* **`chunk_size=N`** splits text and reasoning the same way, when a test
  wants many small deltas;
* **every request is recorded** in `provider.calls` — each entry holds
  `messages`, `system`, `tools` and `max_tokens` as they actually went out,
  which is how a test asserts what the model *read*: the prompt, the frozen
  tool schemas, the tool answers.

`SlowProvider` is a `MockProvider` whose turn never finishes on its own — for
cancellation, timeout and concurrent-turn tests.

## Scripts, and the one rule about endings

A script is a list of responses. Two constructors cover the common entries:

* `say(text)` — a plain-text answer; this is how a turn ends;
* `call_tool(name, args)` — asks for one tool call with `args` serialised
  from a dict (`tool_call_response` is the same thing for an argument string
  you already have, and takes a `call_id`).

**The last response repeats forever.** That is the feature: a test that does
not care how many iterations a run takes does not have to enumerate them.
It is also the trap — a script that *ends* on a tool call never finishes the
turn: the model keeps calling tools, the loop keeps answering, until
`max_iterations` cuts it. End every script with a `say(...)`, and let the
repeat rule absorb the iterations you do not care about:

```python
# asks for the tool once, then answers — the turn ends
script = [call_tool("greet", {"who": "world"}), say("greeted")]

# the turn never ends on its own — max_iterations will be the stop reason
script = [call_tool("greet", {"who": "world"})]
```

## Holding a background job open

A test that needs a background job to *stay alive* until something else
releases it — a job's output, its completion, its kill — should start it as a
bounded `sleep N` child (`"echo up; sleep 0.3"`), which is one fork at spawn and
ends on its own. Do not reach for a polling loop (`while true; do …; done`) or
an external gate file: on Windows, killing a bash whose command keeps forking
strands an MSYS fork-child, because `TerminateProcess` reaches only the direct
child. A `sleep` child has nothing to strand.

Keep the child short — 0.15–0.3s is a comfortable margin over a bash spawn on
Windows, and the whole suite is timed. What *waits* for the job then waits on
the job's own signal, never on a clock: `job.done.wait()`, the output sink, or
a `wait_until(predicate, bound=…)` poll. To prove an announcement did **not**
happen, assert the observable event set inside the turn (the turn end with no
`PluginMessage` in it) instead of out-waiting a window — the turn boundary is
the fact, and waiting longer only proves the window.

## Time is discipline, not luck

A test that sleeps to synchronize is a test that passes on the machine it was
written on. The suite therefore treats *how a test waits* as part of its
contract, and `tests/conftest.py` enforces it: an autouse guard patches
`time.sleep` / `asyncio.sleep`, and a bare sleep whose call site lives under
`tests/` **fails the test** with a message pointing at the sanctioned API. A
sleep the *product* performs (a shell poller's window, an MCP retry backoff) is
measured behaviour, not test fragility — the guard records those, product-side,
without failing them.

The four sanctioned waits, in the order you should reach for them:

* **Event gating** — an `asyncio.Event` (or a `threading.Event` a tool shares
  with its test) that only the thing you are waiting for sets. The strongest
  form: the test cannot proceed until the run says so, and a missed condition
  is a hang, which a bound turns into a failure. Use for concurrency,
  cancellation and every "the tool started" fact.
* **`wait_until(predicate, bound=…)`** — condition polling for state you cannot
  be signalled about (a registry entry, a job's status, a peer's pidfile). It
  polls on a sanctioned short sleep, so it never appears in the guard's ledger,
  and its timeout raises an `AssertionError` naming what never came true.
* **`settle(seconds)`** — the sanctioned sleep, for the two cases where the
  wait *is* the behaviour under test: a real subprocess observation window (a
  fake server that has to notice a kill) and a watchdog deadline that has to
  actually expire. It is not a synchronization primitive; if a condition can be
  polled, poll it.
* **`real_time()`** — the escape hatch, for a wall-clock block the other three
  cannot express. Rare by construction; if you reach for it, the wait probably
  wants a different shape.

Time itself can be faked with `FakeClock` + `advance(clock, dt)`: swap a
module's `time` for the clock (`monkeypatch.setattr(module, "time", clock)`),
and every `time.monotonic()` the product reads is yours to drive. Retry
backoff, deadlines and budget tests prove their rules in fake time — a failed
attempt *costs* an `advance`, never a real second. What that leaves unprovable
is a clock the product reads through a seam you cannot reach; those tests keep
the patch and say so in a docstring.

Two wall-clock facts this suite deliberately keeps instead of hiding: a
refused loopback TCP connect on Windows (and some corporate filter drivers)
costs a flat ~2s in the OS, before the test runs at all; and a product
coalescing window (`_NOTIFY_WINDOW`) is the real 0.3s it is. Both are measured,
not slept, and both are in the slow-tests ledger rather than shaved into
fragility.

## Reading the turn back

Three helpers read what the run emitted:

* `collect(agen)` — drains an async iterator (a `conversation.stream(...)`,
  or a `turn.subscribe()`) into a list;
* `terminal(events)` — the one event that ended the turn, `RunFinished` or
  `RunFailed`. Every turn emits exactly one, so zero is a stream drained too
  early (or through a filter that skipped it) and more than one is events
  spanning several turns — both fail the assertion;
* `events_of_type(events, cls)` — the events of one class, in stream order.

```python
from mocode.testing import collect, events_of_type, terminal
from mocode.core.events import ToolCallFinished

events = await collect(conversation.stream("greet the world"))
[finished] = events_of_type(events, ToolCallFinished)
assert finished.name == "greet"
assert finished.status == "ok"
assert terminal(events).content == "greeted"
```

*A resume reads back the same way.* `load_session` publishes
`ConversationChanged` first — readers clear and repour the document — and
then republishes the session's stored plugin messages in order. Subscribe
before the resume, drain afterwards, and expect the redraw announcement
ahead of the replayed `PluginMessage`s:

```python
from mocode.core.events import PluginMessage
from mocode.host.events import ConversationChanged

sub = fresh.subscribe()
await fresh.load_session(stored)
events = []
while (event := sub.take()) is not None:
    events.append(event)
assert isinstance(events[0], ConversationChanged)
assert [e.kind for e in events_of_type(events, PluginMessage)] == expected
```

## A complete test

The conventions are the repository's own (`tests/test_plugins.py`): write
the plugin into `tmp_path`, point a `MoCode` at it, swap the conversation's
provider for the script, and read the turn back. Nothing touches the real
`~/.mocode` — the runtime's home lives inside the test too.

```python
import json
from pathlib import Path

from mocode.core.events import ToolCallFinished
from mocode.host.config import Config, ModelEntry, ProviderEntry
from mocode.host.runtime import MoCode
from mocode.testing import MockProvider, call_tool, collect, events_of_type, say, terminal

PLUGIN_CODE = '''
from mocode.plugins import Plugin, Tool

class Greeter(Plugin):
    name = "greeter"
    description = "one tool that greets"

    def build(self, ctx):
        ctx.tools.register(Tool(
            name="greet",
            description="greet someone by name",
            schema={"type": "object", "properties": {"who": {"type": "string"}}},
            func=lambda args: f"hello {args['who']}",
        ))
'''


def _write_plugin(plugins_dir: Path, name: str, code: str) -> None:
    root = plugins_dir / name
    (root / "mocode").mkdir(parents=True, exist_ok=True)
    (root / "plugin.json").write_text(
        json.dumps({"$schema": "https://agent-plugins.org/schemas/v1.json", "name": name}),
        encoding="utf-8",
    )
    (root / "mocode" / "plugin.py").write_text(code, encoding="utf-8")


def _config() -> Config:
    """Well-shaped and never real — the provider is replaced right after."""
    return Config(
        provider="test",
        model="test-model",
        providers={
            "test": ProviderEntry(
                name="Test",
                api_key="sk-test",
                base_url="http://localhost",
                models=[ModelEntry(id="test-model")],
            )
        },
    )


async def test_the_model_can_call_the_plugin_tool(tmp_path: Path):
    plugins_dir = tmp_path / "plugins"
    _write_plugin(plugins_dir, "greeter", PLUGIN_CODE)

    mc = MoCode(config=_config(), home=tmp_path / "home", plugin_dirs=[plugins_dir])
    conversation = mc.new_conversation(cwd=tmp_path)
    provider = MockProvider([call_tool("greet", {"who": "world"}), say("greeted")])
    conversation.agent.provider = provider

    events = await collect(conversation.stream("greet the world"))

    [finished] = events_of_type(events, ToolCallFinished)
    assert finished.name == "greet"
    assert finished.status == "ok"
    assert terminal(events).content == "greeted"

    # And what the model read on its last request — the tool's answer:
    [tool_message] = [m for m in provider.calls[-1]["messages"] if m.get("role") == "tool"]
    assert "hello world" in tool_message["content"]
```

Async tests need no decorator (`asyncio_mode = "auto"`); commands publish
rather than print, so a command test subscribes and drains instead of
capturing stdout. The repository's own fixtures (`tests/conftest.py`) wrap the
three moves above — `wired` hands back a conversation already on a scripted
model, `write_plugin` lays a plugin directory on disk, `plugin_host` builds a
host with no runtime around it.

## Testing a `with_context` tool directly

A tool declared with `with_context=True` receives a `ToolCallContext` as its
second parameter. For a unit test of the tool itself you do not need the
loop: the context is a plain dataclass whose fields all default, and whose
`emit` defaults to a no-op — construct one and call `run`:

```python
from mocode.plugins import Tool, ToolCallContext

def _greet(args, ctx):
    if ctx.cancelled:                       # rehearse a cooperative cancel
        return "stopped early"
    return f"hello {args['who']}"

tool = Tool(
    name="greet",
    description="greet someone by name",
    schema={"type": "object", "properties": {"who": {"type": "string"}}},
    func=_greet,
    with_context=True,
)
ctx = ToolCallContext(tool_name="greet", tool_call_id="call_1")
assert tool.run({"who": "world"}, ctx) == "hello world"

ctx.cancel_event.set()                      # the tool notices and bows out
assert tool.run({"who": "world"}, ctx) == "stopped early"
```

`ctx.cancel_event` is a real `threading.Event`, and `ctx.emit` is a sink the
default of which does nothing — an async tool publishes progress with
`await ctx.emit(Notice(message="halfway there"))` and stays testable outside
a running loop. What you cannot rehearse this way is the interception protocol
— hooks rewriting `tool_args`, denials, result truncation — that is the
dispatcher's behavior, and it is tested through the scripted model above.

## Where to look for more

`tests/test_plugins.py` (layout, loading, assembly), `tests/test_conversations.py`
(runtime-level turns) and `tests/test_testing.py` (these helpers' own
contract) are larger examples of the same two moves: script the model,
read the turn back.

The suite's own timing discipline, its guard, and the slimming/consolidation
work that produced the current shape are recorded in the spec group
`specs/2026-10-03-test-suite-slimming/`: `00-overview.md` (the waves, the
mandate floors and the global invariants), `01-w0-time-control.md` (the four
waiting APIs), and `ref/orphans.md` / `ref/deletions.md` (what the gates found
and why each removal was safe). The full suite runs in ~15s on an idle machine
and every test is bounded by a 30s pytest timeout.
