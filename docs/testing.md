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

import pytest

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
        active_provider="test",
        active_model="test-model",
        providers={
            "test": ProviderEntry(
                name="Test",
                api_key="sk-test",
                base_url="http://localhost",
                models={"test-model": ModelEntry()},
            )
        },
    )


@pytest.mark.asyncio
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

Async tests need `@pytest.mark.asyncio`; commands publish rather than print,
so a command test subscribes and drains instead of capturing stdout.

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
a running loop.

`ctx.cancel_event` is a real `threading.Event` (set it to rehearse a
cooperative cancel), and `ctx.emit(...)` is safe to call because the default
does nothing. What you cannot rehearse this way is the interception protocol
— hooks rewriting `tool_args`, denials, result truncation — that is the
dispatcher's behavior, and it is tested through the scripted model above.

## Where to look for more

`tests/test_plugins.py` (layout, loading, assembly), `tests/test_conversations.py`
(runtime-level turns) and `tests/test_testing.py` (these helpers' own
contract) are larger examples of the same two moves: script the model,
read the turn back.
