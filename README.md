# mocode

A CLI coding assistant powered by LLM with tool-calling capabilities.

[中文文档](README_CN.md) | [Documentation](docs/)

## Features

- Interactive CLI with streaming output
- File operations, search, and shell execution
- Multi-provider support (OpenAI, Claude, etc.)
- SDK for embedding in applications
- Plugin system with hooks
- Permission control with modes
- Session management
- WeChat integration via Gateway

## Prerequisites

- Python >= 3.12
- [uv](https://docs.astral.sh/uv/) package manager

## Installation

```bash
uv tool install git+https://github.com/Shingwha/mocode.git
```

## Quick Start

```bash
# Start the CLI
mocode

# Start WeChat gateway
mocode gateway --type weixin
```

**First run?** Create `~/.mocode/config.json` with your API key (see [Configuration](#configuration) below).

## Configuration

Create `~/.mocode/config.json` with your LLM provider credentials:

### Basic Configuration (Single Provider)

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
    }
  },
  "permission": {
    "*": "ask"
  }
}
```

### Complete Configuration (Multiple Providers)

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
    "anthropic": {
      "name": "Anthropic",
      "base_url": "https://api.anthropic.com/v1",
      "api_key": "sk-ant-...",
      "models": ["claude-opus-4", "claude-sonnet-4", "claude-3.5-haiku"]
    },
    "deepseek": {
      "name": "DeepSeek",
      "base_url": "https://api.deepseek.com/v1",
      "api_key": "sk-...",
      "models": ["deepseek-chat", "deepseek-coder"]
    },
    "local": {
      "name": "Local LLM (Ollama)",
      "base_url": "http://localhost:11434/v1",
      "api_key": "dummy",
      "models": ["llama3.2", "codellama", "mistral"]
    }
  },
  "permission": {
    "*": "ask",
    "read": "allow",
    "bash": {
      "*": "ask",
      "ls *": "allow",
      "cat *": "allow",
      "pwd": "allow",
      "git *": "allow"
    }
  },
  "tool_result_limit": 25000
}
```

### Available Model Names

| Provider | Models |
|----------|--------|
| OpenAI | `gpt-4o`, `gpt-4o-mini`, `gpt-4-turbo`, `gpt-3.5-turbo` |
| Anthropic | `claude-opus-4`, `claude-sonnet-4`, `claude-3.5-haiku`, `claude-3-opus` |
| DeepSeek | `deepseek-chat`, `deepseek-coder` |
| Local (Ollama) | `llama3.2`, `codellama`, `mistral`, `qwen2.5-coder` |

> **Note**: Ensure the `model` field matches one of the models listed in the provider's `models` array.

### Configuration Fields

| Field | Type | Description |
|-------|------|-------------|
| `current.provider` | string | Active provider key (must match a provider key) |
| `current.model` | string | Model name to use |
| `providers` | object | Provider configurations keyed by provider name |
| `providers[*].base_url` | string | API endpoint (OpenAI-compatible) |
| `providers[*].api_key` | string | Authentication key |
| `providers[*].models` | array | Available models for this provider |
| `permission` | object | Tool execution rules |
| `tool_result_limit` | number | Max tool output size (0 = unlimited, default: 25000) |
| `gateway` | object | Gateway settings (see [Gateway docs](docs/gateway.md)) |

### Environment Variables

You can use environment variables instead of hardcoding API keys:

```json
{
  "providers": {
    "openai": {
      "base_url": "https://api.openai.com/v1",
      "api_key": "${OPENAI_API_KEY}"
    }
  }
}
```

mocode also reads `OPENAI_API_KEY` as a fallback if no key is configured.

### Switching Providers in CLI

```bash
/provider          # Interactive menu
/provider openai   # Direct selection
/p                 # Alias
```

### Permission Rules

Control which tools require confirmation:

```json
{
  "permission": {
    "*": "ask",              # Default: ask before running
    "read": "allow",         # Read tools: always allow
    "write": "deny",         # Write tools: always block
    "bash": {               # Per-command bash rules
      "*": "ask",
      "ls *": "allow",
      "cat *": "allow",
      "rm *": "deny"
    }
  }
}
```

See [Permission System](docs/permission.md) for detailed rules.

### Mode System

Quickly switch between safety and convenience:

```bash
/mode          # Show current mode
/mode list     # List all available modes
/mode yolo     # Auto-approve safe tools
/mode normal   # Respect permission rules (default)
```

- **normal** (default): Respects all permission rules, prompts for `ask` tools
- **yolo**: Auto-approves safe tools, only blocks dangerous bash commands (rm, mv, dd, format, etc.)

You can define custom modes in `config.json`:

```json
{
  "modes": {
    "safe": {
      "auto_approve": false,
      "dangerous_patterns": ["rm ", "rmdir ", "dd ", "mv "]
    },
    "fast": {
      "auto_approve": true,
      "dangerous_patterns": ["rm ", "format ", "mkfs "]
    }
  }
}
```

For detailed configuration, see:
- [Provider Configuration](docs/provider.md)
- [Permission System](docs/permission.md)
- [CLI Commands](docs/cli.md)

## Gateway

Run mocode as a bot on messaging platforms:

```bash
# Start WeChat gateway
mocode gateway --type weixin
```

See [Gateway Documentation](docs/gateway.md) for details.

## CLI Commands

| Command | Aliases | Description |
|---------|---------|-------------|
| `/help` | `/`, `/h`, `/?` | Show help |
| `/provider` | `/p` | Switch provider |
| `/mode` | | Switch mode |
| `/session` | `/s` | Manage sessions |
| `/clear` | `/c` | Clear history |
| `/skills` | | List skills |
| `/plugin` | | Manage plugins |
| `/rtk` | | Token optimizer |
| `/exit` | `/q`, `/quit` | Exit |

## SDK Usage

```python
from mocode import MocodeCore
import asyncio

async def main():
    config = {
        "current": {"provider": "openai", "model": "gpt-4o"},
        "providers": {
            "openai": {
                "base_url": "https://api.openai.com/v1",
                "api_key": "sk-..."
            }
        },
        "permission": {"*": "ask"}
    }

    client = MocodeCore(config=config, persistence=False)
    response = await client.chat("Hello!")
    print(response)

asyncio.run(main())
```

## Documentation

- [Installation Guide](docs/installation.md)
- [CLI Commands](docs/cli.md)
- [Provider Configuration](docs/provider.md)
- [Permission System](docs/permission.md)
- [Plugin System](docs/plugins.md)
- [Skills](docs/skills.md)
- [Gateway](docs/gateway.md)

## License

MIT
