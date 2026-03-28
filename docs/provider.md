# Provider Configuration

mocode supports multiple LLM providers through OpenAI-compatible APIs. This guide explains how to configure and manage providers.

## Configuration Format

Provider configuration is stored in `~/.mocode/config.json`:

```json
{
  "current": {
    "provider": "openai",
    "model": "gpt-4o"
  },
  "providers": {
    "openai": {
      "name": "OpenAI",
      "base_url": "https://api.openai.com/v1",
      "api_key": "sk-...",
      "models": ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo"]
    },
    "deepseek": {
      "name": "DeepSeek",
      "base_url": "https://api.deepseek.com/v1",
      "api_key": "sk-...",
      "models": ["deepseek-chat", "deepseek-coder"]
    }
  }
}
```

## Provider Fields

| Field | Required | Description |
|-------|----------|-------------|
| `name` | Yes | Display name for the provider |
| `base_url` | Yes | API base URL (OpenAI-compatible) |
| `api_key` | Yes | API key for authentication |
| `models` | No | List of available model names |

## Switching Providers

### CLI Mode

Use the `/provider` command:

```bash
# Interactive selection
/provider

# Use alias
/p

# Direct selection by key
/provider deepseek

# Selection by number
/provider 2
```

When switching providers, mocode automatically prompts for model selection.

### SDK Mode

```python
from mocode import MocodeClient

client = MocodeClient()

# Switch provider
client.set_provider("deepseek")

# Switch provider and model together
client.set_model("deepseek-coder", provider="deepseek")
```

## Adding Custom Providers

Add a new provider entry in your config:

```json
{
  "providers": {
    "my-custom": {
      "name": "My Custom API",
      "base_url": "https://api.example.com/v1",
      "api_key": "your-api-key",
      "models": ["model-1", "model-2"]
    }
  }
}
```

Any OpenAI-compatible API can be used as a provider.

## Environment Variables

mocode reads the `OPENAI_API_KEY` environment variable as a fallback when no `api_key` is configured:

```bash
export OPENAI_API_KEY=sk-...
```

## Multi-Model Support

Each provider can list multiple models. Use the `/provider` command to switch provider and model together, or use the SDK to switch programmatically:

```python
client.set_model("gpt-4o-mini")  # Switch model within current provider
client.set_provider("deepseek", "deepseek-coder")  # Switch provider and model
```

## SDK Configuration

Create a client with in-memory configuration:

```python
from mocode import MocodeClient

client = MocodeClient(config={
    "current": {"provider": "openai", "model": "gpt-4o"},
    "providers": {
        "openai": {
            "api_key": "sk-...",
            "base_url": "https://api.openai.com/v1",
            "models": ["gpt-4o", "gpt-4o-mini"]
        },
        "anthropic": {
            "api_key": "sk-ant-...",
            "base_url": "https://api.anthropic.com/v1",
            "models": ["claude-3-opus", "claude-3-sonnet"]
        }
    }
})
```

## Complete Configuration Example

```json
{
  "current": {
    "provider": "openai",
    "model": "gpt-4o"
  },
  "providers": {
    "openai": {
      "name": "OpenAI",
      "base_url": "https://api.openai.com/v1",
      "api_key": "sk-...",
      "models": ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo"]
    },
    "deepseek": {
      "name": "DeepSeek",
      "base_url": "https://api.deepseek.com/v1",
      "api_key": "sk-...",
      "models": ["deepseek-chat", "deepseek-coder"]
    },
    "local": {
      "name": "Local LLM",
      "base_url": "http://localhost:11434/v1",
      "api_key": "dummy",
      "models": ["llama3", "mistral"]
    }
  },
  "permission": {
    "*": "ask",
    "bash": "allow",
    "read": "allow"
  },
  "max_tokens": 8192
}
```
