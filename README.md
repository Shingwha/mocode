# mocode

A CLI coding assistant powered by LLM with tool-calling capabilities.

[中文文档](README_CN.md)

## Features

- Interactive CLI with streaming output
- File operations, search, and shell execution
- Multi-provider support (OpenAI, Claude, etc.)
- SDK for embedding in applications
- Plugin system with hooks
- Permission control with modes
- Session management

## Prerequisites

- Python >= 3.10
- [uv](https://docs.astral.sh/uv/) package manager

## Installation

```bash
uv tool install git+https://github.com/Shingwha/mocode.git
```

Or for development:

```bash
git clone https://github.com/Shingwha/mocode.git
cd mocode
uv tool install -e .
```

## Quick Start

```bash
mocode
```

## Configuration

Config: `~/.mocode/config.json`

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
    }
  },
  "permission": {
    "*": "ask",
    "bash": "allow"
  }
}
```

## Commands

| Command | Aliases | Description |
|---------|---------|-------------|
| `/help` | `/h`, `/?` | Show help |
| `/provider` | `/p` | Switch provider |
| `/mode` | `/m` | Switch mode |
| `/session` | `/s` | Manage sessions |
| `/clear` | `/c` | Clear history |
| `/plugin` | | Manage plugins |
| `/rtk` | | Token optimizer |
| `/exit` | `/q` | Exit |

### Session Commands

- `/session` - Interactive session selection
- `/session list` - List all sessions
- `/session restore <id>` - Restore session

## RTK (Token Optimization)

RTK reduces token usage by intelligently filtering command outputs.

```bash
/rtk status   # View stats
/rtk on       # Enable
/rtk off      # Disable
```

## SDK Usage

```python
from mocode import MocodeClient
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

    client = MocodeClient(config=config, persistence=False)

    response = await client.chat("Hello!")
    print(response)

    # Switch model
    client.set_model("gpt-4o-mini")

    # Session management
    client.save_session()
    sessions = client.list_sessions()

asyncio.run(main())
```

## Project Structure

```
mocode/
├── core/          # Core logic
├── plugins/       # Plugin system
├── providers/     # LLM providers
├── tools/         # Built-in tools
├── skills/        # Skill system
├── cli/           # Terminal interface
└── sdk.py         # SDK entry
```

## Requirements

- Python >= 3.10
- OpenAI-compatible API

## License

MIT
