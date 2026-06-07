# MoCode 0.3 — Development Guide

## Dev Commands

```bash
# Install dependencies (uses uv + hatchling)
# Always use uv for python!!!
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
│   ├── virtualfs.py   # VirtualFS — in-memory vfs:// file system
│   ├── subagent.py    # SubAgent — isolated child agent (moved from tools/)
│   └── compact.py     # Context compression logic (moved from tools/)
├── app/           # Application layer
│   ├── cli/           # CLIApp, Display, Input, Spinner, Commands
│   │   └── commands/  # Command modules (builtin, workflow, session, model, plan, skill, misc)
│   ├── config.py      # Config — JSON load/save, multi-provider
│   ├── session.py     # Session + SessionManager + SessionStore
│   ├── export.py      # Session export (JSON/Markdown)
│   ├── utils.py       # Shared utilities (read_json, write_json, etc.)
│   └── workflow/      # DAG engine (split from monolithic runner.py)
│       ├── models.py      # Node, Workflow, Route, NodeResult data models
│       ├── graph.py       # DAG validation + wave computation (topological sort)
│       ├── state.py       # RunState — mutable state for one workflow execution
│       ├── events.py      # WorkflowEvent types (NodeStart, NodeDone, Router, Loop, Map, etc.)
│       ├── runner.py      # DAGRunner — main coordinator (delegates to executor/scheduler)
│       ├── executor.py    # Executor — AgentLoop instantiation + map result helpers
│       ├── scheduler.py   # Scheduler — wave announcement, router eval, back-edges, downstream activation
│       ├── hooks.py       # _WorkflowNodeHook
│       ├── registry.py    # WorkflowRegistry — YAML discovery
│       ├── cli.py         # CLI entry point for /workflow commands
│       └── run_store.py   # WorkflowRunStore — persist run results
├── tools/         # Built-in tools (class-based, subclass Tool)
│   ├── file.py        # ReadTool, WriteTool, EditTool
│   ├── glob.py        # GlobTool
│   ├── grep.py        # GrepTool
│   ├── bash.py        # BashTool
│   ├── fetch.py       # FetchTool
│   ├── skill.py       # SkillTool
│   ├── subagent.py    # SubAgentTool
│   ├── plan.py        # PlanTool
│   └── utils.py       # Shared tool utilities
├── hooks/         # Built-in hooks
│   └── compact.py     # CompactHook — auto-trigger context compression
├── prompts/       # System prompts
│   ├── app.py         # Main system prompt builder (reads AGENTS.md, tools, skills)
│   ├── compact.py     # Compression prompt templates
│   └── subagent.py    # Sub-agent system prompt
├── providers/     # LLM providers (OpenAI-compatible)
│   └── openai.py      # OpenAIProvider
└── skills/        # Built-in skills with SKILL.md + references/
```

### Key Design Decisions

1. **Core has zero app dependencies** — `mocode/core/` never imports from `mocode/app/`. The `Provider` is a Protocol, not a base class.

2. **Tools are class-based** — `ReadTool(Tool)`, `WriteTool(Tool)` etc. are subclasses of `Tool`. Constructor captures dependencies (e.g. `ReadTool(vfs=vfs)` binds the VFS). Call `super().__init__(name, description, params, func)` where `func` is typically `self._execute`.

3. **AgentHook is class-based** — Override methods like `before_iteration`, `on_response`, `after_tools`, `on_tool_start`, `on_tool_complete`, `on_compact`. `HookRunner` fans out to all hooks with error isolation (one hook's exception doesn't break others). Methods are dispatched via `__getattr__` — only `_METHODS` frozenset members are forwarded.

4. **Prompt uses Sections with priority** — `Prompt` builds XML or text from `Section` objects sorted by `(priority, name)`. Sections can hold strings, nested `Section` lists, or callables that receive context dicts. The system prompt (`prompts/app.py`) orders sections by priority: guidelines(10) → agents(20) → environment(30) → vfs(35) → tools(40) → skills(50) → workflows(60) to maximize LLM prefix cache hit rate.

5. **Workflow is a DAG engine with three node types** — `task` (runs via in-process AgentLoop), `router` (evaluates regex conditions against dependency outputs; supports loops via back-edges with `max` iteration limits), `map` (fans out to N child tasks using `items` template). Dependencies are auto-inferred from `{nodes.id.*}` template references.

6. **Workflow runner is split into three concerns** — `DAGRunner` (coordinator: main loop, map fan-out, node completion), `Executor` (AgentLoop creation, node context header, map finalization), `Scheduler` (wave announcement, router evaluation, back-edge handling, downstream activation). This split happened because runner.py grew to 600+ lines.

7. **Skills are directory-based** — Each skill has a `SKILL.md` with YAML frontmatter (name, description) and body content. Reference files are mounted into VirtualFS at `vfs://skill-name/path`.

8. **AGENTS.md is read at prompt build time** — Two locations: `~/.mocode/AGENTS.md` (global) and `./AGENTS.md` (project). Both are optional and merged into the `<agents>` section of the system prompt.

9. **SubAgent and Compact live in core/** — `SubAgent` (`core/subagent.py`) creates an isolated `AgentLoop` with its own message history, sharing the parent's provider but with a filtered tool set (`sub_agent` and `compact` are blocked by default). `compact_messages` (`core/compact.py`) generates LLM summaries; extracted from `tools/` to break the `hooks → tools → prompts` dependency chain.

10. **Lazy imports everywhere** — Both `mocode/core/__init__.py` and `mocode/tools/__init__.py` use `__getattr__` + `_LAZY_IMPORTS` dicts to defer module loading. This reduced startup from ~1900ms to ~180ms.

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

### Workflow Execution Flow

```
CLIApp → DAGRunner.run()
              ↓
         RunState.from_workflow() → compute waves, activate root nodes
              ↓
         Main loop: while ready_queue and not should_stop()
              ↓
         Scheduler.announce_waves() → emit WaveReadyEvent
              ↓
         For each ready node:
           router → Scheduler.evaluate_router() → regex match routes → activate targets / back-edge
           map    → parse items → fan_out_children() → concurrent AgentLoop per item
           task   → Executor._exec_node() → AgentLoop.chat(full_prompt) → NodeResult
              ↓
         _record_node_done() → update state.context, persist, emit events, activate_downstream
              ↓
         Scheduler.activate_downstream() → decrement pending_deps, enqueue ready nodes
```

## Code Conventions

- **`from __future__ import annotations`** — Used in every module for PEP 604 unions.
- **Dataclasses over Pydantic** — All models (`Config`, `Session`, `Node`, `Workflow`, `Response`, etc.) use `@dataclass`. Serialization is manual `to_dict()`/`from_dict()`.
- **Async-first** — `AgentLoop.chat()` and all hooks are async. Sync tools run via `asyncio.to_thread()`.
- **TYPE_CHECKING guard** — Import types used only for annotations under `if TYPE_CHECKING:` to avoid circular imports.
- **No global state** — All dependencies are constructor-injected. `CLIApp` is the composition root that wires everything.
- **Tool parameter dicts** — Tool parameters use `dict[str, dict]` with keys `type`, `description`, optional `default`/`optional`. Not JSON Schema — it's a simplified format. Defaults propagate into `to_schema()`.
- **Tool subclasses pass `self._execute` as func** — e.g. `super().__init__(name="read", description=..., params=..., func=self._execute)`. The `_execute` method contains the actual logic.
- **Command system** — Commands use `Command` dataclass with subcommands, handlers, and auto-expansion of `/prefix:subname` aliases. Commands are split across `mocode/app/cli/commands/` by domain.
- **Spinner segments** — The spinner uses composable `Segment` objects with `Priority` (LOW/NORMAL/HIGH) and `Truncate` (TAIL/MIDDLE/NONE) strategies for responsive terminal display.
- **Standard library preference** — `urllib.request` over `httpx`, `asyncio.to_thread` for sync wrappers. Only 5 runtime dependencies: `openai`, `pyyaml`, `prompt-toolkit`, `questionary`, `pyperclip`.

## Testing Patterns

- **MockProvider** — Create a mock provider with canned `Response` objects for deterministic agent tests.
- **`_make_app()` helper** — Patches `CLIApp._build_agent` and `SessionManager` to isolate CLIApp tests from real LLM/config.
- **Display capture** — Override `display.print` with a list append to capture output for assertions.
- **Async tests** — Use `@pytest.mark.asyncio` decorator (not `async def test_` without it).
- **Test classes** — Group related tests in classes (no `unittest.TestCase` inheritance — plain pytest classes).
