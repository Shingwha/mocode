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
│   ├── shell_tools.py # bash tool
│   └── bash_session.py # SimpleBashSession (stateful Git Bash wrapper)
├── skills/        # Skill system (pluggable extensions)
│   ├── manager.py     # SkillManager - discovers skills from ~/.claude/skills/
│   ├── schema.py      # Skill and SkillMetadata dataclasses
│   └── tool.py        # skill tool implementation
└── cli/           # Terminal interface
    ├── app.py         # AsyncApp main entry
    ├── commands/      # Slash command system
    └── ui/            # Layout, colors, widgets
```

### Key Patterns

1. **Event System**: `EventBus` (singleton) decouples `AsyncAgent` from UI. Events include `TEXT_COMPLETE`, `TOOL_START`, `TOOL_COMPLETE`, `PERMISSION_ASK`. UI components subscribe to events via `events.on(EventType.X, handler)`.

2. **Tool Registry**: Tools are registered via `@tool(name, description, params)` decorator. Each tool's schema is auto-generated for OpenAI function calling. Params use `"type?"` syntax for optional parameters.

3. **Permission System**: Tools can be configured with `allow`, `ask`, or `deny` actions. When `ask`, the UI prompts the user before execution. Configured in `config.json` under `permission: {"*": "ask", "bash": "allow"}`.

4. **Command Pattern**: Slash commands (`/help`, `/model`, `/provider`, `/skills`) extend the CLI. Commands are registered via `@command` decorator and `CommandRegistry`.

5. **Skill System**: Skills are pluggable extensions discovered from `~/.claude/skills/`. Each skill is a directory with `SKILL.md` containing YAML frontmatter. Skills are listed in the system prompt; the `skill` tool loads full instructions on demand.

6. **Bash Session**: `SimpleBashSession` maintains `cwd` and environment variables in Python, executing commands via Git Bash subprocess. Handles `cd` and `export` internally for state persistence.

### Data Flow

```
User Input → AsyncApp._main_loop()
    │
    ├─ "/" prefix → CommandRegistry.execute()
    │                  └─ Command.execute(ctx) → updates ctx.pending_message
    │
    └─ otherwise → AsyncAgent.chat(user_input)
                       │
                       ├─ AsyncOpenAIProvider.call() → LLM API (non-streaming)
                       │
                       └─ Tool calls → _run_tool_async()
                                          │
                                          ├─ PermissionMatcher.check()
                                          │    └─ ASK → emit PERMISSION_ASK → UI prompt
                                          │
                                          └─ ToolRegistry.run() → emit TOOL_START/COMPLETE
                                                                  → UI updates
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
  "permission": { "*": "ask", "bash": "allow" },
  "max_tokens": 8192
}
```

## Adding New Tools

1. Create a function `def my_tool(args: dict) -> str`
2. Register with `@tool("name", "description", {"param": "string", "optional?": "number?"})`
3. Call registration function in `tools/__init__.py::register_all_tools()`

## Adding New Commands

1. Subclass `Command` with `name`, `description`, and `execute(ctx: CommandContext) -> bool`
2. Decorate with `@command("/cmd", "/alias", description="...")`
3. Add to `cli/commands/__init__.py::BUILTIN_COMMANDS`

## Adding New Skills

Skills are discovered automatically from `~/.claude/skills/`. Create a directory with `SKILL.md`:

```markdown
---
name: my-skill
description: What this skill does
---

# Instructions

Detailed instructions for the LLM to follow when this skill is loaded...
```
