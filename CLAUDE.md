# mocode 项目指南

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
mocode

# Install dependencies
uv sync
```

## Architecture

mocode uses a layered architecture with event-driven communication. Core is independent of CLI and can be used as a library.

```
mocode/
├── sdk.py              # MocodeClient - SDK entry point (thin facade)
├── main.py             # Entry point (CLI mode)
├── paths.py            # Centralized path configuration
├── core/               # Business logic (independent of UI)
│   ├── orchestrator.py      # MocodeCore - central coordinator
│   ├── agent.py             # AsyncAgent - LLM conversation loop
│   ├── config.py            # Multi-provider config + Mode system
│   ├── config_manager.py    # ConfigManager - persistence & callbacks
│   ├── events.py            # EventBus - instance-based
│   ├── interrupt.py         # InterruptToken - cancel responses
│   ├── permission.py        # PermissionMatcher, PermissionHandler
│   ├── session.py           # SessionManager - persistence
│   └── prompt/              # Modular prompt system
├── plugins/            # Plugin/hook system
│   ├── base.py         # Plugin, Hook, HookPoint, PluginState, HookContext
│   ├── manager.py      # PluginManager - lifecycle management
│   ├── registry.py     # HookRegistry, PluginRegistry
│   ├── loader.py       # PluginLoader - discovery
│   ├── coordinator.py  # PluginCoordinator - high-level coordination
│   ├── context.py      # PluginContext
│   ├── decorators.py   # @hook, @async_hook, HookBuilder
│   ├── venv_manager.py # PluginVenvManager - isolated environments
│   └── builtin/rtk/    # RTK plugin (token optimizer)
├── providers/          # LLM providers
│   └── openai.py       # AsyncOpenAIProvider
├── tools/              # Tool implementations
│   ├── base.py         # Tool class and ToolRegistry
│   ├── context.py      # ToolContext for per-request context
│   ├── file_tools.py   # read, write, edit
│   ├── search_tools.py # glob, grep
│   ├── utils.py        # truncate_result
│   └── bash.py         # BashSession, bash tool
├── skills/             # Skill system
│   ├── manager.py      # SkillManager (singleton)
│   ├── schema.py       # Skill, SkillMetadata dataclasses
│   ├── tool.py         # skill tool implementation
│   └── builtin/        # Built-in skills
└── cli/                # Terminal interface
    ├── app.py          # CLIApp main entry
    ├── commands/       # Slash commands
    │   ├── base.py     # Command, CommandRegistry
    │   ├── builtin.py  # /help, /clear, /exit
    │   ├── provider.py # /provider (model selection)
    │   ├── session.py  # /session
    │   ├── plugin.py   # /plugin
    │   ├── skills.py   # /skills
    │   ├── utils.py    # parse_selection_arg
    │   └── executor.py # CommandExecutor
    ├── monitor/        # Input monitoring
    │   └── esc.py      # ESC key listener
    ├── events/         # Event handling
    │   └── handler.py  # CLIEventHandler
    └── ui/             # Layout, colors, widgets
        ├── base.py           # Component base class
        ├── layout.py         # Terminal layout
        ├── prompt.py         # SelectMenu, ask, MenuItem, MenuAction
        ├── permission.py     # CLIPermissionHandler
        └── components/       # UI components
            ├── input.py      # Input component
            ├── message.py    # Message display
            ├── select.py     # Select menu
            └── animated.py   # Animated components
```

### Key Patterns

1. **Layered Architecture**: `MocodeClient` (SDK) -> `MocodeCore` (orchestrator) -> `AsyncAgent`. SDK is a thin facade; `MocodeCore` coordinates all components and handles persistence.

2. **Event System**: `EventBus` decouples `AsyncAgent` from UI. Key events:
   - `TEXT_STREAMING`, `TEXT_DELTA`, `TEXT_COMPLETE` - text streaming
   - `TOOL_START`, `TOOL_COMPLETE`, `TOOL_PROGRESS` - tool execution
   - `MESSAGE_ADDED` - message tracking
   - `MODEL_CHANGED` - provider/model switches
   - `ERROR`, `STATUS_UPDATE` - status
   - `PERMISSION_ASK` - permission prompts
   - `INTERRUPTED` - cancellation
   - `AGENT_IDLE` - agent ready for more work
   - `COMPONENT_STATE_CHANGE`, `COMPONENT_COMPLETE` - UI lifecycle

3. **Interrupt Mechanism**: `InterruptToken` provides thread-safe cancellation. Used by CLI (ESC key), SDK (`interrupt()` method).

4. **Tool Registry**: Tools registered via `@tool(name, description, params)` decorator.
   - Params: `"type?"` suffix for optional parameters
   - Full format: `{"type": "string", "description": "...", "default": ...}`
   - Use `truncate_result()` for large outputs to prevent context overflow

5. **Permission System**: `PermissionMatcher` checks permissions based on `PermissionConfig`.
   - Actions: `ALLOW`, `ASK`, `DENY`
   - Supports flat format (`{"*": "ask", "bash": "allow"}`)
   - Supports nested format (`{"bash": {"*": "ask", "rm *": "deny"}}`)
   - `PermissionHandler` abstracts interaction - CLI uses `CLIPermissionHandler`

6. **Plugin System**: `PluginManager` manages plugin lifecycle. Plugins can:
   - Register hooks at `HookPoint`s (TOOL_BEFORE_RUN, TOOL_AFTER_RUN, AGENT_CHAT_START/END, etc.)
   - Provide custom tools and commands
   - Replace core tools via `replaces_tools` metadata
   - Run in isolated virtual environments with automatic dependency installation
   - Discovered from `~/.mocode/plugins/` and `<project>/.mocode/plugins/`

7. **Command Pattern**: Slash commands via `@command` decorator and `CommandRegistry`.
   - Built-in commands: `/help`, `/provider` (model selection), `/session`, `/plugin`, `/skills`, `/clear`, `/exit`
   - `/rtk` command provided by RTK plugin

8. **Skill System**: Skills auto-discovered from multiple locations:
   - Project-level: `.mocode/skills/` (highest priority)
   - Global: `~/.mocode/skills/`
   - Builtin: `mocode/skills/builtin/` (lowest priority)
   - Each skill has `SKILL.md` with YAML frontmatter and instructions
   - Skills are listed in system prompt and loaded on demand via `skill` tool

9. **Mode System**: Supports operational modes with different permission behaviors.
   - `normal`: Standard permission checks (ask/allow/deny)
   - `yolo`: Auto-approves non-dangerous commands, only asks for dangerous ones
   - Configured in `config.json` or via `set_mode()` at runtime

### Data Flow

```
User Input -> MocodeClient.chat()
    |
    +- MocodeCore.chat()
    |       |
    |       +- AsyncAgent.chat()
    |       |       |
    |       |       +- Build system prompt (SkillManager + PromptBuilder)
    |       |       |
    |       |       +- AsyncOpenAIProvider.call() -> LLM API
    |       |       |
    |       |       +- Agent loop:
    |       |       |   - Check interrupt
    |       |       |   - Receive response with tool_calls
    |       |       |   - Emit TEXT_COMPLETE
    |       |       |   - For each tool_call (sequential):
    |       |       |       - Check interrupt
    |       |       |       - Check mode permissions (yolo auto-approve)
    |       |       |       - PermissionMatcher.check()
    |       |       |       - If ASK: PermissionHandler.ask_permission()
    |       |       |       - Trigger TOOL_BEFORE_RUN hook (can skip execution)
    |       |       |       - Emit TOOL_START
    |       |       |       - ToolRegistry.run() (with interrupt check)
    |       |       |       - Apply truncate_result (tool_result_limit)
    |       |       |       - Trigger TOOL_AFTER_RUN hook (can modify result)
    |       |       |       - Emit TOOL_COMPLETE
    |       |       |       - Append tool result to messages
    |       |       |
    |       |       +- Append assistant message
    |       |       +- Loop if tool calls exist, else break
    |       |       +- Emit AGENT_CHAT_END hook
    |       |
    |       +- Mark session dirty
    |
    +- Events emitted throughout
```

## Configuration

Config stored at `~/.mocode/config.json`, or use `Config.from_dict(data)` for in-memory:

```json
{
  "current": { "provider": "openai", "model": "gpt-4o" },
  "providers": {
    "openai": {
      "name": "OpenAI",
      "base_url": "https://api.openai.com/v1",
      "api_key": "...",
      "models": ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo"]
    },
    "longcat": {
      "name": "LongCat",
      "base_url": "https://api.longcat.chat/openai",
      "api_key": "",
      "models": ["LongCat-Flash-Chat", "LongCat-Flash-Thinking"]
    }
  },
  "permission": {
    "*": "ask",
    "bash": "allow"
  },
  "max_tokens": 8192,
  "tool_result_limit": 25000,
  "plugins": {
    "rtk": "enable"
  },
  "modes": {
    "normal": { "auto_approve": false },
    "yolo": {
      "auto_approve": true,
      "dangerous_patterns": [
        "rm ", "rmdir ", "dd ", "mv ", "del ",
        "chmod ", "chown ", "sudo ", "format ", "mkfs "
      ]
    }
  },
  "current_mode": "normal"
}
```

**Configuration fields**:
- `current.provider/model`: Current provider key and model name
- `providers`: Map of provider configs (key -> ProviderConfig)
- `permission`: Permission rules (flat or nested)
- `max_tokens`: Context window size
- `tool_result_limit`: Max characters for tool results (0 = unlimited, default: 25000)
- `plugins`: Plugin enable/disable status
- `modes`: Mode configurations (auto-approve rules)
- `current_mode`: Currently active mode

**Mode System**:
- `normal` mode: Standard permission checks (ask/allow/deny based on rules)
- `yolo` mode: Auto-approves all tools except those with dangerous patterns
- Dangerous patterns checked for `bash` tool command prefixes
- Switch modes at runtime via `client.set_mode("yolo")` or `/mode yolo`

## Adding New Tools

1. Define function: `def my_tool(args: dict) -> str`
2. Register with `@tool` decorator:

```python
from mocode.tools import tool

@tool(
    name="my_tool",
    description="What this tool does",
    params={
        "arg1": "string",           # Required (no ?)
        "arg2": "number?",          # Optional (shorthand)
        "arg3": {                   # Full format
            "type": "string",
            "description": "Parameter description",
            "default": "default_value"
        }
    }
)
def my_tool(args: dict) -> str:
    # args contains validated values with defaults filled in
    arg1 = args["arg1"]
    arg2 = args.get("arg2")
    arg3 = args["arg3"]
    return f"Result: {arg1}"
```

3. Tools auto-register via decorator to `ToolRegistry`
4. Use `truncate_result(result, max_size)` if tool may return large output (automatically applied if `tool_result_limit` > 0)

## Adding New Commands

1. Subclass `Command` from `mocode.cli.commands.base`
2. Implement `execute(ctx: CommandContext) -> bool`
3. Decorate with `@command("/cmd", "/alias1", "/alias2", description="...")`:

```python
from mocode.cli.commands.base import Command, CommandContext, command

@command("/mycmd", "/mc", description="My custom command")
class MyCommand(Command):
    def execute(self, ctx: CommandContext) -> bool:
        # Access client: ctx.client
        # Access layout: ctx.layout
        # Parse args: ctx.args
        # Set pending message: ctx.pending_message = "..."
        ctx.layout.add_message("Command executed")
        return True  # continue, False to exit
```

4. Add to `cli/commands/__init__.py::BUILTIN_COMMANDS`

## Adding New Skills

Skills are modular instructions that can be loaded on demand by the LLM.

**Skill Structure**:
```
~/.mocode/skills/my-skill/
├── SKILL.md           # Required: metadata and instructions
├── script.py          # Optional: Python implementation
└── requirements.txt   # Optional: dependencies (installed in skill's venv)
```

**SKILL.md format**:
```markdown
---
name: my-skill
description: Brief description of what this skill does
version: 1.0.0
author: Your Name
dependencies:
  - requests>=2.0.0
  - beautifulsoup4
---

# Instructions

Detailed instructions for the LLM on how to use this skill.

Include examples, best practices, and any constraints.

The LLM will see this content when the skill is loaded via the `skill` tool.
```

**Skill Discovery Priority** (higher overrides lower):
1. Project-level: `<workdir>/.mocode/skills/`
2. Global: `~/.mocode/skills/`
3. Builtin: `mocode/skills/builtin/`

**How Skills Work**:
- `SkillManager` (singleton) discovers all skills at startup
- Skills metadata included in system prompt
- LLM decides when to load a skill based on user request
- `skill` tool loads the skill's `SKILL.md` content
- LLM follows the instructions in that content
- Can include executable code (`script.py`) for complex operations

## Adding New Plugins

Plugins extend mocode with hooks, tools, commands, and prompt sections. They can be enabled/disabled at runtime and run in isolated environments.

### Plugin Structure

```
~/.mocode/plugins/my-plugin/
├── plugin.py           # Required: Plugin class definition
├── plugin.yaml         # Optional: metadata (alternative to class)
├── requirements.txt    # Optional: Python dependencies
└── README.md           # Optional: documentation
```

### Basic Plugin Example

```python
from mocode.plugins import (
    Plugin, PluginMetadata, Hook, HookContext, HookPoint, hook
)

# Define a hook (optional)
@hook(HookPoint.TOOL_AFTER_RUN, name="log-tool-execution", priority=50)
def log_tool_execution(ctx: HookContext) -> HookContext:
    """Log when tools are executed"""
    tool_name = ctx.data.get("name")
    result = ctx.data.get("result", "")
    print(f"[Plugin] Tool '{tool_name}' executed, result length: {len(result)}")
    return ctx

# Define plugin class
class MyPlugin(Plugin):
    def __init__(self):
        self.metadata = PluginMetadata(
            name="my-plugin",
            version="1.0.0",
            description="A sample plugin",
            author="Your Name",
            dependencies=[],  # e.g., ["requests>=2.0.0"]
            permissions=[],  # e.g., ["network", "file_write"]
            replaces_tools=[]  # e.g., ["bash"] to override bash tool
        )

    async def on_load(self) -> None:
        """Called once when plugin is loaded into memory"""
        pass

    async def on_enable(self) -> None:
        """Called when plugin is enabled (after dependencies installed)"""
        pass

    async def on_disable(self) -> None:
        """Called when plugin is disabled"""
        pass

    async def on_unload(self) -> None:
        """Called when plugin is unloaded from memory"""
        pass

    def get_hooks(self) -> list[Hook]:
        """Return list of hook functions"""
        return [log_tool_execution]

    def get_tools(self) -> list:
        """Return list of custom tools (see Tool Development)"""
        return []

    def get_commands(self) -> list:
        """Return list of custom commands (see Command Development)"""
        return []

    def get_prompt_sections(self) -> list:
        """Return list of prompt sections to inject into system prompt"""
        return []

# Required: Export plugin class
plugin_class = MyPlugin
```

### Plugin Features

**Hooks**: Intercept and modify behavior at specific points:
- `PLUGIN_LOAD`, `PLUGIN_ENABLE`, `PLUGIN_DISABLE`, `PLUGIN_UNLOAD`
- `AGENT_CHAT_START`, `AGENT_CHAT_END`
- `TOOL_BEFORE_RUN`, `TOOL_AFTER_RUN`
- `MESSAGE_BEFORE_SEND`, `MESSAGE_AFTER_RECEIVE`
- `PROMPT_BUILD_START`, `PROMPT_BUILD_END`
- `UI_COMPONENT_CREATED`, `UI_COMPONENT_RENDERED`, `UI_COMPONENT_COMPLETED`, `UI_COMPONENT_CLEARED`

**Hook Context** (`HookContext`):
```python
def my_hook(ctx: HookContext) -> HookContext:
    hook_point = ctx.hook_point      # HookPoint enum
    data = ctx.data                  # Original context data (dict)
    ctx.set_result("modified")       # Override result (modifies behavior)
    ctx.stop_propagation()          # Stop calling other hooks
    ctx.set_error(ValueError("...")) # Halt with error
    return ctx
```

**Tool Replacement**:
- Declare in `PluginMetadata.replaces_tools = ["bash"]`
- When plugin enables, its version of `bash` tool overrides core tool
- When plugin disables, original tool is restored
- Supports multiple plugins stacking replacements (LIFO order)

**Isolated Virtual Environments**:
- Non-builtin plugins get their own `venv/` directory
- `dependencies` in metadata automatically installed on enable
- Uses `PluginVenvManager` for lifecycle
- Builtin plugins skip venv (share core environment)

**Plugin Context Access**:
After `on_enable()`, plugins receive `PluginContext`:
```python
def on_enable(self):
    ctx = self.context
    ctx.event_bus.emit(EventType.STATUS_UPDATE, {"msg": "Plugin ready"})
    ctx.inject_message("external message")  # blocks until response
    ctx.queue_message("background message")  # non-blocking
    messages = ctx.get_messages()  # copy of current conversation
    workdir = ctx.workdir
```

**Discovery Locations**:
- User global: `~/.mocode/plugins/`
- Project-specific: `<workdir>/.mocode/plugins/`
- Builtin: `mocode/plugins/builtin/`

**Plugin Lifecycle**:
1. `discover()`: Find all plugin.py files
2. `load()`: Import module, create instance, call `on_load()`
3. `enable()`: Install dependencies, call `on_enable()`, register hooks/tools/commands
4. `disable()`: Call `on_disable()`, unregister hooks/tools/commands
5. `unload()`: Call `on_unload()`, remove from memory

### Async vs Sync Hooks

By default, hooks run synchronously. Use `@async_hook` decorator for async hooks:
```python
from mocode.plugins import async_hook

@async_hook(HookPoint.TOOL_BEFORE_RUN)
async def my_async_hook(ctx: HookContext) -> HookContext:
    await asyncio.sleep(1)
    return ctx
```

## SDK Usage

```python
from mocode import MocodeClient, EventType, PromptBuilder, StaticSection
import asyncio

async def main():
    # In-memory configuration (no filesystem)
    config = {
        "current": {"provider": "openai", "model": "gpt-4o"},
        "providers": {
            "openai": {
                "name": "OpenAI",
                "base_url": "https://api.openai.com/v1",
                "api_key": "sk-...",
                "models": ["gpt-4o", "gpt-4o-mini"]
            }
        },
        "permission": {"*": "ask"},
        "max_tokens": 8192,
        "tool_result_limit": 25000,
        "plugins": {}
    }

    # Create client
    client = MocodeClient(
        config=config,
        persistence=False,  # Don't write to disk
        auto_register_tools=True,
        auto_discover_plugins=True
    )

    # Subscribe to events
    def on_text_complete(event):
        print(f"Response: {event.data['content']}")

    client.on_event(EventType.TEXT_COMPLETE, on_text_complete)
    client.on_event(EventType.INTERRUPTED, lambda e: print("Cancelled"))

    # Chat
    response = await client.chat("Hello!")
    print(f"Final: {response}")

    # Interrupt (from another thread/task)
    # client.interrupt()

    # Session management
    client.save_session()
    sessions = client.list_sessions()
    if sessions:
        client.load_session(sessions[0].id)
    client.clear_history_with_save()

    # Plugin management
    plugins = client.list_plugins()
    await client.enable_plugin("my-plugin")
    await client.disable_plugin("my-plugin")
    info = client.get_plugin_info("my-plugin")

    # Clear history
    client.clear_history()

    # Custom prompt builder
    builder = PromptBuilder()
    builder.add(StaticSection("custom", 100, "Your custom instructions"))
    client.rebuild_system_prompt(builder.build())

    # Mode switching
    client.set_mode("yolo")  # Enable yolo mode (auto-approve non-dangerous)

    # Provider/model management
    client.set_model("gpt-4o-mini")
    client.add_provider("custom", "Custom", "https://...", "sk-...", ["model1"])
    client.remove_provider("old-provider")

if __name__ == "__main__":
    asyncio.run(main())
```

**SDK Properties**:
- `client.config` - Config instance
- `client.agent` - AsyncAgent instance
- `client.event_bus` - EventBus instance
- `client.session_manager` - SessionManager instance
- `client.prompt_builder` - PromptBuilder instance
- `client.workdir` - Working directory
- `client.current_model`, `client.current_provider`
- `client.models`, `client.providers`

**SDK Methods**:
- `chat(message)` - Send message, get response
- `interrupt()` - Cancel current operation
- `clear_history()` - Clear conversation
- `save_session()` / `load_session(id)` / `list_sessions()` - Session ops
- `set_model(model)` / `set_provider(provider, model)` - Switch models
- `add_provider(...)`, `remove_provider(...)`, `update_provider(...)` - Provider mgmt
- `rebuild_system_prompt(context)` / `update_system_prompt(prompt)` - Prompt mgmt
- `list_plugins()`, `enable_plugin(name)`, `disable_plugin(name)` - Plugin mgmt
- `discover_plugins()` - Re-scan plugin directories
- `on_event(type, handler)` / `off_event(type, handler)` - Event subscription
- `set_mode(mode_name)` - Switch operational mode

## SDK Properties & Methods Quick Reference

| Property/Method | Type | Description |
|----------------|------|-------------|
| `config` | `Config` | Configuration object |
| `agent` | `AsyncAgent` | Agent instance |
| `event_bus` | `EventBus` | Event bus |
| `session_manager` | `SessionManager` | Session persistence |
| `prompt_builder` | `PromptBuilder` | System prompt builder |
| `workdir` | `str` | Working directory |
| `current_model` | `str` | Current model name |
| `current_provider` | `str` | Current provider key |
| `models` | `list[str]` | Models for current provider |
| `providers` | `dict` | All providers |
| `persistence_enabled` | `bool` | Whether config persistence is on |

**Chat & History**:
- `async chat(message: str) -> str`
- `interrupt()`
- `clear_history()`
- `clear_history_with_save() -> Session | None`

**Provider/Model**:
- `set_model(model: str, provider: str | None = None)`
- `set_provider(provider_key: str, model: str | None = None)`
- `add_provider(key, name, base_url, api_key, models, set_current)`
- `remove_provider(key) -> str | None`
- `update_provider(key, name, base_url, api_key)`
- `add_model(model, provider, set_current)`
- `remove_model(model, provider) -> str | None`

**Prompt**:
- `rebuild_system_prompt(context: dict | None = None, clear_history: bool = False)`
- `update_system_prompt(prompt: str, clear_history: bool = False)`

**Sessions**:
- `save_session() -> Session`
- `load_session(session_id: str) -> Session | None`
- `list_sessions() -> list[Session]`
- `delete_session(session_id: str) -> bool`

**Plugins**:
- `list_plugins() -> list[PluginInfo]`
- `async enable_plugin(name: str) -> bool`
- `async disable_plugin(name: str) -> bool`
- `get_plugin_info(name: str) -> PluginInfo | None`
- `discover_plugins() -> list[PluginInfo]`

**Events**:
- `on_event(event_type: EventType, handler: Callable)`
- `off_event(event_type: EventType, handler: Callable)`

**Config Persistence**:
- `save_config()` - Manually persist if persistence enabled

**Mode**:
- `set_mode(mode_name: str) -> bool`

## Event System

`EventBus` provides decoupled communication. Events emitted throughout the system.

### Event Types

```python
from mocode.core.events import EventType

# Text streaming
EventType.TEXT_STREAMING    # {"content": "..."} - start of streaming
EventType.TEXT_DELTA        # {"delta": "..."} - incremental chunk
EventType.TEXT_COMPLETE     # {"content": "..."} - full response

# Tool execution
EventType.TOOL_START        # {"name": "...", "args": {...}, "conversation_id": "..."}
EventType.TOOL_PROGRESS     # {"name": "...", "progress": ...}
EventType.TOOL_COMPLETE     # {"name": "...", "result": "...", "conversation_id": "..."}

# Messages
EventType.MESSAGE_ADDED     # {"role": "...", "content": "...", "conversation_id": "..."}

# Model/Provider
EventType.MODEL_CHANGED     # {"provider": "...", "model": "..."}

# Permissions
EventType.PERMISSION_ASK   # {"tool": "...", "args": {...}}

# Control flow
EventType.INTERRUPTED      # {"reason": "..."} - interrupted or denied
EventType.AGENT_IDLE       # None - agent ready for more work

# UI Components
EventType.COMPONENT_STATE_CHANGE   # {"component": "...", "state": {...}}
EventType.COMPONENT_COMPLETE      # {"component": "...", "result": "..."}

# General
EventType.ERROR            # {"error": "..."}
EventType.STATUS_UPDATE    # {"msg": "..."}
```

### Subscribing to Events

```python
from mocode.core.events import EventType, Event

def handler(event: Event):
    print(f"Event: {event.type}, data: {event.data}")

client.on_event(EventType.TEXT_COMPLETE, handler)

# Unsubscribe
client.off_event(EventType.TEXT_COMPLETE, handler)
```

## Permission System

Controls whether tools can be executed. Supports both static rules and interactive prompts.

### Permission Config

```json
{
  "permission": {
    "*": "ask",                    // Default for all tools
    "read": "allow",               // Flat: always allow read
    "write": "deny",               // Flat: always deny write
    "bash": {                      // Nested: per-command rules
      "*": "ask",
      "ls *": "allow",
      "rm *": "deny",
      "**/important.txt": "allow"  // Glob patterns for paths
    }
  }
}
```

**Rule Matching Order**:
1. Exact tool name match (if nested config exists)
2. Wildcard `*` (global default)
3. If nested config: match against command/path with glob patterns
   - Prefix: `"rm *"` matches `"rm file.txt"`
   - Glob: `"**/*.py"` matches any `.py` file recursively
   - Exact: `"ls /home"` matches exactly

**Builtin PermissionHandlers**:
- `DefaultPermissionHandler` - auto-allow (use for headless)
- `DenyAllPermissionHandler` - auto-deny
- `CLIPermissionHandler` - interactive prompts in CLI

### Custom PermissionHandler

```python
from mocode.core.permission import PermissionHandler

class MyPermissionHandler(PermissionHandler):
    async def ask_permission(self, tool_name: str, tool_args: dict) -> str:
        # Return "allow", "deny", or custom string (used as tool result)
        if tool_name == "bash" and "rm" in tool_args.get("command", ""):
            return "deny"
        return "allow"
```

## Mode System

Provides different operational modes with varying permission behaviors.

### Available Modes

**normal** (default):
- Standard permission checking
- Respects `permission` config rules
- Interactive prompts for `ASK` actions

**yolo**:
- Auto-approves all tools except dangerous commands
- Dangerous command detection based on `dangerous_patterns`
- Currently checks `bash` tool command prefixes
- Useful for quick workflows with safety guardrails

### Mode Configuration

```json
{
  "modes": {
    "normal": { "auto_approve": false },
    "yolo": {
      "auto_approve": true,
      "dangerous_patterns": [
        "rm ", "rmdir ", "dd ", "mv ", "del ",
        "chmod ", "chown ", "sudo ", "format ", "mkfs ", "fdisk "
      ]
    }
  },
  "current_mode": "normal"
}
```

### Using Modes

```python
# Check current mode
print(client.config.current_mode)  # "normal"

# Switch mode
client.set_mode("yolo")
# or
client.config.set_mode("yolo")

# In CLI: /mode yolo or /mode normal
```

**Dangerous Command Detection** (yolo mode):
- Only applies to `bash` tool
- Checks if command starts with any pattern in `dangerous_patterns`
- Uses exact prefix matching (with trailing space/tab)
- Examples:
  - `"rm "` matches `"rm file.txt"` but not `"rmv file.txt"`
  - Patterns include both space and `\t` to catch whitespace variations

## Paths Reference

All paths centralized in `mocode/paths.py`:

```python
from mocode.paths import (
    MOCODE_HOME,           # ~/.mocode
    CONFIG_PATH,           # ~/.mocode/config.json
    SKILLS_DIR,            # ~/.mocode/skills
    SESSIONS_DIR,          # ~/.mocode/sessions
    PLUGINS_DIR,           # ~/.mocode/plugins
    PROJECT_SKILLS_DIRNAME, # ".mocode" (for project-level dirs)
)
```

Customize paths by modifying `MOCODE_HOME` before imports (advanced).

## Session Management

Sessions persist conversation history, provider, and model state. Automatically isolated by working directory.

### Storage Structure

```
~/.mocode/sessions/
└── {workdir_sha256[:16]}/
    ├── session_20260318_143022.json
    ├── session_20260318_151530.json
    └── ...
```

### Session Schema

```json
{
  "id": "session_20260318_143022",
  "created_at": "2026-03-18T14:30:22.123456",
  "updated_at": "2026-03-18T14:35:10.789012",
  "workdir": "/path/to/project",
  "messages": [
    {"role": "user", "content": "..."},
    {"role": "assistant", "content": "...", "tool_calls": [...]}
  ],
  "model": "gpt-4o",
  "provider": "openai"
}
```

### Session Operations

```python
# Save current conversation
session = client.save_session()

# List all sessions (sorted by updated_at descending)
sessions = client.list_sessions()

# Load a session (replaces current conversation)
client.load_session(session_id)

# Delete a session
client.delete_session(session_id)

# Auto-save and clear on exit
client.clear_history_with_save()
```

**Auto-Save on Exit**: CLI automatically saves session if conversation has messages.

**Unsaved Changes**: MocodeCore tracks `has_unsaved_changes`. Set via `_mark_unsaved()` on chat. Cleared on save/load/clear.

## Hook System

Hooks allow plugins (or core) to intercept and modify behavior at specific points.

### Available Hook Points

```python
from mocode.plugins import HookPoint

# Plugin lifecycle
HookPoint.PLUGIN_LOAD
HookPoint.PLUGIN_ENABLE
HookPoint.PLUGIN_DISABLE
HookPoint.PLUGIN_UNLOAD

# Agent lifecycle
HookPoint.AGENT_CHAT_START   # Before agent chat loop
HookPoint.AGENT_CHAT_END     # After agent chat loop

# Tool lifecycle
HookPoint.TOOL_BEFORE_RUN    # Before tool execution (can skip)
HookPoint.TOOL_AFTER_RUN     # After tool execution (can modify result)

# Message lifecycle
HookPoint.MESSAGE_BEFORE_SEND  # Before sending to LLM
HookPoint.MESSAGE_AFTER_RECEIVE  # After receiving from LLM

# Prompt lifecycle
HookPoint.PROMPT_BUILD_START  # Before building system prompt
HookPoint.PROMPT_BUILD_END    # After building system prompt

# UI Component lifecycle
HookPoint.UI_COMPONENT_CREATED
HookPoint.UI_COMPONENT_RENDERED
HookPoint.UI_COMPONENT_COMPLETED
HookPoint.UI_COMPONENT_CLEARED
```

### Hook Registration

```python
from mocode.plugins import HookBase, HookContext, HookPoint, hook

class MyHook(HookBase):
    _name = "my-hook"
    _hook_point = HookPoint.TOOL_BEFORE_RUN
    _priority = 50  # lower = earlier execution

    def execute(self, ctx: HookContext) -> HookContext:
        tool_name = ctx.data.get("name")
        args = ctx.data.get("args", {})

        # Modify arguments
        if tool_name == "bash":
            args["command"] = f"echo 'Running: {args['command']}'"

        # Override result (skip tool execution)
        if should_skip:
            ctx.set_result("Skipped!")

        # Stop propagation (no other hooks)
        if should_stop:
            ctx.stop_propagation()

        # Set error (abort with error)
        if error:
            ctx.set_error(ValueError("Something went wrong"))

        return ctx

# Register via plugin.get_hooks() or directly to HookRegistry
```

### Hook Priority & Execution

Hooks sorted by `priority` (lower first). Can stop propagation with `ctx.stop_propagation()`.

HookRegistry triggers all hooks for a point:
```python
ctx = await hook_registry.trigger(HookPoint.TOOL_BEFORE_RUN, {"name": "bash", "args": {...}})
if ctx.modified:
    # Use ctx.result as overridden value
if ctx.has_error:
    # Abort with error
```

## Skill System

Skills provide modular, context-sensitive instructions that can be dynamically loaded by the LLM.

### Skill Discovery

`SkillManager` (singleton) scans directories:
1. `mocode/skills/builtin/` (built-in skills)
2. `~/.mocode/skills/` (global skills)
3. `.mocode/skills/` in current workdir (project-specific)

Skills discovered by presence of `SKILL.md` file.

### Skill Metadata (SKILL.md)

```markdown
---
name: web-scraper
description: Scrape web pages and extract content
version: 1.0.0
author: Your Name
dependencies:
  - requests>=2.28.0
  - beautifulsoup4
---

# Web Scraper Skill

This skill provides tools for fetching and parsing web content.

## Available Tools

- `fetch(url)` - Fetch a URL
- `parse(html, selector)` - Parse HTML with CSS selector

## Usage

When user asks to scrape a webpage:
1. Use `fetch` to get content
2. Use `parse` to extract data
3. Format and return results

## Examples

User: "Get the title of example.com"
Assistant: fetch("https://example.com") then extract <title>
```

### Skill Implementation (optional)

Skills can include Python code in `script.py`:

```python
import requests
from bs4 import BeautifulSoup

def fetch(args: dict) -> str:
    url = args["url"]
    response = requests.get(url)
    return response.text

def parse(args: dict) -> str:
    html = args["html"]
    selector = args.get("selector", "*")
    soup = BeautifulSoup(html, "html.parser")
    elements = soup.select(selector)
    return "\n".join([e.get_text() for e in elements])

# Tool definitions (same @tool decorator)
from mocode.tools import tool

@tool("fetch", "Fetch URL", {"url": "string"})
def _fetch_tool(args):
    return fetch(args)

@tool("parse", "Parse HTML", {"html": "string", "selector?": "string"})
def _parse_tool(args):
    return parse(args)
```

Dependencies declared in SKILL.md frontmatter are installed in skill's isolated environment when enabled.

### Skill Loading

LLM automatically selects appropriate skill based on user request and system prompt. Manually load via:

```python
# In chat: "Use the web-scraper skill"
# LLM calls: skill(name="web-scraper")

# SDK
from mocode.skills.manager import SkillManager
sm = SkillManager.get_instance()
skill = sm.get_skill("web-scraper")
content = skill.load_content()  # Full SKILL.md content
```

## Tool Development

Tools are functions that the LLM can call. Registered globally to `ToolRegistry`.

### Tool Signature

```python
def tool_function(args: dict) -> str:
    """
    Args:
        args: Validated arguments with defaults filled in

    Returns:
        Result string (displayed to user, sent back to LLM)
    """
    # Access arguments
    param1 = args["param1"]  # required
    param2 = args.get("param2")  # optional

    # Execute logic
    result = do_something(param1, param2)

    # Return string (automatically truncated if tool_result_limit set)
    return result
```

### Tool Registration

```python
from mocode.tools import tool

@tool(
    name="read",
    description="Read file contents",
    params={
        "path": {"type": "string", "description": "File path", "default": ""},
        "offset": {"type": "integer", "description": "Byte offset", "default": 0},
        "limit": {"type": "integer", "description": "Max bytes to read"}
    }
)
def read_file(args: dict) -> str:
    path = args["path"]
    offset = args.get("offset", 0)
    limit = args.get("limit")

    try:
        with open(path, "r") as f:
            content = f.read()[offset:]
            if limit:
                content = content[:limit]
            return content
    except Exception as e:
        return f"Error: {e}"
```

**Parameter Specifications**:
- Shorthand: `"type?"` for optional (e.g., `"number?"`)
- Full: `{"type": "...", "description": "...", "default": ...}`
- Types: `"string"`, `"number"` (integer), `"boolean"`, `"array"`, `"object"`

### Existing Tools

Located in `mocode/tools/`:
- `file_tools.py`: `read`, `write`, `edit`, `ls`, `mkdir`, `rm`, `mv`, `cp`
- `search_tools.py`: `glob`, `grep`
- `bash.py`: `bash` (shell execution)
- `utils.py`: `truncate_result`

Register new tools in `tools/__init__.py::register_all_tools()` or via `@tool` decorator at module level.

## Prompt System

System prompt is built dynamically from `PromptBuilder`.

### PromptBuilder API

```python
from mocode.core.prompt import PromptBuilder, StaticSection, DynamicSection

builder = PromptBuilder()

# Add static section (always included)
builder.add(StaticSection(
    name="identity",
    priority=100,
    content="You are mocode, a helpful coding assistant..."
))

# Add dynamic section (function evaluated at build time)
def get_cwd_context(cwd: str, **kwargs) -> str:
    return f"Current working directory: {cwd}"

builder.add(DynamicSection(
    name="cwd",
    priority=90,
    func=get_cwd_context
))

# Build final prompt
prompt = builder.build()

# Clear caches (recompute dynamic sections)
builder.clear_caches()
```

**Section Priority**: Higher priority sections come first. Default sections have priorities 10-90. Custom sections >100 added before defaults.

### Default Prompt Sections

Built-in sections:
- `identity` (100) - Core identity
- `capabilities` (90) - Tool descriptions
- `skills` (80) - Available skills
- `rules` (70) - General rules
- `permissions` (60) - Permission guidance
- `mode` (50) - Current mode information

Override with `default_prompt()`, `minimal_prompt()`, or custom `PromptBuilder`.

### Rebuilding Prompt

```python
# Rebuild with current skills and cwd
client.rebuild_system_prompt()

# Rebuild with custom context (e.g., after mode change)
client.rebuild_system_prompt(context={"mode_info": "Now in yolo mode"})

# Direct update (no context)
client.update_system_prompt("Custom system prompt", clear_history=True)
```

## Testing

### Run Tests

```bash
# Unit tests
pytest tests/

# With coverage
pytest --cov=mocode tests/

# Specific module
pytest tests/test_config.py
```

### Manual Testing

```bash
# Run CLI
mocode

# As library
python -c "
import asyncio
from mocode import MocodeClient

async def test():
    client = MocodeClient(config={'current': {'provider': 'openai', 'model': 'gpt-4o'}})
    result = await client.chat('Hello')
    print(result)

asyncio.run(test())
"
```

### Plugin Development Testing

```bash
# Install plugin to ~/.mocode/plugins/
cp -r my-plugin ~/.mocode/plugins/

# In mocode CLI: /plugin discover
# Or programmatically:
client.discover_plugins()
```

## Configuration Tips

### Multi-Provider Setup

```json
{
  "current": {"provider": "openai", "model": "gpt-4o"},
  "providers": {
    "openai": {
      "name": "OpenAI",
      "base_url": "https://api.openai.com/v1",
      "api_key": "sk-...",
      "models": ["gpt-4o", "gpt-4o-mini"]
    },
    "anthropic": {
      "name": "Anthropic",
      "base_url": "https://api.anthropic.com/v1",
      "api_key": "sk-...",
      "models": ["claude-opus-4", "claude-sonnet-4"]
    },
    "local": {
      "name": "Local LLM",
      "base_url": "http://localhost:11434/v1",
      "api_key": "",
      "models": ["llama2", "codellama"]
    }
  }
}
```

Switch via CLI: `/provider` command or programmatically: `client.set_provider("anthropic")`.

### Permission Rules Examples

```json
{
  "permission": {
    "*": "ask",                           // Default ask
    "ls": "allow",                       // Allow ls always
    "read": "allow",                     // Allow read always
    "write": {"*": "deny"},              // Deny all write
    "bash": {                            // Bash command rules
      "*": "ask",
      "ls *": "allow",
      "pwd": "allow",
      "cat *": "allow",
      "rm *": "deny",
      "git *": "allow",
      "npm *": "ask",
      "docker *": "ask"
    }
  }
}
```

### Yolo Mode for Safe Automation

Enable yolo mode for faster workflow with safety guardrails:

```json
{
  "modes": {
    "yolo": {
      "auto_approve": true,
      "dangerous_patterns": [
        "rm ", "rmdir ", "dd ", "mv ",
        "del ", "rd ", "format ", "mkfs ",
        "chmod ", "chown ", "sudo "
      ]
    }
  },
  "current_mode": "normal"
}
```

Switch: `client.set_mode("yolo")` or CLI `/mode yolo`.

All tools auto-approved EXCEPT:
- Bash commands starting with dangerous patterns
- Tools with DENY permission rules
- Tools requiring ASK (yolo only overrides auto-approve eligible)

### Prevent Context Overflow

Large tool outputs can fill context window. Use `tool_result_limit`:

```json
{
  "tool_result_limit": 25000  // Truncate tool results > 25k chars
}
```

Set to `0` for no limit (not recommended).

Truncation preserves start and end, replaces middle with `[... truncated ...]`.

## Troubleshooting

### Plugin Not Loading

Check:
1. Plugin directory exists in `~/.mocode/plugins/` or `.mocode/plugins/`
2. `plugin.py` defines `plugin_class` variable
3. Plugin class inherits from `Plugin`
4. Dependencies installed (check `plugin.yaml` or metadata)
5. Run `/plugin discover` to re-scan

### Tool Not Found

1. Tool registered with `@tool` decorator (executed at import)
2. Module imported before first use (tools auto-register on `register_all_tools()`)
3. Check spelling in LLM function call

### Permission Denied

1. Check `permission` config rules
2. In yolo mode, only dangerous bash commands denied
3. Custom `PermissionHandler` may be blocking
4. CLI: Use ESC to interrupt or type response to permission prompt

### Config Not Saving

- `persistence=True` when creating `MocodeClient` or `MocodeCore`
- Config path writable: `~/.mocode/config.json`
- Call `client.save_config()` manually if needed

### Events Not Firing

- Ensure subscription before operation starts
- Check `EventType` matches emitted type
- Event handlers can be async or sync
- Use `client.event_bus` directly for advanced usage

## Performance Tips

1. **Tool Result Truncation**: Set `tool_result_limit` to avoid context bloat
2. **Session Management**: Clear old sessions periodically (`~/.mocode/sessions/`)
3. **Plugin Isolation**: Non-builtin plugins use separate venv (slight overhead)
4. **Skill Loading**: Skills loaded on demand, not all at once
5. **Event Handlers**: Keep handlers lightweight; offload heavy work to background tasks

## Contributing

Follow code style: clean, simple, no emojis.

Run tests before submitting PR:
```bash
pytest tests/
uv sync  # ensure dependencies
```

Update documentation when adding features:
- `CLAUDE.md` - This file
- `docs/` directory (if exists)
- Inline code comments

## Resources

- Codebase: `mocode/` package
- Config: `~/.mocode/config.json`
- Sessions: `~/.mocode/sessions/`
- Skills: `~/.mocode/skills/`
- Plugins: `~/.mocode/plugins/`
- Logs: Check terminal output or configure logging
