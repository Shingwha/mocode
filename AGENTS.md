## Project Overview

MoCode 0.3 — a lean agent framework for building custom AI agents with composable tools, prompts, and providers.

## Development Commands

```bash
uv sync                    # Install all dependencies (including dev)
uv sync --group dev        # Install dev dependencies only
uv run pytest              # Run all 228 tests
uv run pytest -xvs         # Fast fail with verbose output
uv run pytest tests/test_builder.py -xvs                              # Single file
uv run pytest tests/test_builder.py::TestBuilder::test_minimal_build  # Single test
uv run pytest -k "test_tool" -xvs                                     # Pattern match
uv run mocode              # Launch the interactive CLI
```

## Architecture

### Package layout

```
mocode/
  core/        # Framework core — no app-specific concerns
    builder.py    Agent fluent builder (composition root)
    agent.py      AgentLoop — LLM chat engine with tool execution loop
    provider.py   Provider Protocol + Response/ToolCall/Usage DTOs
    tool.py       Tool, ToolRegistry, ToolError
    hook.py       AgentHook base class, AgentHookContext, HookRunner
    prompt.py     Prompt, Section — section-based prompt builder
    skill.py      Skill, SkillManager — directory-based skill discovery
  providers/   # LLM provider implementations (just OpenAI-compatible)
  tools/       # Built-in tools (file ops, bash, search, fetch, etc.)
  hooks/       # Built-in hooks (CompactHook, GoalHook)
  prompts/     # System prompt definitions (app, compact, subagent)
  app/         # Application layer
    cli/         Interactive CLI: CLIApp, commands, display, theme
    config.py    Config — pure data + JSON load/save
    session.py   Session state machine with dirty tracking
tests/         # 228 tests, one file per module under test
```

### Core loop (AgentLoop)

The `AgentLoop._loop()` is the heart of the framework:

1. **before_iteration** hook → LLM call → **on_response** hook
2. If tool_calls: run tools in parallel (`asyncio.gather`) → append assistant+tool messages → **after_tools** hook
3. If no tool_calls: append assistant message → **after_iteration** hook
4. Repeat until no tool calls, `max_iterations` reached, or `continue_loop` is False

Tool errors propagate as `ToolError` — AgentLoop catches them and returns as tool result text. Cancellation via `asyncio.Task.cancel()` — `CancelledError` propagates.

### Key patterns

- **Protocol-driven Provider** — `Provider` is a `@runtime_checkable` Protocol. Any class with `model` property and `async call()` method works.
- **Lifecycle hooks with error isolation** — `HookRunner._dispatch()` iterates hooks in order; if one hook raises, the others still run. Each hook gets its own try/except.
- **Fluent builder** — `Agent().provider().prompt().tools().hooks().config().build()` returns `AgentLoop`.
- **Tool factories** — Tools are created via factory functions (`ReadTool()`, `BashTool()`, etc.) returning `Tool` instances with closures for config.
- **Per-tool-call hook context** — Concurrent tool calls each get a fresh `AgentHookContext` copy so `tool_name`/`tool_args`/`tool_error` don't clobber each other.
- **Section-based prompts** — `Prompt` uses `Section` objects with priority-based ordering, `enable()`/`disable()`, callable content with context injection, and nested sections. Two format options: `"text"` and `"xml"`.
- **CompactHook auto-trigger** — Monitors `prompt_tokens` usage; when it crosses 80% of context window (256K default), compresses messages via LLM summary.
- **GoalHook loop control** — Sets `ctx.continue_loop = True` to keep loop running. Tracks turn count and idle count; auto-pauses when limits are reached.
- **SubAgent isolation** — Each SubAgent creates its own `AgentLoop` with a filtered `ToolRegistry` (blocks `sub_agent` and `compact` from recursion). Gets a fresh `HookRunner`.
- **Session dirty tracking** — `SessionManager` tracks unsaved changes; saves only when dirty on interrupt.
- **Command pattern** — Slash commands implement the `Command` Protocol with `name`, `description`, `aliases`, and `async run()`. `CommandRegistry` supports name + alias lookup with leading-slash strip.

### Testing conventions

- One test class per component/feature (e.g., `TestBuilder`, `TestChat`, `TestTool`)
- `MockProvider` pattern: a simple class with `model` property + `async call()` that returns canned `Response` objects
- When tools are involved, tests register a `MockProvider` that returns responses in sequence — first a tool-call response, then a final response
- Async tests use `@pytest.mark.asyncio`
- Filesystem tests use pytest's built-in `tmp_path` fixture
- Complex mocking uses `unittest.mock.patch` and `MagicMock`
- Tests import from public API surface (`mocode.core`, `mocode.tools`, etc.)

## Code Conventions

- **`from __future__ import annotations`** — at the top of every module
- **No `classmethod` for DTO constructors** — use `@classmethod` named constructors (e.g., `Config.from_dict()`)
- **Tool param validation** — params dict values must be dicts with `type` and `description`; `Tool.__init__` raises `TypeError` otherwise
- **Tool schema naming** — params use `"default"` for default values, `"optional": True` as an alias for non-required (both prevent adding to `required` list)
- **No `cli/__init__.py` re-exports from sub-subpackages** — `app/cli/__init__.py` re-exports the public API; individual command files are imported only by `app.py`
- **Session filenames** — file store uses `session_{uuid4().hex[:12]}.json` format, stored under `sessions/{workdir_hash}/`
- **Config JSON format** — models can be plain strings or dicts with optional `extra_body`; `from_dict()` handles both
- **Tool descriptions serve double duty** — they become the tool's `description` in OpenAI schema AND the prompt text shown to the agent in `<tools>` section
