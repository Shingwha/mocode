# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**MoCode** is a CLI coding assistant powered by LLMs, written in Python. This is the v0.2 rewrite — a modular, dependency-injected architecture replacing the v0.1 "god class" design.

### Core Architecture

The application follows a **thin composite root** pattern:

```
App (thin facade) ←→ AppBuilder (DI container)
    ↓
  Agent (LLM orchestration)
    ↓
  Provider (LLM API — OpenAI-compatible)
  ToolRegistry (scoped tool registry)
  PermissionChecker (tool approval)
  CompactManager (context compression)
  DreamManager (background reflection)
  SessionManager (conversation persistence)
  SkillManager (plugin discovery)
  EventBus (pub/sub)
```

Key design decisions from v0.2:
- **No global state**: `ToolRegistry` is instance-scoped (one per `App`), not a class-level singleton
- **Provider abstraction**: All LLM consumers use `Response`/`ToolCall`/`Usage` DTOs; `OpenAIProvider` is the only SDK-aware implementation
- **Protocol-based**: `Provider` is a `Protocol`, enabling alternative backends
- **Store protocols**: `ConfigStore` and `SessionStore` are protocols with file/memory implementations
- **CancellationToken**: Uses `asyncio.Event` + `threading.Event` for zero-polling cancellation

### Directory Structure

```
mocode/
  __init__.py       — Public API exports
  app.py            — App (facade) + AppBuilder (DI)
  agent.py          — Agent: chat loop, tool execution, media handling
  config.py         — Config dataclass (pure data, no persistence)
  provider.py       — Provider protocol + OpenAIProvider implementation
  tool.py           — Tool class + ToolRegistry (instance-scoped)
  event.py          — EventBus + EventType enum
  interrupt.py      — CancellationToken + Interrupted exception
  session.py        — SessionManager (store-agnostic)
  store.py          — Session/Config store protocols + file/memory impls
  prompt.py         — PromptBuilder + section system + SOUL/USER/MEMORY
  compact.py        — CompactManager (context window tracking + LLM summarization)
  subagent.py       — SubAgent (lightweight agent with isolated message history)
  paths.py          — Centralized path constants (MOCODE_HOME, CONFIG_PATH, etc.)
  permission.py     — PermissionChecker + PermissionHandler + mode system
  message_queue.py  — MessageQueue for async message injection
  tools/
    __init__.py     — register_basic_tools() + register_system_tools()
    file.py         — read, write, append, edit
    bash.py         — bash (persistent Git Bash session)
    search.py       — glob, grep
    fetch.py        — fetch (HTTP with httpx)
    compact.py      — compact tool
    dream.py        — dream tool
    subagent.py     — sub_agent tool
    utils.py        — truncate_result()
  dream/
    agent.py        — DreamAgent (uses SubAgent)
    manager.py      — DreamManager (orchestrates cycle)
    cursor.py       — DreamCursor (processed summary tracking)
    snapshot.py     — SnapshotStore (memory file backups)
    prompts.py      — DREAM_SYSTEM_PROMPT + build_dream_prompt()
  skills/
    manager.py      — SkillManager (SKILL.md discovery + `skill` tool)
    schema.py       — Skill + SkillMetadata dataclasses
  .mocode/
    skills/         — Project-level skills (highest priority)
```

### Data Flow

1. **AppBuilder.build()** assembles all components:
   - Loads `Config` from `ConfigStore` (file or in-memory)
   - Creates `OpenAIProvider` from config
   - Builds `ToolRegistry` and registers basic + system tools
   - Creates `SkillManager` and registers `skill` tool
   - Constructs `CompactManager`, `PermissionChecker`, `PromptBuilder`
   - Builds `Agent` with all dependencies
   - Optionally creates `DreamManager` if enabled
   - Wires `EventType.CONTEXT_COMPACT` to session state updates

2. **Chat flow**: `App.chat()` → `Agent.chat()` → provider call → tool execution loop → events emitted

3. **Persistence**: Sessions stored as `session_*.json` in `~/.mocode/sessions/<workdir_hash>/`; config at `~/.mocode/config.json`

## Common Development Tasks

### Setup

```bash
# Create virtual environment (already done: .venv/ exists)
uv sync

# Install in editable mode
uv pip install -e .
```

### Running Tests

```bash
# All tests
uv run pytest

# Specific module
uv run pytest tests/test_agent.py
uv run pytest tests/test_app.py
uv run pytest tests/test_dream/

# Specific test class or function
uv run pytest tests/test_agent.py::TestSimpleChat
uv run pytest tests/test_agent.py::TestSimpleChat::test_simple_chat

# With coverage
uv run pytest --cov=mocode

# Watch mode (if installed pytest-watcher)
ptw
```

### Linting & Formatting

```bash
# Check (ruff is configured in pyproject.toml)
uv run ruff check .

# Auto-fix
uv run ruff check --fix .

# Format
uv run ruff format .
```

### Running the CLI (not yet ported)

The `mocode` console script entry point is defined in `pyproject.toml` as `mocode = "mocode.main:main"`. The `main.py` file has not yet been ported to v0.2 — this is a known gap.

## Configuration

Config is stored at `~/.mocode/config.json` (or `MOCODE_HOME/config.json`). Default providers:

| Provider | Base URL | Default Model |
|----------|----------|---------------|
| zhipu    | https://open.bigmodel.cn/api/coding/paas/v4/ | glm-5 / glm-5.1 |
| step     | https://api.stepfun.com/step_plan/v1 | step-3.5-flash |

To add a custom provider:
```python
app.add_provider("openai", "OpenAI", "https://api.openai.com/v1", api_key="sk-...", models=["gpt-4o"])
```

### Modes

Two built-in modes (in `Config.modes`):
- **normal** (default): No auto-approve, permission handler asks
- **yolo**: `auto_approve=True` except for dangerous patterns (`rm`, `mv`, `sudo`, `format`, `dd`, etc.)

## Key Concepts

### Provider Protocol

All LLM calls go through the `Provider` protocol. The `OpenAIProvider` implementation uses the `openai` AsyncClient. Response is normalized to `Response` DTO with `content`, `tool_calls` (list of `ToolCall`), `usage` (`Usage`), and `finish_reason`.

### ToolRegistry

Instance-scoped registry. Tools are registered via factory functions that receive `(registry, config)` and close over `config` for timeout/limits. Each `App` gets its own `ToolRegistry` — no cross-talk.

### Events

`EventBus` supports sync and async handlers with priority ordering. Key events: `TEXT_COMPLETE`, `TOOL_START`, `TOOL_COMPLETE`, `MESSAGE_ADDED`, `MODEL_CHANGED`, `CONTEXT_COMPACT`, `DREAM_START/COMPLETE`, `INTERRUPTED`.

### Compact

`CompactManager` tracks `last_prompt_tokens` via `update_usage()`. When `should_compact(model)` returns true (configurable threshold, default 0.80 of context window), it calls the LLM to generate a structured summary, replaces old messages with `[Context Summary]` + assistant acknowledgment + recent turns.

### Dream

`DreamManager` runs on a schedule (not automatically wired yet). It reads summaries from `~/.mocode/dream/summaries/`, loads `SOUL.md`/`USER.md`/`MEMORY.md`, uses a `DreamAgent` (SubAgent with `read`/`edit`/`append` only) to process summaries and edit memory files, then snapshots the memory dir before edits.

### Skills

`SkillManager` discovers skills from:
1. `~/.mocode/skills/` (global)
2. `./.mocode/skills/` (project-local, highest priority)

Each skill is a folder containing `SKILL.md` with YAML frontmatter (`name`, `description`, `tags`, `author`). The `skill` tool loads a skill by name and returns its directory path + full content.

### Sessions

`SessionManager` hashes the workdir to namespace sessions. Each session is a JSON file with `id`, `created_at`, `updated_at`, `workdir`, `messages`, `model`, `provider`. Sessions are listed sorted by `updated_at` descending.

### Permission System

`PermissionChecker` combines:
- Mode-based auto-approval (`normal` = ask, `yolo` = auto-approve except dangerous)
- `PermissionConfig` rules (tool-level allow/deny/ask, with path/command patterns)
- `PermissionHandler` for interactive prompts (default = auto-allow; `DenyAllPermissionHandler` for tests)

Tool arguments are inspected to extract target (e.g., `bash` command, file `path`) for fine-grained pattern matching.

## Important Files to Read

- `mocode/app.py` — App and AppBuilder (composite root + DI)
- `mocode/agent.py` — Core chat loop and tool execution
- `mocode/config.py` — All configuration dataclasses
- `mocode/tool.py` — Tool and ToolRegistry definitions
- `mocode/provider.py` — Provider protocol and OpenAI implementation
- `mocode/prompt.py` — PromptBuilder and memory file rendering

## Testing Notes

- Uses `pytest` with `pytest-asyncio` (Mode.STRICT)
- Test fixtures in `tests/conftest.py`: `config`, `event_bus`, `cancel_token`, `registry`
- 215 tests covering agent, app, compact, dream, permission, session, tools, subagent
- Tests use `InMemoryConfigStore` / `InMemorySessionStore` for isolation
- Mock provider pattern: pass a simple object with `.call()` returning `Response` DTO

## Not Yet Implemented (v0.2 gap)

- `mocode/main.py` — CLI entry point (being ported)
- REPL/REPL server integration
- Gateway/http server (was in v0.1)
- Cron/scheduled tasks wiring to Dream
- Message queue full implementation

## Code Style

- Python 3.12+ (uses `from __future__ import annotations` throughout)
- Dataclasses for DTOs, Protocols for abstractions
- `snake_case` for functions/variables, `PascalCase` for classes
- Docstrings with "v0.2 关键改进：" notes describing architectural changes
- Type hints everywhere; `TYPE_CHECKING` guards for circular imports
