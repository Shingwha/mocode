# MoCode 0.3 — Architecture Overview

MoCode is a lean, async-first agentic coding assistant built in Python. The design philosophy is **layered isolation with zero circular dependencies**: a stable `core/` framework that never imports from the application layer, pluggable providers, class-based tools and hooks, and a DAG-based workflow engine. The entire system starts in ~180ms thanks to lazy imports everywhere.

---

## Layer Diagram

```
┌──────────────────────────────────────────────────────────────────────┐
│                           Application Layer                          │
│                                                                      │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌───────────────────┐   │
│  │  CLI      │  │ Workflow │  │ Session  │  │   Config / Utils  │   │
│  │ REPL +   │  │ DAG      │  │ Store +  │  │   Export +        │   │
│  │ Commands │  │ Engine   │  │ Manager  │  │   WorkflowRenderer│   │
│  └────┬─────┘  └────┬─────┘  └──────────┘  └───────────────────┘   │
│       │              │                                               │
│  ┌────┴──────────────┴──────────────────────────────────────────┐   │
│  │                        app/cli/                              │   │
│  │  CLIApp · Display · Input · Spinner · Hook · Theme · Commands│   │
│  └───────────────────────────┬──────────────────────────────────┘   │
└──────────────────────────────┼───────────────────────────────────────┘
                               │ imports (never circular)
┌──────────────────────────────┼───────────────────────────────────────┐
│                          Core Framework                               │
│                                                                      │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌───────────────────┐   │
│  │  Agent   │  │  Tool    │  │  Hook    │  │  Prompt / Section │   │
│  │  Loop +  │  │  + Tool  │  │  Agent   │  │  XML/text +       │   │
│  │  Builder │  │  Registry│  │  Hook    │  │  lazy callables   │   │
│  │  Config  │  │          │  │  Runner  │  │                   │   │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └───────────────────┘   │
│       │              │              │                                │
│  ┌────┴──────────────┴──────────────┴───────────────────────────┐   │
│  │  Provider Protocol · Skill · SkillManager · VirtualFS       │   │
│  │  SubAgent · Compact · ToolTimingTracker                      │   │
│  └──────────────────────────────────────────────────────────────┘   │
└──────────────────────────────┬───────────────────────────────────────┘
                               │ imported by core (Protocol only)
┌──────────────────────────────┼───────────────────────────────────────┐
│                     Extension Modules                                │
│                                                                      │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌───────────────────┐   │
│  │ tools/   │  │ hooks/   │  │ prompts/ │  │ providers/        │   │
│  │ ReadTool │  │ Compact  │  │ app.py   │  │ OpenAIProvider    │   │
│  │ WriteTool│  │ Hook     │  │ compact  │  │ (lazy openai SDK) │   │
│  │ EditTool │  │          │  │ subagent │  │                   │   │
│  │ GlobTool │  └──────────┘  └──────────┘  └───────────────────┘   │
│  │ GrepTool │  ┌──────────┐                                        │
│  │ BashTool │  │ skills/  │                                        │
│  │ FetchTool│  │ workflow │                                        │
│  │ SkillTool│  │ (skill)  │                                        │
│  │ SubAgent │  └──────────┘                                        │
│  │ PlanTool │                                                      │
│  └──────────┘                                                      │
└──────────────────────────────────────────────────────────────────────┘
```

**Dependency direction:** Extensions → Core → (Protocol only) Provider. The app layer imports from core and extensions. Core never imports from app, tools, hooks, prompts, providers, or skills.

---

## Core Framework (`mocode/core/`)

The core is a reusable framework with **zero application dependencies**. Every symbol is lazy-imported via `__getattr__` for fast startup.

| Module | Purpose | Key Types |
|--------|---------|-----------|
| `provider.py` | LLM interaction protocol + DTOs | `Provider` (protocol), `Response`, `ToolCall`, `Usage`, `with_retry()` |
| `tool.py` | Callable tools with schema generation | `Tool`, `ToolRegistry`, `ToolError` |
| `agent.py` | Agentic chat loop (LLM → tools → repeat) | `AgentLoop`, `AgentConfig`, `LoopResult` |
| `builder.py` | Fluent builder for `AgentLoop` | `Agent` (fluent API) |
| `hook.py` | Lifecycle hooks with error isolation | `AgentHook`, `HookRunner`, `IterationContext`, `ToolCallContext`, `CompactContext`, `ToolTimingTracker` |
| `prompt.py` | Section-based prompt builder | `Section`, `Prompt` (XML/text, priority ordering, lazy callables) |
| `skill.py` | Directory-based skill discovery | `Skill`, `SkillMetadata`, `SkillManager` |
| `virtualfs.py` | In-memory virtual file system | `VirtualFS` (Mapping protocol, `vfs://` URIs) |
| `subagent.py` | Isolated child agent | `SubAgent`, `SubAgentConfig` |
| `compact.py` | Context compression via LLM | `compact_messages()` |

The core defines **21 public symbols** exported through `mocode/core/__init__.py`.

---

## Application Layer (`mocode/app/`)

Owns everything above the framework: configuration, sessions, the interactive CLI, the workflow DAG engine, and all rendering.

| Module | Purpose |
|--------|---------|
| `config.py` | `Config`, `ProviderEntry`, `ModelEntry` — JSON persistence at `~/.mocode/config.json` |
| `session.py` | `Session`, `SessionStore`, `SessionManager` — conversation persistence under `~/.mocode/sessions/` |
| `export.py` | `render_session_md()` — Markdown export with YAML frontmatter |
| `utils.py` | JSON I/O, text shaping (`visible_width`, `ellipsize_middle`), tool call grouping |
| **`cli/`** | Interactive REPL with all UI components |
| `cli/app.py` | `CLIApp` — composition root that wires config, display, agent, commands, workflows |
| `cli/display.py` | `Display` — tool lifecycle, responses, usage, message rendering |
| `cli/input.py` | `Input` — `PromptSession`, paste handling, slash completion |
| `cli/hook.py` | `CLIDisplayHook` — bridges `AgentHook` → `Display` |
| `cli/spinner.py` | `SpinnerRunner` — 18 built-in animations, priority-based truncation |
| `cli/style.py` | `Style` — atomic visual descriptor |
| `cli/palette.py` | `C` (ANSI tokens), `ColorPalette` — semantic color mapping |
| `cli/styles.py` | `DisplayStyles`, `WorkflowStyles`, `SpinnerStyles` — component groups |
| `cli/theme.py` | `Theme` — composition of palette + style groups |
| `cli/formatter.py` | Workflow summary formatting |
| `cli/prompts.py` | `select`, `multiselect`, `confirm`, `text_input` — questionary wrappers |
| `cli/workflow_renderer.py` | `WorkflowRenderer` — event dispatch, DAG tree views, summaries |
| `cli/commands/` | Command system — `Command`, `CommandRegistry`, auto-expansion of `/prefix:sub` |
| **`workflow/`** | DAG-based multi-step task orchestration |
| `workflow/models.py` | `Node`, `Workflow`, `Route`, `NodeResult`, `FanItem` |
| `workflow/graph.py` | DAG validation + wave computation (topological sort) |
| `workflow/state.py` | `RunState` — mutable execution state |
| `workflow/events.py` | `WorkflowEvent` types (WaveReady, NodeStart, NodeDone, RouterMatch, etc.) |
| `workflow/runner.py` | `DAGRunner` — main coordinator |
| `workflow/executor.py` | `Executor` — `AgentLoop` instantiation per node |
| `workflow/scheduler.py` | `Scheduler` — wave announcement, router eval, back-edges, downstream activation |
| `workflow/hooks.py` | `_WorkflowNodeHook` — per-node hook with timing |
| `workflow/registry.py` | `WorkflowRegistry` — YAML file discovery |
| `workflow/run_store.py` | `WorkflowRunStore` — persist run results as JSON |

---

## Extension Modules

These modules implement the concrete tools, hooks, prompts, providers, and skills that the core framework references through protocols and abstractions.

**Tools** (`mocode/tools/`) — Class-based tools, each a `Tool` subclass:

| Tool | Purpose |
|------|---------|
| `ReadTool` | Read files (real + VFS, UTF-8/GBK, offset/limit) |
| `WriteTool` | Create/overwrite files with `mkdir -p` semantics |
| `EditTool` | Find-and-replace with uniqueness checks |
| `GlobTool` | File discovery by glob pattern (real + VFS) |
| `GrepTool` | Regex content search across directory trees |
| `BashTool` | Persistent bash session with env/cwd persistence |
| `FetchTool` | HTTP fetch with Markdown conversion |
| `SkillTool` | Load skills on demand → `skill(name="...")` |
| `SubAgentTool` | Spawn isolated child agents |
| `PlanTool` | Multi-step task planning |

**Hooks** (`mocode/hooks/`):

| Hook | Purpose |
|------|---------|
| `CompactHook` | Auto-triggers context compression at 80% token utilization |

**Prompts** (`mocode/prompts/`):

| Module | Purpose |
|--------|---------|
| `app.py` | `build_system_prompt()` — assembles all sections (guidelines, agents, environment, tools, skills, workflows) |
| `compact.py` | `summary_system_prompt`, `COMPACT_USER_TEMPLATE` — compression prompts |
| `subagent.py` | `build_subagent_prompt()` — layers sub-agent identity onto parent prompt |

**Providers** (`mocode/providers/`):

| Provider | Purpose |
|----------|---------|
| `OpenAIProvider` | OpenAI-compatible API client with message normalization, retry, `extra_body` forwarding, reasoning content support |

**Skills** (`mocode/skills/`):

| Skill | Purpose |
|-------|---------|
| `workflow` | DAG workflow design + execution instructions, YAML reference, task board pattern |

---

## Data Flow

### Interactive Session

```
User types input
    ↓
CLIApp._dispatch()
    ├── Slash command → CommandRegistry → CommandResult
    └── Chat prompt → _run_chat()
                        ↓
                    AgentLoop.chat(user_input)
                        ↓
                    _loop():
                        ├── hooks.before_iteration()     ← messages modifiable
                        ├── Provider.call(messages, ...)  ← LLM API call
                        ├── hooks.on_response()           ← observe response
                        ├── if tool_calls:
                        │     parallel execution via asyncio.gather()
                        │     hooks.on_tool_start() / on_tool_complete()
                        │     hooks.after_tools()          ← messages modifiable
                        │     → loop continues
                        └── else:
                              hooks.after_iteration()
                              → return final response
                        ↓
                    Display.text_response(response)
                    SessionManager.save(messages)
```

### Workflow Execution

```
DAGRunner.run(workflow)
    ↓
RunState.from_workflow()
    ↓ compute waves (topological sort), activate root nodes
    ↓
Main loop: while ready_queue and not should_stop():
    ↓
Scheduler.announce_waves() → WaveReadyEvent
    ↓
For each ready node:
    ├── router → Scheduler.evaluate_router() → regex match → activate targets / back-edge
    ├── task+each → fan_out_children() → concurrent AgentLoop per item
    └── task → Executor._exec_node() → AgentLoop.chat(full_prompt) → NodeResult
         ↓
    _record_node_done() → update state.context, emit events, activate_downstream
         ↓
    Scheduler.activate_downstream() → decrement pending_deps, enqueue ready nodes
```

### Sub-Agent Isolation

```
Parent AgentLoop
    ↓ SubAgentTool called
SubAgent.create()
    ↓
    ├── Shared: Provider (same LLM backend)
    ├── Shared: System prompt (parent's prompt + sub-agent identity layer)
    ├── Inherited: ToolRegistry (filtered — sub_agent + compact blocked by default)
    ├── Isolated: Message history (fresh copy)
    └── Isolated: HookRunner (new instance)
         ↓
    SubAgent._loop() — same AgentLoop mechanics, independent state
         ↓
    Return result to parent → appended as tool result
```

---

## Key Design Decisions

### 1. Zero Circular Dependencies

`core/` never imports from `app/`, `tools/`, `hooks/`, `prompts/`, `providers/`, or `skills/`. The `Provider` is a `Protocol`, not a base class. This allows:
- Testing core primitives in isolation
- Swapping providers without touching framework code
- Reusing `AgentLoop` outside the CLI (workflow nodes, sub-agents, custom integrations)

### 2. Lazy Imports Everywhere

Both `mocode/core/__init__.py` and `mocode/tools/__init__.py` use `__getattr__` + `_LAZY_IMPORTS` dicts. Heavy modules (`openai`, `prompt_toolkit`, `questionary`, `asyncio`) are only imported on first access. This reduced startup from ~1900ms to ~180ms.

### 3. Dataclasses, Not Pydantic

All models (`Config`, `Session`, `Node`, `Workflow`, `Response`, `ToolCall`, `Usage`) use `@dataclass`. Serialization is manual `to_dict()` / `from_dict()`. This eliminates a runtime dependency and keeps the import graph small.

### 4. Class-Based Tools and Hooks

Tools are `Tool` subclasses that capture dependencies via constructor injection (`ReadTool(vfs=vfs)`). Hooks are `AgentHook` subclasses with no-op defaults — override only what you need. `HookRunner` provides error isolation: one hook's exception never breaks another.

### 5. Prompt Sections with Priority

`Prompt` builds XML or text from `Section` objects sorted by `(priority, name)`. The system prompt orders sections to maximize LLM prefix cache hits: `guidelines(10)` → `agents(20)` → `environment(30)` → `vfs(35)` → `tools(40)` → `skills(50)` → `workflows(60)`. Sections can hold strings, nested lists, or callables for deferred rendering.

### 6. Workflow = DAG + AgentLoop per Node

Each task node runs as an in-process `AgentLoop` — no subprocess. Router nodes evaluate regex conditions against dependency outputs. Back-edges enable loops with `max` iteration limits. The runner is split into three concerns: `DAGRunner` (coordinator), `Executor` (loop instantiation), `Scheduler` (waves, routing, activation).

### 7. Skills Are Directory-Based Discovery

Each skill is a folder with `SKILL.md` (YAML frontmatter + Markdown body) and optional reference files. Reference files are mounted into `VirtualFS` at `vfs://skill-name/` and accessed through the standard `read`/`glob`/`grep` tools. This keeps the system prompt compact while giving the agent deep domain knowledge on demand.

### 8. Instance-Scoped Tool Registry

`ToolRegistry` is per-agent, not global. Tools are registered during `CLIApp._build_agent()`. `SubAgentTool` creates a derived registry with `registry.derived(exclude={"sub_agent", "compact"})` to give child agents a filtered tool set. `PlanTool` is registered after `PlanState` initialization to avoid circular imports.

### 9. Visual System Composition

`Style` (atomic descriptor) → `ColorPalette` (semantic → ANSI mapping) → `DisplayStyles` / `WorkflowStyles` / `SpinnerStyles` (component groups) → `Theme` (composition wrapper). Components receive only the `*Styles` subset they need, not the full theme. Re-skinning requires only swapping the palette.

---

## Module File Counts

| Layer | Modules | Key Files |
|-------|---------|-----------|
| `core/` | 11 | agent, builder, provider, tool, hook, prompt, skill, virtualfs, subagent, compact |
| `app/` | 40 | cli/app, cli/display, cli/spinner, cli/commands/*, workflow/runner, workflow/executor, workflow/scheduler, config, session |
| `tools/` | 10 | file, glob, grep, bash, fetch, skill, subagent, plan |
| `hooks/` | 2 | compact |
| `prompts/` | 4 | app, compact, subagent |
| `providers/` | 2 | openai |
| `skills/` | 2 | workflow |

---

## Per-Layer Documentation

| Document | Coverage |
|----------|----------|
| [`docs/hooks.md`](hooks.md) | Hook system (`AgentHook`, `HookRunner`, context objects, `CompactHook`) and Tool system (`Tool`, `ToolRegistry`, built-in tools, custom tools) |
| [`docs/prompts.md`](prompts.md) | Prompt building (`Section`, `Prompt`, priority ordering, XML/text modes, callable sections) and prompt modules (`app.py`, `compact.py`, `subagent.py`) |
| [`docs/providers.md`](providers.md) | Provider protocol, DTOs (`Response`, `ToolCall`, `Usage`), `OpenAIProvider` implementation, adding custom providers |
| [`docs/skills.md`](skills.md) | Skill system (`Skill`, `SkillManager`, `VirtualFS` integration, SKILL.md format, built-in skills, creating custom skills) |

---

*Generated by the `write-docs` workflow · MoCode 0.3*
