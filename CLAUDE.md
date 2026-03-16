# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

NanoCode is a CLI coding assistant powered by LLM (OpenAI-compatible APIs). It provides an interactive terminal interface with tool-calling capabilities for file operations, search, and shell execution.

## Commands

```bash
# Run the CLI
uv run nanocode

# Run as module
uv run python -m nanocode

# Install dependencies
uv sync
```

## Architecture

NanoCode uses a layered architecture with event-driven communication:

```
nanocode/
├── core/          # Business logic (independent of UI)
│   ├── agent.py       # AsyncAgent - LLM conversation loop with tool execution
│   ├── config.py      # Multi-provider configuration management
│   ├── events.py      # EventBus for decoupling Agent from UI
│   ├── permission.py  # Tool permission system (allow/ask/deny)
│   └── prompts.py     # System prompts for LLM
├── providers/     # LLM providers
│   └── openai.py      # AsyncOpenAIProvider using OpenAI SDK
├── tools/         # Tool implementations
│   ├── base.py        # Tool class and ToolRegistry
│   ├── file_tools.py  # read, write, edit
│   ├── search_tools.py # glob, grep
│   └── shell_tools.py # bash (persistent Git Bash session)
└── cli/           # Terminal interface
    ├── app.py         # AsyncApp main entry
    ├── commands/      # Slash command system
    └── ui/            # Layout, colors, widgets
```

### Key Patterns

1. **Event System**: `EventBus` decouples `AsyncAgent` from UI. Events include `TEXT_COMPLETE`, `TOOL_START`, `TOOL_COMPLETE`, `PERMISSION_ASK`.

2. **Tool Registry**: Tools are registered via `@tool` decorator or `ToolRegistry.register()`. Each tool has a schema auto-generated for OpenAI function calling.

3. **Permission System**: Tools can be configured with `allow`, `ask`, or `deny` actions. When `ask`, the UI prompts the user before execution.

4. **Command Pattern**: Slash commands (`/help`, `/model`, `/provider`, etc.) extend the CLI. Commands are registered via `CommandRegistry`.

### Data Flow

```
User Input → AsyncApp._main_loop()
    ↓
    ├── "/" prefix → CommandRegistry.execute()
    └── otherwise → AsyncAgent.chat()
                        ↓
                    AsyncOpenAIProvider.call() → LLM API
                        ↓
                    Tool calls → ToolRegistry.run() (with permission check)
                        ↓
                    Events emitted → UI updates via event handlers
```

## Configuration

Configuration is stored at `~/.nanocode/config.json`:

```json
{
  "current": { "provider": "openai", "model": "gpt-4o" },
  "providers": {
    "openai": {
      "name": "OpenAI",
      "base_url": "https://api.openai.com/v1",
      "api_key": "...",
      "models": ["gpt-4o", "gpt-4o-mini"]
    }
  },
  "permission": { "*": "ask", "bash": "allow" }
}
```

## Adding New Tools

1. Create a function that takes `args: dict` and returns `str`
2. Register with `@tool(name, description, params)` decorator
3. Call registration function in `tools/__init__.py::register_all_tools()`

## Adding New Commands

1. Subclass `Command` with `name`, `description`, and `execute(ctx: CommandContext) -> bool`
2. Register in `cli/commands/__init__.py::BUILTIN_COMMANDS`
