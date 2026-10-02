# Examples: the kernel alone

Agents built from `mocode.core` with no host, no plugins and no config file.
The two providers here are stubs you swap for an HTTP client; nothing else in
either file changes.

| file | what it shows |
|---|---|
| `minimal.py` | the whole engine in one file: a provider, one `Tool` declared as a JSON Schema object node, one `AgentHook`, five constructor arguments — and a turn watched through `Turn.subscribe()` |
| `nested.py` | `AgentLoop.derive()`: a sub-agent that inherits *copies* of the parent's tools, runs on a cheaper provider and publishes into the parent's channel — while each agent's `Turn` and `state` stay scoped to its own run |

## Run them

```bash
uv run python examples/core/minimal.py
uv run python examples/core/nested.py
```

Needs no API key: the model is scripted, so the output is deterministic. For
the same two shapes inside a full runtime — conversation, plugins, sessions —
see [../plugins/](../plugins) and [../../docs/embedding.md](../../docs/embedding.md).
