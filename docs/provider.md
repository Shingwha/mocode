# Provider Configuration

mocode supports multiple LLM providers through OpenAI-compatible APIs.

## Quick Example

```json
{
  "current": {
    "provider": "openai",
    "model": "gpt-4o"
  },
  "providers": {
    "openai": {
      "base_url": "https://api.openai.com/v1",
      "api_key": "sk-..."
    },
    "anthropic": {
      "base_url": "https://api.anthropic.com/v1",
      "api_key": "sk-ant-..."
    }
  }
}
```

## Provider Fields

| Field | Required | Description |
|-------|----------|-------------|
| `base_url` | Yes | API base URL (OpenAI-compatible) |
| `api_key` | Yes | API key for authentication |

## Switching Providers

### In the CLI

```bash
/provider          # Interactive selection
/p                 # Alias
/provider deepseek # Direct selection by key
/provider 2        # Selection by number
```

### In Code

```python
from mocode import MocodeCore

client = MocodeCore()

# Switch provider
client.set_provider("anthropic")

# Switch provider and model together
client.set_model("claude-sonnet-4", provider="anthropic")
```

## Adding Custom Providers

Any OpenAI-compatible API (including local LLMs) can be added:

```json
{
  "providers": {
    "local": {
      "base_url": "http://localhost:11434/v1",
      "api_key": "dummy"
    }
  }
}
```

## Environment Variables

mocode reads `OPENAI_API_KEY` as a fallback when no `api_key` is configured in the provider settings.

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
      "models": ["gpt-4o", "gpt-4o-mini"]
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
  }
}
```
