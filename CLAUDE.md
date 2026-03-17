# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

mocode is a CLI coding assistant powered by LLM (OpenAI-compatible APIs). It provides an interactive terminal interface with tool-calling capabilities for file operations, search, and shell execution. Can also be embedded as an SDK in other applications.

## Commands

```bash
# Install as a tool
uv tool install -e .

# Run the CLI
uv run mocode

# or use the tool directly after installation
mocode

# Run Gateway (multi-channel bot mode)
uv run mocode gateway

# or use mocode gateway directly after installation
mocode gateway

# Install dependencies
uv sync
```

## Architecture

mocode uses a layered architecture with event-driven communication. Core is independent of CLI and can be used as a library.

```
mocode/
├── sdk.py              # SDK entry point (MocodeClient)
├── main.py             # Entry point (CLI or gateway mode)
├── core/               # Business logic (independent of UI)
│   ├── agent.py        # AsyncAgent - LLM conversation loop
│   ├── config.py       # Multi-provider config (file or in-memory)
│   ├── events.py       # EventBus - instance-based for multi-tenant
│   ├── interrupt.py    # InterruptToken - cancel AI responses
│   ├── permission.py   # PermissionMatcher (allow/ask/deny)
│   ├── permission_handler.py  # PermissionHandler abstraction
│   └── prompts.py      # System prompts for LLM
├── gateway/            # Multi-channel bot support
│   ├── base.py         # BaseChannel abstract class
│   ├── config.py       # GatewayConfig, TelegramConfig
│   ├── manager.py      # GatewayManager - manages channels & sessions
│   └── telegram.py     # TelegramChannel implementation
├── providers/          # LLM providers
│   └── openai.py       # AsyncOpenAIProvider
├── tools/              # Tool implementations
│   ├── base.py         # Tool class and ToolRegistry
│   ├── file_tools.py   # read, write, edit
│   ├── search_tools.py # glob, grep
│   ├── shell_tools.py  # bash
│   └── bash_session.py # SimpleBashSession (stateful)
├── skills/             # Skill system (pluggable extensions)
│   ├── manager.py      # SkillManager
│   ├── schema.py       # Skill dataclasses
│   └── tool.py         # skill tool implementation
└── cli/                # Terminal interface
    ├── app.py          # AsyncApp main entry
    ├── commands/       # Slash command system
    └── ui/             # Layout, colors, widgets
```

### Key Patterns

1. **Event System**: `EventBus` instances decouple `AsyncAgent` from UI. Use `get_event_bus()` for default instance. Events: `TEXT_COMPLETE`, `TOOL_START`, `TOOL_COMPLETE`, `PERMISSION_ASK`, `INTERRUPTED`. Agent uses `self.event_bus.emit()`; UI subscribes via `event_bus.on(EventType.X, handler)`.

2. **Interrupt Mechanism**: `InterruptToken` provides thread-safe cancellation for AI responses. Used by CLI (ESC key), Gateway (`/cancel` command), and SDK (`interrupt()` API). Agent checks `token.is_interrupted` during API calls and tool execution.

3. **Tool Registry**: Tools registered via `@tool(name, description, params)` decorator. Schema auto-generated for OpenAI function calling. Params use `"type?"` syntax for optional parameters.

4. **Permission System**: `PermissionMatcher` checks tool permissions (allow/ask/deny). `PermissionHandler` abstracts user interaction - CLI uses Future-based prompt, SDK can use custom handlers, Gateway auto-approves all (set `permission_matcher=None`).

5. **Command Pattern**: Slash commands (`/help`, `/model`, `/provider`, `/skills`) via `@command` decorator and `CommandRegistry`.

6. **Skill System**: Skills discovered from `~/.mocode/skills/`. Each skill has `SKILL.md` with YAML frontmatter. Listed in system prompt; loaded on demand via `skill` tool.

7. **Gateway System**: `GatewayManager` manages multiple channels (Telegram, etc.) with per-user sessions. Each session gets isolated `MocodeClient` with its own `EventBus`. Channels implement `BaseChannel` interface.

8. **SDK Usage**: `MocodeClient` provides easy embedding. Supports in-memory config, custom EventBus instances, PermissionHandler, and InterruptToken.

### Data Flow

```
User Input → AsyncApp._main_loop() | GatewayManager._handle_message()
    │
    ├─ "/" prefix → CommandRegistry.execute() | _handle_command()
    │
    └─ otherwise → AsyncAgent.chat(user_input)
                       │
                       ├─ interrupt_token.reset() → Reset cancellation state
                       │
                       ├─ AsyncOpenAIProvider.call() → LLM API (interruptible)
                       │
                       └─ Tool calls → _run_tool_async()
                                          │
                                          ├─ PermissionMatcher.check()
                                          │    └─ ASK → emit PERMISSION_ASK
                                          │
                                          ├─ interrupt_token.is_interrupted → INTERRUPTED
                                          │
                                          └─ ToolRegistry.run() → emit TOOL_START/COMPLETE
```

## Configuration

Config stored at `~/.mocode/config.json`, or use `Config.from_dict(data)` for in-memory:

```json
{
  "current": { "provider": "openai", "model": "gpt-4o" },
  "providers": {
    "openai": { "name": "OpenAI", "base_url": "...", "api_key": "...", "models": [...] }
  },
  "permission": { "*": "ask", "bash": "allow" },
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

## Adding New Tools

1. Create `def my_tool(args: dict) -> str`
2. Register with `@tool("name", "description", {"param": "string", "optional?": "number?"})`
3. Call registration in `tools/__init__.py::register_all_tools()`

## Adding New Commands

1. Subclass `Command` with `name`, `description`, `execute(ctx: CommandContext) -> bool`
2. Decorate with `@command("/cmd", "/alias", description="...")`
3. Add to `cli/commands/__init__.py::BUILTIN_COMMANDS`

## Adding New Skills

Skills auto-discovered from `~/.mocode/skills/`. Create `SKILL.md`:

```markdown
---
name: my-skill
description: What this skill does
---

# Instructions

Detailed instructions for the LLM...
```

## SDK Usage

```python
from mocode import MocodeClient, EventType

async def main():
    client = MocodeClient(config={
        "current": {"provider": "openai", "model": "gpt-4o"},
        "providers": {"openai": {"api_key": "sk-...", "base_url": "..."}}
    })
    client.on_event(EventType.TEXT_COMPLETE, lambda e: print(e.data))
    client.on_event(EventType.INTERRUPTED, lambda e: print("Cancelled"))

    # Normal chat
    response = await client.chat("Hello!")

    # Interrupt ongoing operation (from another task/thread)
    client.interrupt()

    # Clear history
    client.clear_history()
```
