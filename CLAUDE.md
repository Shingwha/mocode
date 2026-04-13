# mocode 项目指南

mocode is a CLI coding assistant powered by LLM with interactive terminal interface, tool-calling capabilities, and SDK for embedding.

**Requirements**: Python >= 3.12

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

**Layered**: `MocodeCore` (orchestrator/SDK) → `AsyncAgent` (LLM loop). `MocodeClient` is a backward-compatibility alias for `MocodeCore`. Core is UI-independent.

**Key Components**:
- `core/` - Business logic, event bus, config, permissions, sessions, prompts, commands
- `core/commands/` - Command infrastructure (CommandContext, CommandResult, CommandRegistry)
- `tools/` - Tool registry with `@tool` decorator
- `skills/` - Modular instructions loaded on demand
- `cli/` - Terminal interface, CLI command wrappers, UI components
- `gateway/` - Third-party platform integration (WeChat, etc.)

**Event-Driven**: `EventBus` decouples agent from UI. Key events: `TEXT_STREAMING`, `TOOL_START/COMPLETE`, `MESSAGE_ADDED`, `ERROR`, `AGENT_IDLE`.

**Data Flow**: User → `MocodeCore.chat()` → `AsyncAgent.chat()` → LLM API → tool execution loop (permission check → run → truncate) → response.

## Configuration

Config: `~/.mocode/config.json`

**Essential fields**:
- `current.provider/model` - Active provider and model
- `providers` - Provider configurations (base_url, api_key, models)
- `permission` - Tool permissions (`allow`, `ask`, `deny`)
- `tool_result_limit` - Max output size (0 = unlimited, default: 25000)
- `gateway` - Gateway configuration (max_users, idle_timeout, etc.)

**Note**: `modes` and `current_mode` are in-memory only and NOT persisted to config file. They are initialized at runtime with defaults.

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
  "tool_result_limit": 25000
}
```

**Modes** (in-memory only):
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

Params: `"type"` (string, number, boolean, array, object), add `?` for optional. Full format: `{"arg": {"type": "string", "default": "..."}}`.

### Commands

Subclass `Command` and use `@command`:

```python
from mocode.core.commands.base import Command, CommandContext, command
from mocode.core.commands.result import CommandResult

@command("/mycmd", description="Description")
class MyCommand(Command):
    def execute(self, ctx: CommandContext) -> CommandResult:
        return CommandResult(success=True, message="Done")
```

Register in `core/commands/__init__.py::BUILTIN_COMMANDS` or in `cli/commands/__init__.py::BUILTIN_COMMANDS` for CLI-wrapped versions.

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

**Key Properties**:
- `client.config`, `client.agent`, `client.event_bus`, `client.workdir`
- `client.current_model`, `client.current_provider`

**Key Methods**:
- `chat(message)` → response
- `interrupt()`, `clear_history()`
- `save_session()`, `load_session(id)`, `list_sessions()`
- `set_model(model)`, `set_provider(key, model)`
- `config.set_mode(name)`
- `on_event(type, handler)`, `off_event(type, handler)`
- `inject_message(role, content)`, `queue_message(role, content)`
- `add_provider(key, config)`, `add_model(provider, model)`, `remove_provider(key)`, `remove_model(provider, model)`, `update_provider(key, updates)`
- `rebuild_system_prompt(context)` / `update_system_prompt(prompt)`

## Gateway

Third-party platform integration layer. Each platform user gets an isolated `MocodeCore` instance.

**Start**: `mocode gateway --type weixin`

**Architecture**:
- `BaseChannel` — Abstract base with lifecycle (`start()`/`stop()`/`send()`)
- `ChannelManager` — Dispatches messages with retry logic
- `UserRouter` — Per-user session management with LRU eviction and `asyncio.Lock`
- `WeixinChannel` — WeChat implementation via direct HTTP long-poll API
- `GatewayApp` — Entry point with gateway registry

**Key behaviors**:
- Forced yolo mode (auto-approve safe tools)
- Per-user serialization (same user messages queued, different users parallel)
- Long messages auto-split at 3500 chars by newline boundaries
- LRU eviction when exceeding `max_users` (default 100)
- Sessions saved on eviction/shutdown

**Config** (in `~/.mocode/config.json`):
```json
{
  "gateway": {
    "max_users": 100,
    "idle_timeout": 3600
  }
}
```

**Adding new channels**: Subclass `BaseChannel`, implement `start()`, `stop()`, `send()`, and register in `gateway/registry.py`.

## Core Systems

### Events

`EventBus` decouples components. Subscribe:

```python
from mocode.core.events import EventType

def handler(event):
    print(f"{event.type}: {event.data}")

client.on_event(EventType.TEXT_COMPLETE, handler)
```

**Common Events**: `TEXT_STREAMING`, `TEXT_DELTA`, `TEXT_COMPLETE`, `TOOL_START`, `TOOL_COMPLETE`, `TOOL_PROGRESS`, `MESSAGE_ADDED`, `MODEL_CHANGED`, `ERROR`, `STATUS_UPDATE`, `PERMISSION_ASK`, `INTERRUPTED`, `AGENT_IDLE`, `COMPONENT_STATE_CHANGE`, `COMPONENT_COMPLETE`.

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

Switch: `client.config.set_mode("yolo")` or CLI `/mode yolo`.

From `mocode/paths.py`:

- `MOCODE_HOME` - `~/.mocode`
- `CONFIG_PATH` - `~/.mocode/config.json`
- `SKILLS_DIR` - `~/.mocode/skills`
- `SESSIONS_DIR` - `~/.mocode/sessions`
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

Built-in: `/help`, `/provider` (model selection), `/session`, `/skills`, `/clear`, `/exit`.

Use ESC to interrupt.

## Testing

282 tests across 13 files. Dependencies: `pytest>=8.0`, `pytest-asyncio>=0.23`, `pytest-cov`.

**IMPORTANT**: 必须使用 `uv run pytest` 来运行测试。不要使用 `python -c`、`python -m pytest` 或裸 `pytest` 命令，因为项目依赖 uv 管理虚拟环境，直接运行可能找不到正确的 Python 或依赖。

```bash
# Install test dependencies
uv sync --extra test

# Run all tests
uv run pytest tests/ -v

# Single module
uv run pytest tests/test_events.py -v

# With coverage
uv run pytest tests/ --cov=mocode --cov-report=term-missing

# Run CLI manually
mocode
```

**Test structure**:
```
tests/
├── conftest.py                    # Shared fixtures (config, event_bus, interrupt_token)
├── test_events.py                 # EventBus (9 tests)
├── test_interrupt.py              # InterruptToken (4 tests)
├── test_utils.py                  # truncate_result, preview_result (10 tests)
├── test_config.py                 # Config CRUD, modes, properties (27 tests)
├── test_permission.py             # PermissionConfig, PermissionChecker (18 tests)
├── test_tools.py                  # ToolRegistry, file/search tools (24 tests)
├── test_session.py                # SessionManager CRUD, isolation (14 tests)
├── test_agent.py                  # AsyncAgent with mock provider (17 tests)
├── test_message_queue.py          # MessageQueue async (7 tests)
├── test_prompt_builder.py         # PromptBuilder, Section (12 tests)
├── test_orchestrator.py           # MocodeCore integration (23 tests)
└── test_gateway/
    ├── test_crypto.py             # AES encrypt/decrypt, key parsing (11 tests)
    ├── test_bus.py                # MessageBus pub/sub (6 tests)
    └── test_router.py             # UserRouter LRU, isolation (8 tests)
```

**Coverage**: Core modules 80%+. Key coverage: `interrupt.py` 100%, `message_queue.py` 100%, `tools/base.py` 100%, `events.py` 95%, `config.py` 93%, `session.py` 93%.


Switch: `client.config.set_mode("yolo")` or CLI `/mode yolo`.

All tools auto-approved EXCEPT:
- Bash commands starting with dangerous patterns
- Tools with DENY permission rules
- Tools requiring ASK (yolo only overrides auto-approve eligible)

## Troubleshooting

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

Run tests: `uv sync --extra test && uv run pytest tests/ -v`.

Update docs when adding features.

## Resources

- Codebase: `mocode/`
- Config: `~/.mocode/config.json`
- Data: `~/.mocode/{sessions,skills}/`
- Logs: terminal output
