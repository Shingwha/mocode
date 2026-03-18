# mocode

A CLI coding assistant powered by LLM with tool-calling capabilities.

[中文文档](README_CN.md)

## Features

- Interactive CLI with real-time streaming output
- Built-in tools for file operations, search, and shell execution
- Multi-provider support (OpenAI, Claude, DeepSeek, etc.)
- SDK mode for embedding into applications
- Gateway mode for multi-channel bots (Telegram)
- Extensible skill system
- Fine-grained permission control

## Documentation

- [Provider Configuration](docs/provider.md)
- [Permission System](docs/permission.md)
- [Gateway Mode](docs/gateway.md)
- [CLI Commands](docs/cli.md)

## Installation

```bash
uv tool install -e .
```

## Quick Start

```bash
mocode           # CLI mode
mocode gateway   # Gateway mode (Telegram bot)
```

## Configuration

Config stored at `~/.mocode/config.json`:

```json
{
  "current": { "provider": "openai", "model": "gpt-4o" },
  "providers": {
    "openai": {
      "name": "OpenAI",
      "base_url": "https://api.openai.com/v1",
      "api_key": "sk-...",
      "models": ["gpt-4o", "gpt-4o-mini"]
    }
  },
  "permission": { "*": "ask", "bash": "allow", "read": "allow" }
}
```

## SDK Usage

```python
from mocode import MocodeClient, EventType

async def main():
    client = MocodeClient(config={
        "current": {"provider": "openai", "model": "gpt-4o"},
        "providers": {"openai": {"api_key": "sk-...", "base_url": "https://api.openai.com/v1"}}
    })

    client.on_event(EventType.TEXT_COMPLETE, lambda e: print(e.data))
    await client.chat("Hello!")

asyncio.run(main())
```

## Commands

| Command | Aliases | Description |
|---------|---------|-------------|
| `/help` | `/h`, `/?` | Show commands |
| `/model` | `/m` | Switch model |
| `/provider` | `/p` | Switch provider |
| `/clear` | `/c` | Clear history |
| `/skills` | | List skills |
| `/rtk` | | Manage RTK |
| `/exit` | `/q`, `quit` | Exit |

## Skills

Skills are discovered from `~/.mocode/skills/`:

```markdown
---
name: my-skill
description: What this skill does
---

# Instructions
Detailed instructions for the LLM...
```

## Architecture

```
mocode/
├── sdk.py              # MocodeClient
├── main.py             # Entry point
├── core/               # Business logic
│   ├── agent.py        # AsyncAgent
│   ├── config.py       # Multi-provider config
│   ├── events.py       # EventBus
│   └── permission.py   # Permission management
├── gateway/            # Multi-channel bot
├── providers/          # LLM providers
├── tools/              # Tool implementations
├── skills/             # Skill system
└── cli/                # Terminal interface
```

## Requirements

- Python >= 3.10
- OpenAI-compatible API

## License

MIT
