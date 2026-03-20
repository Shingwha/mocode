# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

mocode is a CLI coding assistant powered by LLM (OpenAI-compatible APIs). It provides an interactive terminal interface with tool-calling capabilities for file operations, search, and shell execution. Can also be embedded as an SDK in other applications.

Requires Python >= 3.10.

## Code Style

Do not use emojis in code or commit messages. Keep code clean and simple.

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

### Data Flow

```
User Input -> MocodeClient.chat()
    |
    +- MocodeCore.chat() -> AgentFacade.chat() -> AsyncAgent.chat()
    |       |
    |       +- AsyncOpenAIProvider.call() -> LLM API
    |       |
    |       +- Tool calls -> PermissionMatcher.check() -> ToolRegistry.run()
    |                               |                        |
    |                               +- ASK -> emit PERMISSION_ASK
    |                               |
    |                               +- interrupt check
    |
    +- Events: TEXT_STREAMING, TEXT_DELTA, TOOL_START, TOOL_COMPLETE
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
  "plugins": {
    "rtk": "enable"
  },
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
2. Register with `@tool("name", "description", {"param": "string", "optional?": "number?"})` - use `?` suffix for optional params
3. Tools are registered in `ToolRegistry` (class-level registry) and called in `tools/__init__.py::register_all_tools()`

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

## Adding New Plugins

1. Create plugin directory in `~/.mocode/plugins/my-plugin/`
2. Create `plugin.py` with `Plugin` subclass:

```python
from mocode.plugins import Plugin, PluginMetadata, Hook, HookContext, HookPoint, hook

@hook(HookPoint.TOOL_AFTER_RUN, name="my-hook", priority=50)
def my_hook(ctx: HookContext) -> HookContext:
    print(f"Tool executed: {ctx.data.get('name')}")
    return ctx

class MyPlugin(Plugin):
    def __init__(self):
        self.metadata = PluginMetadata(
            name="my-plugin",
            version="1.0.0",
            description="A sample plugin",
        )

    def on_load(self) -> None:
        pass

    def on_enable(self) -> None:
        pass

    def on_disable(self) -> None:
        pass

    def on_unload(self) -> None:
        pass

    def get_hooks(self) -> list[Hook]:
        return [my_hook]

plugin_class = MyPlugin
```

## SDK Usage

```python
from mocode import MocodeClient, EventType, PromptBuilder, StaticSection

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

    # Session management
    client.save_session()
    sessions = client.list_sessions()
    client.load_session(sessions[0].id)
    client.clear_history_with_save()

    # Plugin management
    plugins = client.list_plugins()
    client.enable_plugin("my-plugin")
    client.disable_plugin("my-plugin")
    info = client.get_plugin_info("my-plugin")

    # Clear history
    client.clear_history()

    # Custom prompt builder
    builder = PromptBuilder()
    builder.add(StaticSection("custom", 100, "Your custom instructions"))
    client = MocodeClient(prompt_builder=builder)
```
