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
- [Plugin System](docs/plugins.md)

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
| `/plugin` | | Manage plugins |
| `/rtk` | | Manage RTK (token optimizer) |
| `/exit` | `/q`, `/quit` | Exit |

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

    # Plugin management
    plugins = client.list_plugins()
    client.enable_plugin("my-plugin")
    client.disable_plugin("my-plugin")
    info = client.get_plugin_info("my-plugin")

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

mocode uses a layered architecture with event-driven communication. Core is independent of CLI and can be used as a library.

```
mocode/
├── sdk.py              # MocodeClient - thin facade over MocodeCore
├── main.py             # Entry point (CLI or gateway mode)
├── paths.py            # Centralized path configuration
├── core/               # Business logic (independent of UI)
│   ├── orchestrator.py      # MocodeCore - central coordinator
│   ├── agent_facade.py      # AgentFacade - high-level agent ops
│   ├── session_coordinator.py # SessionCoordinator
│   ├── plugin_coordinator.py  # PluginCoordinator
│   ├── agent.py             # AsyncAgent - LLM conversation loop
│   ├── config.py            # Multi-provider config
│   ├── events.py            # EventBus - instance-based
│   ├── interrupt.py         # InterruptToken - cancel responses
│   ├── permission.py        # PermissionMatcher, PermissionHandler
│   ├── session.py           # SessionManager - persistence
│   └── prompt/              # Modular prompt system
├── plugins/            # Plugin/hook system
│   ├── base.py         # Plugin, Hook, HookPoint, PluginState
│   ├── manager.py      # PluginManager - lifecycle
│   ├── registry.py     # HookRegistry
│   ├── loader.py       # PluginLoader - discovery
│   └── builtin/rtk/    # RTK plugin (token optimizer)
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
│   └── bash.py         # BashSession, bash tool
├── skills/             # Skill system
│   ├── manager.py      # SkillManager
│   ├── schema.py       # Skill dataclasses
│   └── tool.py         # skill tool implementation
└── cli/                # Terminal interface
    ├── app.py          # AsyncApp main entry
    ├── commands/       # Slash commands
    │   ├── base.py     # Command base class
    │   ├── builtin.py  # /help, /clear, /exit
    │   ├── model.py    # /model
    │   ├── provider.py # /provider
    │   ├── session.py  # /session
    │   ├── plugin.py   # /plugin
    │   └── skills.py   # /skills
    └── ui/             # Layout, colors, widgets
        ├── colors.py   # ANSI color codes
        ├── layout.py   # Terminal layout
        ├── prompt.py   # SelectMenu, ask, Wizard
        ├── menu.py     # MenuItem, MenuAction
        └── permission.py # CLIPermissionHandler
```

### Key Patterns

1. **Layered Architecture**: `MocodeClient` (SDK) -> `MocodeCore` (orchestrator) -> Facades/Coordinators -> `AsyncAgent`. SDK is a thin facade; `MocodeCore` coordinates all components.

2. **Event System**: `EventBus` decouples `AsyncAgent` from UI. Key events: `TEXT_STREAMING`, `TEXT_DELTA`, `TEXT_COMPLETE`, `TOOL_START`, `TOOL_COMPLETE`, `PERMISSION_ASK`, `INTERRUPTED`.

3. **Interrupt Mechanism**: `InterruptToken` provides thread-safe cancellation. Used by CLI (ESC), Gateway (`/cancel`), SDK (`interrupt()`).

4. **Tool Registry**: Tools registered via `@tool(name, description, params)` decorator. Params use `"type?"` suffix for optional.

5. **Permission System**: `PermissionMatcher` checks permissions (allow/ask/deny). `PermissionHandler` abstracts interaction - CLI uses `CLIPermissionHandler`, Gateway auto-approves.

6. **Plugin System**: `PluginManager` manages plugins, hooks intercept at `HookPoint`s (`TOOL_BEFORE_RUN`, `TOOL_AFTER_RUN`, etc.). RTK is a built-in plugin. Plugins discovered from `~/.mocode/plugins/` and `<project>/.mocode/plugins/`.

7. **Command Pattern**: Slash commands via `@command` decorator and `CommandRegistry`. Commands: `/help`, `/model`, `/provider`, `/session`, `/plugin`, `/skills`, `/rtk`, `/clear`, `/exit`.

8. **Skill System**: Skills from `~/.mocode/skills/`. Each has `SKILL.md` with YAML frontmatter. Listed in system prompt; loaded on demand via `skill` tool.

## Requirements

- Python >= 3.10
- OpenAI-compatible API

## License

MIT
