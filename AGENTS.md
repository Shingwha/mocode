# MoCode 0.3 — Development Guide

## Dev Commands

```bash
# Install dependencies (uses uv + hatchling)
uv sync

# Run all tests
uv run pytest

# Run single test file
uv run pytest tests/test_builder.py

# Run single test class/method
uv run pytest tests/test_builder.py::TestBuilder::test_minimal_build

# Run Python one-liner (ad-hoc scripts, quick checks)
uv run python -c "from mocode.core import Tool; t = Tool('x','desc',{'p':{'type':'string','description':'p'}}, lambda a:'ok'); print(t.to_schema())"
```

## Architecture Overview

MoCode is a lean agent framework with a layered design:

```
mocode/
├── core/          # Framework primitives (no app dependencies)
│   ├── agent.py       # AgentLoop — LLM chat engine with parallel tool execution
│   ├── builder.py     # Agent — fluent builder for AgentLoop
│   ├── provider.py    # Provider Protocol + Response/ToolCall/Usage DTOs
│   ├── tool.py        # Tool + ToolRegistry — instance-scoped, sync/async
│   ├── hook.py        # AgentHook + HookRunner — lifecycle hooks with error isolation
│   ├── prompt.py      # Prompt + Section — section-based, format-aware (xml/text)
│   ├── skill.py       # Skill + SkillManager — directory-based discovery
│   └── virtualfs.py   # VirtualFS — in-memory vfs:// file system
├── app/           # Application layer
│   ├── cli/           # CLIApp, Display, Input, Spinner, Commands
│   ├── config.py      # Config — JSON load/save, multi-provider
│   ├── session.py     # Session + SessionManager + FileSessionStore
│   └── workflow/      # DAG engine: models, runner, graph, state, events
├── tools/         # Built-in tools (file, bash, search, fetch, etc.)
├── hooks/         # Built-in hooks (CompactHook, GoalHook)
├── prompts/       # System prompts (app, compact, subagent)
├── providers/     # LLM providers (OpenAI-compatible)
└── skills/        # Built-in skills with SKILL.md + references/
```

### Key Design Decisions

1. **Core has zero app dependencies** — `mocode/core/` never imports from `mocode/app/`. The `Provider` is a Protocol, not a base class.

2. **Tools are factory functions** — `ReadTool()`, `WriteTool()` etc. return `Tool` instances. Some capture closures (e.g., `ReadTool(vfs=vfs)` binds the VFS). This keeps tools stateless while allowing dependency injection.

3. **AgentHook is class-based** — Override methods like `before_iteration`, `on_response`, `after_tools`, `on_tool_start`, `on_tool_complete`. `HookRunner` fans out to all hooks with error isolation (one hook's exception doesn't break others).

4. **Prompt uses Sections with priority** — `Prompt` builds XML or text from `Section` objects sorted by `(priority, name)`. Sections can hold strings, nested `Section` lists, or callables that receive context dicts.

5. **Workflow is a DAG engine** — Nodes have types: `task` (runs via agent), `router` (evaluates conditions, enables loops via back-edges), `map` (fans out to N child tasks). Dependencies are auto-inferred from `{nodes.id.*}` template references.

6. **Skills are directory-based** — Each skill has a `SKILL.md` with YAML frontmatter (name, description) and body content. Reference files are mounted into VirtualFS at `vfs://skill-name/path`.

7. **AGENTS.md is read at prompt build time** — Two locations: `~/.mocode/AGENTS.md` (global) and `./AGENTS.md` (project). Both are optional and merged into the `<agents>` section of the system prompt.

### Data Flow

```
User Input → CLIApp._dispatch() → Command or AgentLoop.chat()
                                        ↓
                              Provider.call() ← system_prompt + tools + messages
                                        ↓
                              Response (content/tool_calls/usage)
                                        ↓
                              HookRunner.on_response()
                                        ↓
                              Tool execution (parallel via asyncio.gather)
                                        ↓
                              HookRunner.after_tools()
                                        ↓
                              Loop continues or returns final response
```

### SubAgent Pattern

`SubAgent` creates an isolated `AgentLoop` with its own message history, sharing the parent's provider but potentially a filtered tool set. `sub_agent` and `compact` tools are blocked by default to prevent recursion.

## Code Conventions

- **`from __future__ import annotations`** — Used in every module for PEP 604 unions.
- **Dataclasses over Pydantic** — All models (`Config`, `Session`, `Node`, `Workflow`, `Response`, etc.) use `@dataclass`. Serialization is manual `to_dict()`/`from_dict()`.
- **Async-first** — `AgentLoop.chat()` and all hooks are async. Sync tools run via `asyncio.to_thread()`.
- **TYPE_CHECKING guard** — Import types used only for annotations under `if TYPE_CHECKING:` to avoid circular imports.
- **No global state** — All dependencies are constructor-injected. `CLIApp` is the composition root that wires everything.
- **Tool parameter dicts** — Tool parameters use `dict[str, dict]` with keys `type`, `description`, optional `default`/`optional`. Not JSON Schema — it's a simplified format.
- **Command system** — Commands use `Command` dataclass with subcommands, handlers, and auto-expansion of `/prefix:subname` aliases.
- **Spinner segments** — The spinner uses composable `Segment` objects with `Priority` (LOW/NORMAL/HIGH) and `Truncate` (TAIL/MIDDLE/NONE) strategies for responsive terminal display.

## Testing Patterns

- **MockProvider** — Create a mock provider with canned `Response` objects for deterministic agent tests.
- **`_make_app()` helper** — Patches `CLIApp._build_agent` and `SessionManager` to isolate CLIApp tests from real LLM/config.
- **Display capture** — Override `display.print` with a list append to capture output for assertions.
- **Async tests** — Use `@pytest.mark.asyncio` decorator (not `async def test_` without it).
- **Test classes** — Group related tests in classes (no `unittest.TestCase` inheritance — plain pytest classes).
