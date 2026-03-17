# mocode

A CLI coding assistant powered by LLM with tool-calling capabilities.

[中文文档](README_CN.md)

## Features

- **Interactive CLI** - Beautiful terminal interface with real-time streaming output
- **Tool Calling** - Built-in tools for file operations, search, and shell execution
- **Multi-Provider** - Support for OpenAI-compatible APIs (OpenAI, Claude, DeepSeek, etc.)
- **SDK Mode** - Embed mocode into your own applications
- **Gateway Mode** - Run as a multi-channel bot (Telegram supported)
- **Skill System** - Extensible with custom skills
- **Permission Control** - Fine-grained tool permission management
- **Interrupt Support** - Cancel long-running operations with ESC key

## Installation

```bash
# Install with uv
uv tool install -e .

# Or install dependencies
uv sync
```

## Quick Start

### CLI Mode

```bash
# Run the interactive CLI
mocode

# Or use uv run
uv run mocode
```

### Gateway Mode

```bash
# Run as a Telegram bot
mocode gateway
```

## Configuration

Configuration is stored at `~/.mocode/config.json`:

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
    }
  },
  "permission": {
    "*": "ask",
    "bash": "allow",
    "read": "allow"
  },
  "max_tokens": 8192,
  "gateway": {
    "channels": {
      "telegram": {
        "enabled": true,
        "token": "bot_token",
        "allowFrom": ["telegram_user_id"]
      }
    }
  }
}
```

### Permission Settings

| Value | Description |
|-------|-------------|
| `allow` | Execute without asking |
| `ask` | Prompt for confirmation |
| `deny` | Block execution |

## SDK Usage

```python
import asyncio
from mocode import MocodeClient, EventType

async def main():
    # Create client with in-memory config
    client = MocodeClient(config={
        "current": {"provider": "openai", "model": "gpt-4o"},
        "providers": {
            "openai": {
                "api_key": "sk-...",
                "base_url": "https://api.openai.com/v1",
                "models": ["gpt-4o"]
            }
        }
    })

    # Subscribe to events
    client.on_event(EventType.TEXT_COMPLETE, lambda e: print(f"[Response] {e.data}"))
    client.on_event(EventType.INTERRUPTED, lambda e: print("Cancelled"))

    # Chat
    response = await client.chat("Hello!")
    print(response)

    # Interrupt ongoing operation (from another task/thread)
    client.interrupt()

    # Clear history
    client.clear_history()

asyncio.run(main())
```

## Skills

Skills are discovered from `~/.mocode/skills/` directory. Each skill is a `SKILL.md` file:

```markdown
---
name: my-skill
description: What this skill does
---

# Instructions

Detailed instructions for the LLM...
```

## Built-in Commands

| Command | Description |
|---------|-------------|
| `/help` | Show available commands |
| `/model` | Switch model |
| `/provider` | Switch provider |
| `/clear` | Clear conversation history |
| `/skills` | List available skills |
| `/exit` | Exit the application |

## Architecture

```
mocode/
├── sdk.py              # SDK entry point (MocodeClient)
├── main.py             # Entry point (CLI or gateway mode)
├── core/               # Business logic (independent of UI)
│   ├── agent.py        # AsyncAgent - LLM conversation loop
│   ├── config.py       # Multi-provider config
│   ├── events.py       # EventBus - event-driven communication
│   ├── interrupt.py    # InterruptToken - cancel operations
│   └── permission.py   # Permission management
├── gateway/            # Multi-channel bot support
│   ├── manager.py      # GatewayManager
│   └── telegram.py     # Telegram channel
├── providers/          # LLM providers
│   └── openai.py       # OpenAI-compatible provider
├── tools/              # Tool implementations
│   ├── file_tools.py   # read, write, edit
│   ├── search_tools.py # glob, grep
│   └── shell_tools.py  # bash
├── skills/             # Skill system
│   └── manager.py      # SkillManager
└── cli/                # Terminal interface
    ├── app.py          # Main application
    ├── commands/       # Slash commands
    └── ui/             # Layout, colors, widgets
```

## Development

```bash
# Install dependencies
uv sync

# Run CLI
uv run mocode
```

## Requirements

- Python >= 3.10
- OpenAI-compatible API access

## License

MIT
