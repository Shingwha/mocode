# MoCode Providers Layer — Developer Reference

> Covers the Provider Protocol (`core/provider.py`), the built-in `OpenAIProvider` (`providers/openai.py`), and how to implement custom providers.

---

## Table of Contents

1. [Overview](#overview)
2. [DTOs — Chunk, Response, ToolCall, Usage](#dtos)
3. [Provider Protocol](#provider-protocol)
4. [Retry](#retry)
5. [OpenAIProvider](#openaiprovider)
6. [Configuration Integration](#configuration-integration)
7. [Adding a New Provider](#adding-a-new-provider)
8. [Design Notes](#design-notes)

---

## Overview

MoCode abstracts LLM communication through a **Protocol-based provider system**. The LLM consumer (primarily `AgentLoop`) only ever touches the DTOs in `core/provider.py` and never imports any SDK-specific types. This clean separation means:

- Adding a new LLM backend requires implementing a single Protocol.
- The rest of the framework (the agent loop, tools, hooks, prompt assembly) is LLM-agnostic.
- Multiple providers can coexist — `Config` holds a registry of `ProviderEntry` objects.

The providers layer consists of two files:

| File | Role |
|------|------|
| `mocode/core/provider.py` | `Provider` Protocol, the DTOs, `StreamAccumulator`, `with_retry_stream()` |
| `mocode/providers/openai.py` | `OpenAIProvider` — concrete implementation for OpenAI-compatible APIs |

---

## DTOs

All are `@dataclass` types defined in `mocode/core/provider.py`. They are the **only types** the framework passes between the LLM and the agent engine.

### Chunk

```python
@dataclass
class Chunk:
    """One streaming delta. Every field is optional: a chunk carries what arrived."""
    text: str = ""
    reasoning: str = ""
    tool_call: ToolCallDelta | None = None
    usage: Usage | None = None
    finish_reason: str | None = None
```

A stream is a sequence of `Chunk`. Text and reasoning accumulate by concatenation; tool calls accumulate by `index` (see below). Most chunks carry exactly one field.

### ToolCallDelta

```python
@dataclass
class ToolCallDelta:
    """One fragment of a tool call inside a stream."""
    index: int = 0
    id: str = ""
    name: str = ""
    arguments: str = ""
```

Streaming APIs split a tool call across chunks: the first fragment carries the `index`, `id` and `name`, and every later fragment appends more JSON text to `arguments`. `index` is what identifies which call a fragment belongs to — a response may contain several tool calls interleaved.

### ToolCall

```python
@dataclass
class ToolCall:
    """A complete LLM-issued tool call."""
    id: str           # unique identifier (e.g. "call_abc123")
    name: str         # tool name (e.g. "read")
    arguments: str    # JSON-encoded argument string (e.g. '{"path": "README.md"}')
```

The `arguments` field is always a **raw JSON string**, not a parsed dict. This is intentional — `AgentLoop._parse_args()` handles parsing and validation at the tool execution layer, keeping the provider layer SDK-agnostic.

### Usage

```python
@dataclass
class Usage:
    """Token usage."""
    prompt_tokens: int
    completion_tokens: int
```

`total_tokens` is not stored — it is `prompt_tokens + completion_tokens` when needed.

Streaming APIs only report usage if the request asks for it, and they report it in a **final chunk that has no choices at all**. See `stream_options` below.

### Response

```python
@dataclass
class Response:
    """What a chunk stream adds up to."""
    content: str | None = None
    tool_calls: list[ToolCall] | None = None
    usage: Usage | None = None
    finish_reason: str | None = None
    reasoning_content: str | None = None
```

`Response` is not a provider return value — it is what :class:`StreamAccumulator` produces after consuming a stream, and what the `IterationFinished` event carries. A consumer that ignored the deltas reads the whole iteration from it.

Key design decisions:

- **`content` and `tool_calls` are both optional** — a response can contain only text, only tool calls, or both.
- **`usage` is optional** — not all providers report token counts.
- **`finish_reason` is a string** — not an enum, to remain provider-agnostic. Common values: `"stop"`, `"tool_calls"`, `"length"`.
- **`reasoning_content` is optional** — for models that expose chain-of-thought tokens (e.g. DeepSeek R1, OpenAI o1/o3).

### StreamAccumulator

```python
acc = StreamAccumulator()
async for chunk in provider.stream(...):
    acc.feed(chunk)
response = acc.build()
```

A plain object rather than a helper function, because a consumer that renders deltas needs to accumulate *while* they stream past, not after. `AgentLoop` uses it to build each iteration's `Response` while emitting `TextDelta` / `ReasoningDelta` events for the same chunks.

---

## Provider Protocol

```python
@runtime_checkable
class Provider(Protocol):
    """LLM provider protocol."""

    @property
    def model(self) -> str: ...

    def is_retriable(self, exc: Exception) -> bool: ...

    def stream(
        self,
        messages: list[dict[str, Any]],
        system: str,
        tools: list[dict[str, Any]],
        max_tokens: int | None,
    ) -> AsyncIterator[Chunk]: ...
```

`stream()` is the only required call. A provider that cannot stream natively yields a single `Chunk` holding the whole response — the loop does not care which it is.

### `model` property

Returns the model identifier string (e.g. `"gpt-4o"`, `"deepseek-r1"`). Used by:

- `AgentLoop` for logging and display.
- `Config` to track which model is active.
- Building the `ModelSpec` when a model is not listed in the config catalog.

### `is_retriable(exc) -> bool`

Determines whether an exception should be retried. `with_retry_stream()` delegates to this method for each caught exception. Return `True` for transient errors (rate limits, timeouts, connection failures) and `False` for permanent errors (invalid API key, malformed request).

### `stream()` — The Core Method

```python
def stream(
    self,
    messages: list[dict[str, Any]],   # conversation history (role/content/tool_calls/tool)
    system: str,                       # system prompt (single string)
    tools: list[dict[str, Any]],       # tool JSON schemas (empty list = no tools)
    max_tokens: int | None,            # output cap, or None to let the server decide
) -> AsyncIterator[Chunk]: ...
```

**Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `messages` | `list[dict[str, Any]]` | OpenAI-format message list. Each dict has `role` and either `content`, `tool_calls`, or `tool_call_id`. |
| `system` | `str` | The complete system prompt — a single string, not a message. The provider is responsible for placing it as the system message. |
| `tools` | `list[dict[str, Any]]` | JSON Schema tool definitions. An empty list means no tools are available (the provider should omit the `tools` parameter from the API call). |
| `max_tokens` | `int \| None` | Output cap for one response. `None` means MoCode sends no cap at all — the server applies its own. Set per model via `max_output`. |

**The provider must:**

1. Prepend the system message to the message list (or handle it as a separate parameter).
2. Call the LLM API, streaming.
3. Map each SDK chunk onto a `Chunk` DTO, dropping chunks that carry nothing.
4. Map tool-call fragments onto `ToolCallDelta` preserving `index`.

**The provider must not:**

- Modify the incoming message list (it may be shared).
- Translate SDK exceptions (let them propagate — `with_retry_stream()` catches them).
- Validate tool schemas (the caller guarantees they are valid).
- Accumulate chunks into a full response — that is `StreamAccumulator`'s job, and a streaming consumer wants the raw deltas.

**`stream()` is a generator function, not a coroutine.** Calling it produces an iterator without performing any I/O; the request is made when the consumer asks for the first chunk. That is what makes the retry window work — see below.

### `runtime_checkable`

The Protocol is decorated with `@runtime_checkable`, allowing isinstance checks on the class shape.

---

## Retry

`with_retry_stream()` wraps a stream factory with retry logic:

```python
async def with_retry_stream(
    provider: Provider,
    stream_fn: Callable[..., AsyncIterator[Chunk]],
    *args: Any,
    max_retries: int = 6,
    **kwargs: Any,
) -> AsyncIterator[Chunk]:
```

### The retry window closes at the first chunk

A stream cannot be replayed. Once a chunk has been handed to the caller, retrying would duplicate output — so retries only cover the window **before the first chunk arrives**, and after that an error propagates to the caller.

```
attempt → create stream → pull first chunk ─┬─ fails?  retry (inside the window)
                                            └─ succeeds → yield it, then pass
                                               everything through (outside the window)
```

In practice this is exactly the right window: connection failures, rate limits and authentication errors all surface when the request is made, which is inside the window. A failure mid-stream is a genuinely different situation that the caller has to handle anyway, because it already received partial output.

`CancelledError` is never retried.

### Backoff Parameters

| Parameter | Value | Description |
|-----------|-------|-------------|
| `max_retries` | 6 | Total of 7 attempts (1 initial + 6 retries) |
| `BASE_DELAY` | 1.0 s | Base delay for exponential backoff |
| `MAX_DELAY` | 60.0 s | Maximum delay cap |
| `JITTER_MAX` | 0.5 s | Random jitter added to each delay |

Delay formula: `min(BASE_DELAY * 2^attempt + random(0, JITTER_MAX), MAX_DELAY)`

| Attempt | Delay |
|---------|-------|
| 0 (1st retry) | ~1.0–1.5 s |
| 1 (2nd retry) | ~2.0–2.5 s |
| 2 (3rd retry) | ~4.0–4.5 s |
| 3 (4th retry) | ~8.0–8.5 s |
| 4 (5th retry) | ~16.0–16.5 s |
| 5 (6th retry) | ~32.0–32.5 s |

### Usage in AgentLoop

```python
async for chunk in with_retry_stream(provider, provider.stream, messages, system, tools, max_tokens):
    ...
```

The first argument is the `Provider` instance (used for `is_retriable()`); the second is the generator function to call.

### Logging

Retries are logged at WARNING level:

```
LLM call failed (RateLimitError), retrying in 2.3s (attempt 2/6)
```

---

## OpenAIProvider

The only built-in provider. Handles OpenAI's API and any **OpenAI-compatible** API (via `base_url` override).

### Constructor

```python
def __init__(
    self,
    api_key: str,
    model: str = "gpt-4o",
    base_url: str | None = None,
    extra_body: dict[str, Any] | None = None,
):
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `api_key` | `str` | (required) | API key for authentication |
| `model` | `str` | `"gpt-4o"` | Model identifier passed to the API |
| `base_url` | `str \| None` | `None` | Custom API base URL for compatible providers |
| `extra_body` | `dict \| None` | `None` | Additional fields merged into the API request body |

The constructor does **not** create the OpenAI client — that happens lazily on the first stream.

### `stream()` — The Core Method

**Internal steps:**

1. **Normalize messages** — calls `_normalize_messages()` to strip orphaned tool calls (see below).
2. **Prepend system message** — creates the message list with system first, followed by normalized conversation messages.
3. **Request the stream** — `await client.chat.completions.create(..., stream=True)`. The `await` performs the request, so a rate limit or a dead connection surfaces *inside* the retry window, before any chunk reaches the caller.
4. **Map each chunk** — `_to_chunk()` converts one SDK chunk into a `Chunk`, or returns `None` when it carries nothing.

**Tools handling:** when the tools list is empty, `None` is passed instead of an empty list, because the API rejects an empty `tools` array.

### `stream_options` and token accounting

Streaming responses report usage only when asked. MoCode sends `{"stream_options": {"include_usage": True}}` by default so token counts still work.

Some OpenAI-compatible endpoints reject that parameter. A model may override it by putting its own `stream_options` inside `extra_body` — that value wins, and it is lifted out of `extra_body` first so it is not sent twice:

```json
{
  "providers": {
    "intern": {
      "name": "Intern",
      "base_url": "https://example.invalid/v1",
      "models": {
        "some-model": {
          "extra_body": { "stream_options": { "include_usage": false } }
        }
      }
    }
  }
}
```

### Message Normalization

`_normalize_messages()` is a static method that fixes a subtle issue with OpenAI-format message histories:

**Problem:** If an assistant message contains `tool_calls` but the corresponding `tool` result messages are missing (e.g. due to session corruption, partial export, or tool execution failure), the API rejects the request.

**Solution:** The method builds a set of valid `tool_call_id` values from all `tool` messages, then filters assistant `tool_calls` to only include those with valid IDs.

| Case | Action |
|------|--------|
| No assistant messages have `tool_calls` | Return messages unchanged (fast path) |
| No valid `tool_call_id` values exist | Strip `tool_calls` from all assistant messages |
| Some `tool_call_id` values exist | Filter each assistant message to keep only tool calls with matching result messages |

This normalization is applied on every call and is critical for robustness when sessions are imported, resumed from JSON, or manually edited.

### Chunk Mapping

`_to_chunk()` is deliberately tolerant — compatible endpoints disagree about details:

| Source | Mapping |
|---|---|
| `delta.content` | `chunk.text`, empty string if absent or not a string |
| `delta.reasoning_content` | `chunk.reasoning` |
| `delta.reasoning` | `chunk.reasoning`, **only if it is a string** — the newest OpenAI models use `reasoning` for a structured object, which is ignored rather than stringified |
| `delta.tool_calls[0]` | `chunk.tool_call` — `index`, `id`, `function.name`, `function.arguments`; `index` defaults to 0 when an endpoint omits it |
| `choice.finish_reason` | `chunk.finish_reason` |
| `raw.usage` | `chunk.usage`, present on the final chunk which has no choices |

Chunks where every field is empty are dropped.

### Lazy Client Creation

```python
def _ensure_client(self):
    if self._client is None:
        from openai import AsyncOpenAI
        self._client = AsyncOpenAI(api_key=self._api_key, base_url=self._base_url)
    return self._client
```

The `openai` SDK is imported **only when needed** — not at module load time. The `AsyncOpenAI` client is created once and reused across all calls.

### Lazy Exception Loading

OpenAI exception classes are loaded lazily and cached as a class variable, so importing MoCode never imports `openai`. The tuple is reused for every `is_retriable()` check.

### Retriable Error Detection

| Exception | Meaning | Retriable? |
|-----------|---------|-----------|
| `RateLimitError` | 429 — too many requests | ✅ |
| `InternalServerError` | 500 — server-side error | ✅ |
| `APIConnectionError` | Network connectivity issue | ✅ |
| `APITimeoutError` | Request timed out | ✅ |

All other exceptions (e.g. `AuthenticationError`, `BadRequestError`) are **not retried** — they indicate permanent problems.

---

## Configuration Integration

`OpenAIProvider` is instantiated from `Config` data in `MoCode._create_provider()`:

| Config field | OpenAIProvider param | Notes |
|---|---|---|
| `ProviderEntry.api_key` | `api_key` | Falls back to `$<PROVIDER_KEY>_API_KEY` |
| `Config.active_model` | `model` | Passed as-is to the API |
| `ProviderEntry.base_url` | `base_url` | `None` uses the OpenAI default |
| `ModelEntry.extra_body` | `extra_body` | Per-model extra fields |

### `extra_body` Handling

`extra_body` is a dict of additional fields merged into the API request body. This enables provider-specific extensions without modifying provider code — `reasoning_effort`, `temperature`, custom headers, and `stream_options` (see above).

It is passed directly to the SDK's `chat.completions.create()`, which merges it into the request payload.

---

## Adding a New Provider

### Step 1: Create the Provider Module

Create a new file in `mocode/providers/` (e.g. `anthropic.py`):

```python
"""Anthropic Claude provider implementation."""

from __future__ import annotations

from typing import Any, AsyncIterator

from ..core.provider import Chunk, ToolCallDelta, Usage


class AnthropicProvider:
    """Anthropic Claude API provider — implements Provider Protocol."""

    def __init__(self, api_key: str, model: str = "claude-sonnet-4-20250514", **kwargs):
        self._api_key = api_key
        self._model = model
        self._client = None  # lazy

    @property
    def model(self) -> str:
        return self._model

    def is_retriable(self, exc: Exception) -> bool:
        from anthropic import RateLimitError, APIConnectionError, APITimeoutError
        return isinstance(exc, (RateLimitError, APIConnectionError, APITimeoutError))

    async def stream(
        self,
        messages: list[dict[str, Any]],
        system: str,
        tools: list[dict[str, Any]],
        max_tokens: int | None,
    ) -> AsyncIterator[Chunk]:
        # 1. Convert messages to your SDK's format
        # 2. Open the stream, passing max_tokens only when it is not None
        # 3. Map each event onto a Chunk

        async with self._ensure_client().messages.stream(
            model=self._model,
            system=system,          # Anthropic takes system as a separate param
            messages=messages,      # may need format conversion
            max_tokens=max_tokens or 4096,
            tools=tools or None,
        ) as stream:
            async for event in stream:
                if event.type == "content_block_delta":
                    if event.delta.type == "text_delta":
                        yield Chunk(text=event.delta.text)
                    elif event.delta.type == "input_json_delta":
                        # Tool arguments arrive as JSON fragments, keyed by block
                        yield Chunk(
                            tool_call=ToolCallDelta(
                                index=event.index, arguments=event.delta.partial_json
                            )
                        )
                elif event.type == "message_start":
                    yield Chunk(
                        usage=Usage(
                            prompt_tokens=event.message.usage.input_tokens,
                            completion_tokens=0,
                        )
                    )
                elif event.type == "message_delta":
                    yield Chunk(
                        usage=Usage(
                            prompt_tokens=0,
                            completion_tokens=event.usage.output_tokens,
                        ),
                        finish_reason=event.delta.stop_reason,
                    )
```

Note the shape: the provider does the SDK-specific work (opening a stream, naming events) and yields `Chunk`s. It never assembles a full response, and it never decides what a tool call *means*.

### Step 2: Register in `__init__.py`

Add a lazy import in `mocode/providers/__init__.py` so importing MoCode does not import the SDK:

```python
def __getattr__(name: str):
    if name == "AnthropicProvider":
        from .anthropic import AnthropicProvider
        globals()["AnthropicProvider"] = AnthropicProvider
        return AnthropicProvider
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
```

### Step 3: Add Config Support

`MoCode._create_provider()` picks the provider class for the active entry. With more than one implementation, turn that into a registry lookup keyed by something in `ProviderEntry`.

### Step 4: Handle Provider-Specific Message Formats

The `messages` list passed to `stream()` uses **OpenAI format**. If your provider uses a different format, convert inside `stream()`.

| Field | OpenAI format | Provider-specific format |
|-------|---------------|--------------------------|
| System prompt | `{"role": "system", "content": "..."}` | Separate `system` parameter (Anthropic) |
| Tool definitions | `tools` parameter | `tools` with a different schema |
| Tool calls | `message.tool_calls[].function.arguments` (JSON string) | May be a JSON object or a different structure |
| Tool results | `{"role": "tool", "tool_call_id": "..."}` | Different format (Anthropic uses `tool_result`) |

### Checklist

- [ ] Implements `Provider` Protocol (`model` property, `is_retriable`, `stream`)
- [ ] `stream` is an async generator, not a coroutine — the request happens on first iteration
- [ ] Yields `Chunk` DTOs, and never accumulates them into a `Response`
- [ ] Splits tool-call fragments by `index`, keeping `arguments` as raw JSON text
- [ ] Lazy SDK imports (never import the SDK at module level)
- [ ] `is_retriable()` returns `True` for transient errors, `False` for permanent ones
- [ ] Handles an empty tools list (pass `None` or omit the parameter)
- [ ] Sends no `max_tokens` when `max_tokens is None`
- [ ] Handles missing usage data (omit the `usage` field rather than reporting zeros)
- [ ] Reads `reasoning_content` only when it is a string

---

## Design Notes

### Protocol vs ABC

MoCode uses `Protocol` instead of an abstract base class for the Provider. Benefits:

- **No inheritance required** — providers are structurally typed. Any class with the right methods works.
- **No import dependency** — implementing a provider doesn't require importing `core.provider` as a base class (though it usually does for the DTOs).
- **Runtime checkable** — `isinstance(obj, Provider)` works for validation.

### DTOs Are Plain Dataclasses

The DTOs are simple dataclasses with no behavior: no validation, no serialization, no SDK references. This keeps them stable even as provider implementations change.

### Why `arguments` Is a String

`ToolCall.arguments` is a JSON string, not a parsed dict. Reasons:

1. **Lazy parsing** — if the tool call is rejected or the iteration is compacted, the JSON is never parsed.
2. **Lossless round-trip** — re-serializing a dict may produce different key ordering.
3. **One validation point** — `AgentLoop._parse_args()` is the single place argument JSON is parsed, keeping concerns separated.
4. **It arrives that way** — a streaming API sends `arguments` as fragments of a JSON string. Any other representation would mean joining and re-parsing inside every provider.

### Why streaming is the primitive

The alternative — a non-streaming `call()` plus a separate streaming method — would have meant two request paths, two retry policies and two response shapes, with the loop picking one and the other rotting. Making `stream()` the only method means:

- **One path through the loop.** Text deltas, tool-call assembly and usage reporting all fall out of consuming chunks, so there is no "streaming mode" that can drift from the non-streaming one.
- **A provider that cannot stream is trivial**, not special: yield one chunk.
- **A consumer that does not want deltas is trivial too**: `StreamAccumulator` gives it a `Response`.

### Provider Identity

The `model` property is the only identifying information. There's no `name` or `id` field — the provider is identified by its implementation class, and the model string distinguishes variants. `Config` tracks which provider *instance* is active via its key in the `providers` dict.
