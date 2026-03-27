# mocode 项目指南

mocode is a CLI coding assistant powered by LLM with interactive terminal interface, tool-calling capabilities, and SDK for embedding.

**Requirements**: Python >= 3.10

## Quick Start

```bash
# Install
uv tool install -e .

# Run
mocode

# Install dependencies
uv sync
```

**Code Style**: Clean, simple, no emojis.

## Architecture

**Layered**: `MocodeClient` (SDK) → `MocodeCore` (orchestrator) → `AsyncAgent` (LLM loop). Core is UI-independent.

**Key Components**:
- `core/` - Business logic, event bus, config, permissions, sessions, prompts
- `tools/` - Tool registry with `@tool` decorator
- `plugins/` - Hook system, plugin lifecycle, isolated environments
- `skills/` - Modular instructions loaded on demand
- `cli/` - Terminal interface, slash commands, UI components

**Event-Driven**: `EventBus` decouples agent from UI. Key events: `TEXT_STREAMING`, `TOOL_START/COMPLETE`, `MESSAGE_ADDED`, `ERROR`, `AGENT_IDLE`.

**Data Flow**: User → `MocodeClient.chat()` → `MocodeCore.chat()` → `AsyncAgent.chat()` → LLM API → tool execution loop (permission check → hooks → run → truncate) → response.

## Configuration

Config: `~/.mocode/config.json`

**Essential fields**:
- `current.provider/model` - Active provider and model
- `providers` - Provider configurations (base_url, api_key, models)
- `permission` - Tool permissions (`allow`, `ask`, `deny`)
- `tool_result_limit` - Max output size (0 = unlimited, default: 25000)
- `modes` - Mode configurations
- `current_mode` - Active mode (`normal` or `yolo`)

**Example**:
```json
{
  "current": {"provider": "openai", "model": "gpt-4o"},
  "providers": {
    "openai": {
      "base_url": "https://api.openai.com/v1",
      "api_key": "sk-...",
      "models": ["gpt-4o", "gpt-4o-mini"]
    }
  },
  "permission": {"*": "ask", "bash": "allow"},
  "tool_result_limit": 25000,
  "modes": {
    "normal": {"auto_approve": false},
    "yolo": {
      "auto_approve": true,
      "dangerous_patterns": ["rm ", "rmdir ", "dd ", "mv ", "del ", "chmod ", "sudo ", "format ", "mkfs "]
    }
  },
  "current_mode": "normal"
}
```

**Modes**:
- `normal` - Standard permission checks
- `yolo` - Auto-approves non-dangerous tools (except bash commands with dangerous patterns)

## Extending mocode

### Tools

Register with `@tool` decorator:

```python
from mocode.tools import tool

@tool("my_tool", "Description", {"arg": "string", "opt?": "number"})
def my_tool(args: dict) -> str:
    return f"Result: {args['arg']}"
```

Params: `"type"` (string, number, boolean, array, object), add `?` for optional.

### Commands

Subclass `Command` and use `@command`:

```python
@command("/mycmd", description="Description")
class MyCommand(Command):
    def execute(self, ctx: CommandContext) -> bool:
        ctx.layout.add_message("Done")
        return True
```

Add to `cli/commands/__init__.py::BUILTIN_COMMANDS`.

### Skills

Modular instructions loaded on demand.

**Structure**:
```
~/.mocode/skills/skill-name/
├── SKILL.md           # Required: YAML frontmatter + instructions
├── script.py          # Optional: tool implementations
└── requirements.txt   # Optional: dependencies
```

**SKILL.md**:
```markdown
---
name: skill-name
description: Brief description
dependencies:
  - requests>=2.0
---

# Instructions

LLM usage instructions...
```

Discovery: project `.mocode/skills/` → global `~/.mocode/skills/` → builtin.

### Plugins

Extend with hooks, tools, commands. Run in isolated venv.

**Structure**:
```
~/.mocode/plugins/plugin-name/
├── plugin.py      # Required: Plugin class
├── plugin.yaml    # Optional: metadata
└── requirements.txt
```

**Basic plugin**:
```python
from mocode.plugins import Plugin, PluginMetadata, hook, HookPoint

@hook(HookPoint.TOOL_AFTER_RUN)
def my_hook(ctx: HookContext) -> HookContext:
    # Modify behavior
    return ctx

class MyPlugin(Plugin):
    def __init__(self):
        self.metadata = PluginMetadata(
            name="my-plugin",
            dependencies=["requests>=2.0"],
            replaces_tools=[]
        )

    def get_hooks(self):
        return [my_hook]

plugin_class = MyPlugin
```

**Hook points**: `PLUGIN_LOAD/ENABLE/DISABLE/UNLOAD`, `AGENT_CHAT_START/END`, `TOOL_BEFORE_RUN/AFTER_RUN`, `MESSAGE_BEFORE_SEND/AFTER_RECEIVE`, `PROMPT_BUILD_START/END`, `UI_COMPONENT_*`.

**Lifecycle**: discover → load → enable (install deps) → disable → unload.

**Discovery**: `~/.mocode/plugins/`, `<workdir>/.mocode/plugins/`, builtin.

## SDK Usage

```python
from mocode import MocodeCore
import asyncio

async def main():
    config = {
        "current": {"provider": "openai", "model": "gpt-4o"},
        "providers": {
            "openai": {
                "base_url": "https://api.openai.com/v1",
                "api_key": "sk-...",
                "models": ["gpt-4o", "gpt-4o-mini"]
            }
        },
        "permission": {"*": "ask"},
        "tool_result_limit": 25000
    }

    client = MocodeCore(config=config, persistence=False)

    # Chat
    response = await client.chat("Hello!")
    print(response)

    # Subscribe to events
    def on_text(event):
        print(f"Response: {event.data['content']}")
    client.on_event(EventType.TEXT_COMPLETE, on_text)

    # Mode switching
    client.set_mode("yolo")

    # Sessions
    client.save_session()
    sessions = client.list_sessions()
    client.load_session(sessions[0].id if sessions else None)

    # Plugins
    await client.enable_plugin("my-plugin")
    await client.disable_plugin("my-plugin")

    # Provider/Model
    client.set_model("gpt-4o-mini")
    client.set_provider("anthropic", "claude-opus-4")

    # Prompt
    client.rebuild_system_prompt()

asyncio.run(main())
```

**Key Properties**:
- `client.config`, `client.agent`, `client.event_bus`, `client.workdir`
- `client.current_model`, `client.current_provider`

**Key Methods**:
- `chat(message)` → response
- `interrupt()`, `clear_history()`
- `save_session()`, `load_session(id)`, `list_sessions()`
- `set_model(model)`, `set_provider(key, model)`
- `enable_plugin(name)`, `disable_plugin(name)`, `list_plugins()`, `discover_plugins()`
- `set_mode(name)`
- `on_event(type, handler)`, `off_event(type, handler)`
- `rebuild_system_prompt(context)` / `update_system_prompt(prompt)`

## Core Systems

### Events

`EventBus` decouples components. Subscribe:

```python
from mocode.core.events import EventType

def handler(event):
    print(f"{event.type}: {event.data}")

client.on_event(EventType.TEXT_COMPLETE, handler)
```

**Common Events**: `TEXT_STREAMING`, `TEXT_DELTA`, `TEXT_COMPLETE`, `TOOL_START`, `TOOL_COMPLETE`, `MESSAGE_ADDED`, `MODEL_CHANGED`, `ERROR`, `STATUS_UPDATE`, `INTERRUPTED`, `AGENT_IDLE`.

### Permissions

Control tool execution with rules in config:

```json
{
  "permission": {
    "*": "ask",              // Default for all tools
    "read": "allow",         // Always allow read
    "write": "deny",         // Always deny write
    "bash": {                // Per-command rules
      "*": "ask",
      "ls *": "allow",
      "rm *": "deny"
    }
  }
}
```

Matching: exact tool name → wildcard `*` → nested glob patterns.

### Modes

- `normal` (default) - Respects permission rules, prompts for `ask`
- `yolo` - Auto-approves safe tools, only blocks dangerous bash commands

Switch: `client.set_mode("yolo")` or CLI `/mode yolo`.

## Important Paths

From `mocode/paths.py`:

- `MOCODE_HOME` - `~/.mocode`
- `CONFIG_PATH` - `~/.mocode/config.json`
- `SKILLS_DIR` - `~/.mocode/skills`
- `SESSIONS_DIR` - `~/.mocode/sessions`
- `PLUGINS_DIR` - `~/.mocode/plugins`
- `PROJECT_SKILLS_DIRNAME` - `.mocode`

## Session Management

Auto-saved by workdir. Sessions stored in `~/.mocode/sessions/{workdir_hash}/`.

```python
session = client.save_session()
sessions = client.list_sessions()
client.load_session(session_id)
client.delete_session(session_id)
```

CLI auto-saves on exit if conversation has messages.

## CLI Commands

Built-in: `/help`, `/provider` (model selection), `/session`, `/plugin`, `/skills`, `/clear`, `/exit`.

RTK plugin: `/rtk` (token optimization).

Use ESC to interrupt.

## Testing

```bash
# Unit tests
pytest tests/

# With coverage
pytest --cov=mocode tests/

# Run CLI manually
mocode
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

## Contributing

Code style: clean, simple, no emojis.

Run tests: `pytest tests/` then `uv sync`.

Update docs when adding features.

## Resources

- Codebase: `mocode/`
- Config: `~/.mocode/config.json`
- Data: `~/.mocode/{sessions,skills,plugins}/`
- Logs: terminal output
