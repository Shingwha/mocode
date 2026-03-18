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
- Session management - save and restore conversations
- RTK integration - reduce token usage by 60-90%
- Modular prompt system with caching
- Interactive command selection with keyboard navigation

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

## Commands

| Command | Aliases | Description |
|---------|---------|-------------|
| `/help` | `/h`, `/?` | Show commands (interactive menu) |
| `/model` | `/m` | Switch or manage models |
| `/provider` | `/p` | Switch or manage providers |
| `/session` | `/s` | Manage conversation sessions |
| `/clear` | `/c` | Clear history (auto-save session) |
| `/skills` | | List skills |
| `/rtk` | | Manage RTK (token optimizer) |
| `/exit` | `/q`, `quit` | Exit |

### Interactive Menus

Commands like `/model`, `/provider`, `/session` support interactive selection with keyboard navigation:
- Arrow keys to navigate
- Enter to select
- ESC to cancel

## Session Management

Sessions are automatically saved when clearing history and can be restored later:

```bash
/session          # Interactive session selection
/session list     # List all sessions
/session restore <id>  # Restore specific session
```

Sessions are stored per working directory in `~/.mocode/sessions/`.

## RTK Integration

[RTK (Rust Token Killer)](https://github.com/rtk-ai/rtk) reduces token usage by 60-90% through intelligent filtering, grouping, truncation, and deduplication of command outputs.

```bash
/rtk         # Check RTK status and stats
/rtk on      # Enable RTK wrapping
/rtk off     # Disable RTK wrapping
/rtk install # Auto-install on Windows
```

## SDK Usage

```python
from mocode import MocodeClient, EventType, PromptBuilder, StaticSection

# Basic usage
async def main():
    client = MocodeClient(config={
        "current": {"provider": "openai", "model": "gpt-4o"},
        "providers": {"openai": {"api_key": "sk-...", "base_url": "https://api.openai.com/v1"}}
    })

    client.on_event(EventType.TEXT_COMPLETE, lambda e: print(e.data))
    await client.chat("Hello!")

    # Interrupt ongoing operation
    client.interrupt()

    # Session management
    client.save_session()
    sessions = client.list_sessions()
    client.load_session(sessions[0].id)

    # Clear history with auto-save
    client.clear_history_with_save()

asyncio.run(main())
```

### Custom Prompt Builder

```python
# Build custom system prompts
builder = PromptBuilder()
builder.add(StaticSection("custom", 100, "Your custom instructions"))
client = MocodeClient(prompt_builder=builder)
```

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
├── sdk.py              # MocodeClient - SDK entry point
├── main.py             # Entry point (CLI or gateway mode)
├── paths.py            # Centralized path configuration
├── core/               # Business logic (independent of UI)
│   ├── agent.py        # AsyncAgent - LLM conversation loop
│   ├── config.py       # Multi-provider config
│   ├── events.py       # EventBus - event-driven communication
│   ├── interrupt.py    # InterruptToken - cancellation support
│   ├── permission.py   # PermissionMatcher, PermissionHandler
│   ├── session.py      # SessionManager - conversation persistence
│   └── prompt/         # Modular prompt system
│       ├── builder.py  # PromptBuilder with caching
│       ├── sections.py # Built-in prompt sections
│       └── templates.py
├── gateway/            # Multi-channel bot support
│   ├── base.py         # BaseChannel abstract class
│   ├── config.py       # GatewayConfig
│   ├── manager.py      # GatewayManager
│   └── telegram.py     # TelegramChannel
├── providers/          # LLM providers
│   └── openai.py       # AsyncOpenAIProvider
├── tools/              # Tool implementations
│   ├── base.py         # Tool class and ToolRegistry
│   ├── file_tools.py   # read, write, edit
│   ├── search_tools.py # glob, grep
│   ├── shell_tools.py  # bash
│   ├── bash_session.py # SimpleBashSession
│   ├── rtk_wrapper.py  # RTK integration
│   └── context.py      # ContextVar for tool config
├── skills/             # Skill system (pluggable extensions)
│   ├── manager.py      # SkillManager
│   ├── schema.py       # Skill dataclasses
│   └── tool.py         # skill tool implementation
└── cli/                # Terminal interface
    ├── app.py          # AsyncApp main entry
    ├── commands/       # Slash command system
    │   ├── builtin.py  # /help, /clear, /exit
    │   ├── model.py    # /model command
    │   ├── provider.py # /provider command
    │   ├── session.py  # /session command
    │   ├── rtk.py      # /rtk command
    │   └── skills_command.py
    └── ui/             # Layout, colors, widgets
        ├── colors.py   # ANSI color codes
        ├── components.py
        ├── interactive.py  # Wizard, ask() prompts
        ├── keyboard.py     # getch, ESC monitoring
        ├── layout.py       # Terminal layout
        ├── permission_handler.py
        └── widgets.py      # SelectMenu
```

### Key Patterns

1. **Event System**: `EventBus` decouples `AsyncAgent` from UI. Key events: `TEXT_STREAMING`, `TEXT_DELTA`, `TEXT_COMPLETE`, `TOOL_START`, `TOOL_COMPLETE`, `PERMISSION_ASK`, `INTERRUPTED`.

2. **Interrupt Mechanism**: `InterruptToken` provides thread-safe cancellation for AI responses. Used by CLI (ESC), Gateway (`/cancel`), and SDK (`interrupt()`).

3. **Tool Registry**: Tools registered via `@tool()` decorator. Params use `"type?"` syntax for optional parameters.

4. **Permission System**: `PermissionMatcher` checks tool permissions (allow/ask/deny). `PermissionHandler` abstracts user interaction.

5. **Session Management**: `SessionManager` stores conversations per working directory with auto-save on clear.

6. **Prompt Builder**: Modular prompt construction with `StaticSection` and `DynamicSection`. Supports caching and conditional rendering.

## Requirements

- Python >= 3.10
- OpenAI-compatible API

## License

MIT
