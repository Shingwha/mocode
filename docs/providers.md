# Providers

> The Provider Protocol (`core/provider.py`), the built-in `OpenAIProvider`,
> and how to add another one.

The LLM consumer (`AgentLoop`) only ever touches the DTOs in
`core/provider.py`; it never imports an SDK type. A provider is therefore one
small class: translate between your backend and the kernel's dialect.

## The dialect comes first

The kernel's interchange format is the **OpenAI wire format**, and it is
declared, not abstracted away:

- `messages` are OpenAI role dicts — `user`, `assistant` (with optional
  `tool_calls`, `reasoning_content`), `tool` (with `tool_call_id`);
- `tools` are OpenAI function schemas (`{"type": "function", "function": …}`);
- `system` travels as a separate string, not a message;
- the output cap is called `max_tokens`.

One dialect means the kernel has exactly one message shape to keep correct. A
backend that speaks something else — Anthropic, a local server — translates
inside its provider, at the edge.

## The DTOs

All `@dataclass` types in `core/provider.py`; these are the only types that
cross the boundary.

**`Chunk`** — one streaming delta; every field optional, a chunk carries what
arrived:

| Field | Type | Accumulates |
|---|---|---|
| `text` | `str` | by concatenation |
| `reasoning` | `str` | by concatenation |
| `tool_calls` | `list[ToolCallDelta]` | by `index` — a list, because one delta may carry several parallel fragments |
| `usage` | `Usage \| None` | last one wins |
| `finish_reason` | `str \| None` | last one wins |

**`ToolCallDelta`** — one fragment of a tool call: `index`, `id`, `name`,
`arguments`. Streaming APIs split a call across chunks: the first fragment
carries `index`/`id`/`name` (assign-once), later ones append JSON text to
`arguments`. `index` identifies which call a fragment belongs to.

**`ToolCall`** — a complete call: `id`, `name`, `arguments` (a raw JSON
string, never a parsed dict — the loop parses at the tool layer).

**`Usage`** — `prompt_tokens`, `completion_tokens`. Streaming APIs report it
only if the request asks, in a final chunk with no choices at all.

**`Response`** — what a chunk stream adds up to. Not a provider return value:
`StreamAccumulator` produces it, and `IterationFinished` carries it. A
provider never assembles one; a streaming consumer wants the raw deltas.

## The Protocol

```python
@runtime_checkable
class Provider(Protocol):
    @property
    def model(self) -> str: ...

    def is_retriable(self, exc: Exception) -> bool: ...
    def stream(
        self,
        messages: list[dict[str, Any]],   # OpenAI-format history
        system: str,                      # the complete system prompt
        tools: list[dict[str, Any]],      # function schemas; [] = no tools
        max_tokens: int | None,           # output cap; None = send no cap
    ) -> AsyncIterator[Chunk]: ...
```

All three members are required; the protocol is structural, not inherited. A
provider that cannot stream natively yields a single chunk holding the whole
response — the loop does not care which it is.

**The provider must:**

- place `system` where the backend wants it (prepended message or parameter);
- map each backend chunk onto a `Chunk`, dropping chunks that carry nothing;
- preserve `index` on tool-call fragments, keeping `arguments` as raw JSON text;
- make the request inside the generator, on first iteration — that is what
  puts connection failures and rate limits inside the retry window.

**The provider must not:**

- modify the incoming message list (it is shared);
- translate SDK exceptions — let them propagate, `with_retry_stream` catches;
- validate tool schemas (the caller guarantees them);
- accumulate chunks into a `Response` — that is `StreamAccumulator`'s job.

`is_retriable` decides what is worth retrying: `True` for transient errors
(rate limits, timeouts, connection failures), `False` for permanent ones
(bad key, malformed request).

## Retry

`with_retry_stream(provider, *args, max_retries=6)` calls
`provider.stream(*args)` and retries until the first chunk:

```
attempt → create stream → pull first chunk ─┬─ fails?  retry (inside the window)
                                            └─ succeeds → yield it, then pass
                                               everything through (outside the window)
```

A stream cannot be replayed: once a chunk has reached the caller, retrying
would duplicate output. The window is exactly right in practice — connection
failures, rate limits and auth errors all surface when the request is made.
A failure mid-stream propagates; the caller handles it, because it already
received partial output. `CancelledError` is never retried.

Backoff: `min(1.0 · 2^attempt + U(0, 0.5)s, 60s)` — 6 retries, so ~1s, 2s, 4s,
8s, 16s, 32s. Retries are logged at WARNING.

## The built-in `OpenAIProvider`

`providers/openai.py` — OpenAI itself and anything OpenAI-compatible via
`base_url`.

```python
OpenAIProvider(api_key, model="gpt-4o", base_url=None, extra_body=None)
```

What `stream()` does, in order:

1. **Normalize** — `_normalize_messages()` drops assistant `tool_calls` that
   have no matching `tool` answer. A reloaded or hand-edited history can
   contain orphans; endpoints reject such a list. Calls whose id has no tool
   message are dropped, and an assistant left with no calls loses the field.
2. **Prepend system** — `{"role": "system", "content": system}` first.
3. **Request** — `await client.chat.completions.create(..., stream=True)`.
   The `await` performs the request, so a dead connection surfaces inside the
   retry window. An empty tools list becomes `None` (the API rejects `[]`);
   `max_tokens` is sent only when set — MoCode never invents a cap.
4. **Map** — `_to_chunk()` tolerates what compatible endpoints disagree about:
   `delta.content` → `text`; `delta.reasoning_content` (or a *string*
   `delta.reasoning` — the newest models use it for a structured object,
   which is ignored) → `reasoning`; every `delta.tool_calls[i]` → one
   `ToolCallDelta` (`index` defaults to 0 when omitted); `finish_reason` and
   the final usage-only chunk pass through. Empty chunks are dropped.

**Token accounting:** streaming responses report usage only when asked, so
`stream_options: {"include_usage": True}` is sent by default. An endpoint that
rejects the parameter is configured around by putting its own
`stream_options` in the model's `extra_body` — that value wins and is lifted
out so it is not sent twice.

**Retriable:** `RateLimitError`, `InternalServerError`, `APIConnectionError`,
`APITimeoutError`. Everything else (auth, bad request) is permanent.

**Laziness:** the `openai` SDK is imported on first use — the client in
`_ensure_client()`, the exception tuple in `is_retriable` — so importing
MoCode never imports `openai`.

## Configuration

`MoCode.provider_for(key, model)` builds a provider from the config entry.
Which implementation it builds is the entry's `type` field:

```jsonc
{
  "providers": {
    "intern":  { "base_url": "…", "models": { … } },   // no type → "openai"
    "local":   { "type": "llama", "models": { … } }
  }
}
```

Types live in a registry on the runtime: `"openai"` ships built in, anything
else arrives from outside — a plugin's `build()`, an embedding application, a
script:

```python
mc.register_provider_type("llama", lambda entry, key, model: LlamaProvider(
    base_url=entry.base_url, model=model,
))
```

A plugin registers through the context instead. `build()` runs before the
provider is resolved, so the type is available to the very conversation that
shipped it:

```python
class LlamaPlugin(Plugin):
    name = "llama"

    def build(self, ctx):
        ctx.register_provider_type("llama", lambda entry, key, model: LlamaProvider(
            base_url=entry.base_url, model=model,
        ))
```

A factory receives `(entry, key, model)` and reads what it needs —
`entry.api_key_for(key)` resolves `api_key` → `$<PROVIDER_KEY>_API_KEY`,
`entry.base_url`, the model entry's `extra_body`. Registering a name again
replaces it. A config entry naming an unregistered type is a configuration
mistake: `provider_for()` raises instead of falling back.

`extra_body` is a per-model dict of additional request fields
(`temperature`, `reasoning_effort`, …) merged into the call — provider
extensions without provider code.

## Adding a provider

Three steps, roughly a hundred lines.

**1. The module** — `mocode/providers/anthropic.py`, say. The shape, elided:

```python
class AnthropicProvider:
    def __init__(self, api_key: str, model: str, base_url: str | None = None):
        self._client = None                    # lazy — never import the SDK at module level

    @property
    def model(self) -> str: ...

    def is_retriable(self, exc: Exception) -> bool:
        from anthropic import APIConnectionError, APITimeoutError, RateLimitError
        return isinstance(exc, (RateLimitError, APIConnectionError, APITimeoutError))

    async def stream(self, messages, system, tools, max_tokens):
        # convert messages to your SDK's format, open the stream, map events
        # onto Chunk. Anthropic *requires* an output cap where OpenAI makes it
        # optional — a backend like that makes models.<m>.max_output a config
        # requirement; the provider still never invents a number.
        ...
        # text:      yield Chunk(text=…)
        # tool args: yield Chunk(tool_calls=[ToolCallDelta(index=…, arguments=…)])
        # usage:     yield Chunk(usage=Usage(…)) on the start/delta events
```

Note what the provider does not do: it never assembles a full response, and it
never decides what a tool call *means*.

**2. A lazy export** — so importing MoCode does not import the SDK:

```python
def __getattr__(name: str):
    if name == "AnthropicProvider":
        from .anthropic import AnthropicProvider
        globals()["AnthropicProvider"] = AnthropicProvider
        return AnthropicProvider
    raise AttributeError(name)
```

**3. Register the type** — as shown above, `mc.register_provider_type(...)`.

If your backend is not OpenAI-shaped, the conversion is inside `stream()`:

| | Kernel (OpenAI dialect) | e.g. Anthropic |
|---|---|---|
| System prompt | separate `system` string | also a separate parameter |
| Tool calls | `message.tool_calls[].function.arguments` (JSON string) | may be a JSON object, keyed by block |
| Tool results | `{"role": "tool", "tool_call_id": "…"}` | `tool_result` content blocks |

### Checklist

- [ ] `model` property, `is_retriable`, `stream` — all three, structural
- [ ] `stream` is an async generator; the request happens on first iteration
- [ ] yields `Chunk`s and never accumulates them into a `Response`
- [ ] tool-call fragments split by `index`, `arguments` left as raw JSON text
- [ ] SDK imports lazy (never at module level)
- [ ] `is_retriable`: transient yes, permanent no
- [ ] registered with `register_provider_type` and named by the config's `type`
