# MoCode Providers Layer — Developer Reference

> Covers the Provider Protocol (`core/provider.py`), the built-in `OpenAIProvider` (`providers/openai.py`), and how to implement custom providers.

---

## Table of Contents

1. [Overview](#overview)
2. [DTOs — Response, ToolCall, Usage](#dtos--response-toolcall-usage)
3. [Provider Protocol](#provider-protocol)
4. [Retry with Exponential Backoff](#retry-with-exponential-backoff)
5. [OpenAIProvider](#openaiprovider)
   - [Constructor](#constructor)
   - [call() — The Core Method](#call--the-core-method)
   - [Message Normalization](#message-normalization)
   - [Lazy Client Creation](#lazy-client-creation)
   - [Lazy Exception Loading](#lazy-exception-loading)
   - [Retriable Error Detection](#retriable-error-detection)
   - [reasoning_content Support](#reasoning_content-support)
6. [Configuration Integration](#configuration-integration)
7. [Adding a New Provider](#adding-a-new-provider)
8. [Design Notes](#design-notes)

---

## Overview

MoCode abstracts LLM communication through a **Protocol-based provider system**. The LLM consumer (primarily `AgentLoop`) only ever touches three DTOs (`Response`, `ToolCall`, `Usage`) and never imports any SDK-specific types. This clean separation means:

- Adding a new LLM backend requires implementing a single Protocol.
- The rest of the framework (tools, hooks, prompts, workflow engine) is LLM-agnostic.
- Multiple providers can coexist — `Config` holds a registry of `ProviderEntry` objects.

The providers layer consists of two files:

| File | Role |
|------|------|
| `mocode/core/provider.py` | Defines `Provider` Protocol, `Response`/`ToolCall`/`Usage` DTOs, and `with_retry()` |
| `mocode/providers/openai.py` | `OpenAIProvider` — concrete implementation for OpenAI-compatible APIs |

---

## DTOs — Response, ToolCall, Usage

All three are `@dataclass` types defined in `mocode/core/provider.py`. They are the **only types** the framework passes between the LLM and the agent engine.

### ToolCall

```python
@dataclass
class ToolCall:
    """LLM-issued tool call."""
    id: str           # unique identifier (e.g. "call_abc123")
    name: str         # tool name (e.g. "read")
    arguments: str    # JSON-encoded argument string (e.g. '{"path": "README.md"}')
```

The `arguments` field is always a **raw JSON string**, not a parsed dict. This is intentional — `ToolRegistry.validate()` handles argument parsing and validation at the tool execution layer, keeping the provider layer SDK-agnostic.

### Usage

```python
@dataclass
class Usage:
    """Token usage."""
    prompt_tokens: int       # tokens consumed by the prompt
    completion_tokens: int   # tokens generated in the response
```

Note: `total_tokens` is not stored. It can be computed as `prompt_tokens + completion_tokens` when needed. The `Usage` DTO captures only the two values that the provider returns separately.

### Response

```python
@dataclass
class Response:
    """Normalized LLM response — all consumers only touch this type."""
    content: str | None = None              # text content (None if tool-only response)
    tool_calls: list[ToolCall] | None = None  # tool calls (None if no tools invoked)
    usage: Usage | None = None              # token counts (None if provider doesn't report)
    finish_reason: str | None = None        # e.g. "stop", "tool_calls", "length"
    reasoning_content: str | None = None    # chain-of-thought / reasoning tokens
```

Key design decisions:

- **`content` and `tool_calls` are both optional** — a response can contain only text, only tool calls, or both. Some reasoning models produce `reasoning_content` alongside `content`.
- **`usage` is optional** — not all providers report token counts.
- **`finish_reason` is a string** — not an enum, to remain provider-agnostic. Common values: `"stop"`, `"tool_calls"`, `"length"`.
- **`reasoning_content` is optional** — for models that expose chain-of-thought tokens (e.g. DeepSeek R1, OpenAI o1/o3). This field is read by the display layer and can be rendered separately from the main content.

---

## Provider Protocol

```python
@runtime_checkable
class Provider(Protocol):
    """LLM provider protocol."""

    @property
    def model(self) -> str: ...

    def is_retriable(self, exc: Exception) -> bool: ...

    async def call(
        self,
        messages: list[dict[str, Any]],
        system: str,
        tools: list[dict[str, Any]],
        max_tokens: int,
    ) -> Response: ...
```

The Protocol has three requirements:

### `model` property

Returns the model identifier string (e.g. `"gpt-4o"`, `"deepseek-r1"`). Used by:

- `AgentLoop` for logging and display.
- `Config` to track which model is active.
- `CompactHook` to determine context window limits.

### `is_retriable(exc) -> bool`

Determines whether an exception should be retried. The framework calls `with_retry()` which delegates to this method for each caught exception. Return `True` for transient errors (rate limits, timeouts, connection failures) and `False` for permanent errors (invalid API key, malformed request).

### `call()` — The Core Method

```python
async def call(
    self,
    messages: list[dict[str, Any]],   # conversation history (role/content/tool_calls/tool)
    system: str,                        # system prompt (single string)
    tools: list[dict[str, Any]],        # tool JSON schemas (empty list = no tools)
    max_tokens: int,                    # maximum response tokens
) -> Response: ...
```

**Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `messages` | `list[dict[str, Any]]` | OpenAI-format message list. Each dict has `role` and either `content`, `tool_calls`, or `tool_call_id`. |
| `system` | `str` | The complete system prompt — a single string, not a message. The provider is responsible for placing it as the system message. |
| `tools` | `list[dict[str, Any]]` | JSON Schema tool definitions. An empty list means no tools are available (the provider should omit the `tools` parameter from the API call). |
| `max_tokens` | `int` | Maximum tokens for the response. Default in `Config` is 8192. |

**The provider must:**

1. Prepend the system message to the message list (or handle it as a separate parameter).
2. Call the LLM API with the normalized messages.
3. Convert the SDK-specific response into a `Response` DTO.
4. Map `ToolCall` objects from SDK format to the DTO format.

**The provider must not:**

- Modify the incoming message list (it may be shared).
- Raise raw SDK exceptions (let them propagate — `with_retry()` catches them).
- Validate tool schemas (the caller guarantees they are valid).

### `runtime_checkable`

The Protocol is decorated with `@runtime_checkable`, allowing isinstance checks:

```python
if isinstance(provider, Provider):
    response = await provider.call(...)
```

This is used by `AgentLoop` and other components to validate that a provider object actually conforms to the Protocol at runtime.

---

## Retry with Exponential Backoff

`core/provider.py` includes a standalone `with_retry()` helper that wraps any async callable with retry logic:

```python
async def with_retry(
    provider: Provider,
    fn: Callable[..., Awaitable[T]],
    *args: Any,
    max_retries: int = 6,
    **kwargs: Any,
) -> T:
```

### How It Works

1. Call `fn(*args, **kwargs)`.
2. If the call succeeds, return the result.
3. If it raises `CancelledError`, re-raise immediately (never retry user cancellation).
4. If it raises a retriable exception (per `provider.is_retriable()`), sleep and retry.
5. If it raises a non-retriable exception or retries are exhausted, raise.

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

`AgentLoop` uses `with_retry()` for every LLM call:

```python
response = await with_retry(self._provider, self._provider.call, messages, system, tools, max_tokens)
```

The first argument is the `Provider` instance (used for `is_retriable()`), and the second is the callable to invoke (typically `provider.call`).

### Logging

Retries are logged at WARNING level:

```
LLM call failed (RateLimitError), retrying in 2.3s (attempt 2/6)
```

---

## OpenAIProvider

The only built-in provider. Handles OpenAI's API and any **OpenAI-compatible** API (via `base_url` override).

```python
class OpenAIProvider:
    """OpenAI-compatible API provider — implements Provider Protocol."""
```

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

The constructor does **not** create the OpenAI client — that happens lazily on first `call()`.

### `call()` — The Core Method

```python
async def call(
    self,
    messages: list[dict[str, Any]],
    system: str,
    tools: list[dict[str, Any]],
    max_tokens: int,
) -> Response:
```

**Internal steps:**

1. **Normalize messages** — calls `_normalize_messages()` to strip orphaned tool calls (see below).
2. **Prepend system message** — creates `openai_messages` list with system message first, followed by normalized conversation messages.
3. **Call the API** — `await self._ensure_client().chat.completions.create(...)` with model, messages, tools, max_tokens, and extra_body.
4. **Extract tool calls** — maps `message.tool_calls` to `ToolCall` DTOs.
5. **Extract usage** — maps `raw.usage` to a `Usage` DTO (handles `None` values).
6. **Extract reasoning** — reads `message.reasoning_content` via `getattr()` (not all models support this).
7. **Build Response** — assembles and returns the normalized `Response` DTO.

**Tools handling:**

```python
tools=tools or None,  # type: ignore[arg-type]
```

When the tools list is empty, `None` is passed instead of an empty list. This is required because the OpenAI API rejects an empty `tools` array — it expects the parameter to be omitted entirely.

### Message Normalization

`_normalize_messages()` is a static method that fixes a subtle issue with OpenAI-format message histories:

**Problem:** If an assistant message contains `tool_calls` but the corresponding `tool` result messages are missing (e.g. due to session corruption, partial export, or tool execution failure), the API rejects the request.

**Solution:** The method builds a set of valid `tool_call_id` values from all `tool` messages, then filters assistant `tool_calls` to only include those with valid IDs.

Three cases are handled:

| Case | Action |
|------|--------|
| No assistant messages have `tool_calls` | Return messages unchanged (fast path) |
| No valid `tool_call_id` values exist | Strip `tool_calls` from all assistant messages |
| Some `tool_call_id` values exist | Filter each assistant message to keep only tool calls with matching result messages |

This normalization is applied on every call and is critical for robustness when sessions are imported, resumed from JSON, or manually edited.

### Lazy Client Creation

```python
def _ensure_client(self):
    if self._client is None:
        from openai import AsyncOpenAI
        self._client = AsyncOpenAI(api_key=self._api_key, base_url=self._base_url)
    return self._client
```

The `openai` SDK is imported **only when needed** — not at module load time. This contributes to MoCode's fast startup (~180ms). The `AsyncOpenAI` client is created once and reused across all calls.

### Lazy Exception Loading

```python
_exc_classes: tuple[type[Exception], ...] | None = None

@classmethod
def _load_exc_classes(cls) -> tuple[type[Exception], ...]:
    if cls._exc_classes is None:
        from openai import (
            RateLimitError, InternalServerError,
            APIConnectionError, APITimeoutError,
        )
        cls._exc_classes = (
            RateLimitError, InternalServerError,
            APIConnectionError, APITimeoutError,
        )
    return cls._exc_classes
```

OpenAI exception classes are loaded lazily and cached as a class variable. This avoids importing the entire `openai` module at startup. The tuple is reused for every `is_retriable()` check.

### Retriable Error Detection

```python
def is_retriable(self, exc: Exception) -> bool:
    return isinstance(exc, self._load_exc_classes())
```

| Exception | Meaning | Retriable? |
|-----------|---------|-----------|
| `RateLimitError` | 429 — too many requests | ✅ |
| `InternalServerError` | 500 — server-side error | ✅ |
| `APIConnectionError` | Network connectivity issue | ✅ |
| `APITimeoutError` | Request timed out | ✅ |

All other exceptions (e.g. `AuthenticationError`, `BadRequestError`) are **not retried** — they indicate permanent problems.

### `reasoning_content` Support

```python
reasoning_content = getattr(message, "reasoning_content", None)
```

Models like DeepSeek R1 and OpenAI o1/o3 expose chain-of-thought tokens via a `reasoning_content` field on the response message. `OpenAIProvider` reads this field using `getattr()` with a `None` default — if the field doesn't exist (i.e. the model doesn't support it), it's safely ignored.

This field is surfaced in the `Response` DTO and can be displayed by the CLI display layer (e.g. collapsed reasoning output).

---

## Configuration Integration

`OpenAIProvider` is instantiated from `Config` data in `CLIApp._build_provider()`:

```python
# Pseudocode from CLIApp:
provider = OpenAIProvider(
    api_key=config.api_key,
    model=config.active_model,
    base_url=config.current.base_url,
    extra_body=config.extra_body,
)
```

The `Config` → `OpenAIProvider` mapping:

| Config field | OpenAIProvider param | Notes |
|---|---|---|
| `ProviderEntry.api_key` | `api_key` | Required |
| `Config.active_model` | `model` | Passed as-is to the API |
| `ProviderEntry.base_url` | `base_url` | `None` uses OpenAI default |
| `ModelEntry.extra_body` | `extra_body` | Per-model extra fields |

### `extra_body` Handling

`extra_body` is a dict of additional fields merged into the API request body. This enables provider-specific extensions without modifying the provider code:

```json
{
  "providers": {
    "openai": {
      "name": "OpenAI",
      "api_key": "sk-...",
      "models": [
        {
          "name": "gpt-4o",
          "extra_body": { "reasoning_effort": "high" }
        }
      ]
    }
  }
}
```

The `extra_body` dict is passed directly to the `openai` SDK's `chat.completions.create()` method, which merges it into the request payload. This is the primary extension mechanism for non-standard API fields (e.g. `reasoning_effort`, `temperature`, custom headers).

---

## Adding a New Provider

To add a custom LLM provider:

### Step 1: Create the Provider Module

Create a new file in `mocode/providers/` (e.g. `anthropic.py`, `ollama.py`):

```python
"""Anthropic Claude provider implementation."""

from __future__ import annotations

from typing import Any

from ..core.provider import Provider, Response, ToolCall, Usage


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
        # Import your SDK's retriable exceptions here
        from anthropic import RateLimitError, APIConnectionError, APITimeoutError
        return isinstance(exc, (RateLimitError, APIConnectionError, APITimeoutError))

    async def call(
        self,
        messages: list[dict[str, Any]],
        system: str,
        tools: list[dict[str, Any]],
        max_tokens: int,
    ) -> Response:
        # 1. Convert messages to your SDK's format
        # 2. Call the API
        # 3. Convert response to Response DTO

        # Example (pseudocode):
        client = self._ensure_client()
        response = await client.messages.create(
            model=self._model,
            system=system,           # Anthropic takes system as separate param
            messages=messages,       # May need format conversion
            max_tokens=max_tokens,
            tools=tools or None,
        )

        # Convert to Response DTO
        return Response(
            content=response.content[0].text if response.content else None,
            tool_calls=[
                ToolCall(id=tc.id, name=tc.name, arguments=tc.input)
                for tc in response.tool_calls
            ] if response.tool_calls else None,
            usage=Usage(
                prompt_tokens=response.usage.input_tokens,
                completion_tokens=response.usage.output_tokens,
            ),
            finish_reason=response.stop_reason,
        )
```

### Step 2: Register in `__init__.py`

Add a lazy import in `mocode/providers/__init__.py`:

```python
def __getattr__(name: str):
    if name == "AnthropicProvider":
        from .anthropic import AnthropicProvider
        globals()["AnthropicProvider"] = AnthropicProvider
        return AnthropicProvider
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
```

### Step 3: Add Config Support

The `Config` → `ProviderEntry` → `Provider` wiring in `CLIApp._build_provider()` determines which constructor to call. You'll need to:

1. Add a `provider_type` field or use `base_url` to distinguish provider types.
2. Map `ProviderEntry` fields to your constructor parameters.
3. Register the provider in the `/connect` command flow.

### Step 4: Handle Provider-Specific Message Formats

Different LLM APIs use slightly different message formats. Common conversions:

| Field | OpenAI Format | Provider-specific format |
|-------|---------------|--------------------------|
| System prompt | `{"role": "system", "content": "..."}` | Separate `system` parameter (Anthropic) |
| Tool definitions | `tools` parameter | `tools` with different schema |
| Tool calls | `message.tool_calls[].function.arguments` (JSON string) | May be JSON object or different structure |
| Tool results | `{"role": "tool", "tool_call_id": "..."}` | Different format (Anthropic uses `tool_result`) |

The `messages` list passed to `call()` uses **OpenAI format**. If your provider uses a different format, convert inside `call()`.

### Checklist

- [ ] Implements `Provider` Protocol (property `model`, method `is_retriable`, method `call`)
- [ ] Returns `Response` DTO (not raw SDK objects)
- [ ] Lazy SDK imports (don't import the SDK at module level)
- [ ] `is_retriable()` returns `True` for transient errors (rate limits, timeouts, connection failures)
- [ ] `is_retriable()` returns `False` for permanent errors (bad auth, invalid request)
- [ ] Handles empty tools list (pass `None` or omit the parameter)
- [ ] Handles missing usage data (default to `None` if not reported)
- [ ] Reads `reasoning_content` if supported by the model (via `getattr` with `None` default)

---

## Design Notes

### Protocol vs ABC

MoCode uses `Protocol` instead of an abstract base class for the Provider. Benefits:

- **No inheritance required** — providers are "structurally typed" (duck-typed). Any class with the right methods works.
- **No import dependency** — implementing a provider doesn't require importing `core.provider` as a base class (though it usually does for the DTOs).
- **Runtime checkable** — `isinstance(obj, Provider)` works for validation.

### DTOs Are Plain Dataclasses

The DTOs (`Response`, `ToolCall`, `Usage`) are simple dataclasses with no behavior:

- No validation logic.
- No serialization methods (they're never serialized to JSON — they flow from provider to agent engine in-memory).
- No SDK references.

This keeps the DTOs stable even as provider implementations change.

### Why `arguments` Is a String

`ToolCall.arguments` is a JSON string, not a parsed dict. Reasons:

1. **Lazy parsing** — if the tool call is rejected or the iteration is compacted, the JSON is never parsed.
2. **Lossless round-trip** — re-serializing a dict may produce different key ordering.
3. **Schema validation** — `ToolRegistry.validate()` is the single point of argument parsing and validation, keeping concerns separated.

### Why `extra_body` Exists

The OpenAI-compatible API ecosystem includes hundreds of providers (Ollama, vLLM, Together AI, Fireworks, Groq, etc.). Many support model-specific parameters not captured in the standard `chat.completions.create()` signature. `extra_body` provides a catch-all escape hatch for these parameters without modifying provider code.

### No Streaming Support

The current `Provider` Protocol defines only non-streaming `call()`. Streaming is not implemented because:

- The agent loop processes complete responses (to check for tool calls and parse content).
- Streaming adds complexity without significant benefit for tool-using agents (the model must complete its response before tool calls can be executed).
- Display-layer streaming (e.g. typing effect) can be achieved by chunking the response text after it arrives.

### Provider Identity

The `model` property is the only identifying information. There's no `name` or `id` field — the provider is identified by its implementation class, and the model string distinguishes variants. The `Config` system tracks which provider *instance* is active via its key in the `providers` dict.
