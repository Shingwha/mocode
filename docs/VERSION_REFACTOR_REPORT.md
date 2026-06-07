# MoCode 0.3 — Version Refactor Report

> This document summarizes all significant changes from v0.2 to v0.3, including new features, refactoring efforts, design philosophy shifts, and lessons learned.

---

## Table of Contents

1. [Version Overview](#version-overview)
2. [New Core Features](#new-core-features)
3. [Architecture Refactoring](#architecture-refactoring)
4. [Performance Optimizations](#performance-optimizations)
5. [Feature Removals & Design Rollbacks](#feature-removals--design-rollbacks)
6. [Bug Fixes](#bug-fixes)
7. [Design Philosophy Evolution](#design-philosophy-evolution)
8. [Development Lessons Learned](#development-lessons-learned)

---

## Version Overview

**v0.3** is a large-scale refactor comprising **189 commits** across:

| Category | Count | Description |
|----------|-------|-------------|
| New Features | ~25 | Workflow, Skills, VirtualFS, Plan, etc. |
| Refactoring | ~80 | Module splits, code extraction, layering |
| Bug Fixes | ~35 | UI, concurrency, path handling, etc. |
| Performance | ~10 | Startup time, lazy loading |
| Documentation | ~10 | AGENTS.md, skill docs, architecture |
| Removals/Rollbacks | ~15 | Redundant code cleanup, feature removal |

---

## New Core Features

### 1. Workflow Engine (DAG-Based Workflow System)

**Built a complete YAML-driven workflow engine from scratch:**

```
mocode/app/workflow/
├── __init__.py       # Module entry point
├── models.py         # Data models (Node, Workflow, Route, etc.)
├── graph.py          # DAG graph construction & validation
├── state.py          # Runtime state management
├── events.py         # Event system
├── runner.py         # Main runner
├── executor.py       # Node executor (extracted from runner)
├── scheduler.py      # DAG scheduler (extracted from runner)
├── hooks.py          # Workflow hooks
├── registry.py       # Workflow registry
├── cli.py            # CLI command entry point
├── run_store.py      # Run result persistence
└── renderer.py       # Renderer (extracted from Display)
```

**Evolution:**

1. **Phase/Lane/Step model** → Found too complex
2. **DAG node graph model** → Current approach; supports task/router/map node types
3. **Subprocess execution** → **In-process AgentLoop** → Better resource control and error handling

**Key capabilities:**
- Auto-dependency inference from `{nodes.X.*}` template references
- Router nodes: conditional routing + loop support (back-edges)
- Map nodes: fan-out/fan-in parallel execution
- Ctrl+C cancellation: saves partial results, graceful exit

### 2. Skills System

**Directory-based skill discovery and loading:**

```
mocode/skills/
├── workflow/         # Built-in skill
│   ├── SKILL.md      # YAML frontmatter + body content
│   └── references/   # Reference documentation
└── ...
```

**Evolution:**
1. Initial: Inline in code
2. Mid-term: `builtin_skills.py` centralized management
3. Final: **Package data pattern** + VirtualFS mounting

### 3. VirtualFS (Virtual File System)

**In-memory read-only file system for skill content mounting:**

```python
# mocode/core/virtualfs.py
class VirtualFS(collections.abc.Mapping):
    def __getitem__(self, path: str) -> str: ...
    def __len__(self) -> int: ...
    def __iter__(self) -> Iterator[str]: ...
```

**Design decisions:**
- Implements `Mapping` protocol instead of custom API → more Pythonic
- Transparent support for `read`/`glob`/`grep` tools
- `vfs://` path prefix distinguishes virtual from real files

### 4. Plan System

**LLM-driven plan writing and execution:**

```
/plan         → Ask LLM to write a .md plan file
/plan:start   → Execute the plan
/plan:copy    → Copy plan to clipboard
```

**Workflow:**
1. User describes a task
2. LLM writes `~/.mocode/plans/xxx.md`
3. Call `/plan:start` to execute

### 5. Session Management

**Complete session lifecycle management:**

- Auto-save: `~/.mocode/sessions/xxx.json`
- Resume: `/resume [session_id|index|path]`
- Export: `/export [json|md]`
- Clear: `/clear`

**Evolution:**
1. Initial: Simple JSON serialization
2. Mid-term: `SessionStore` Protocol + `FileSessionStore`
3. Final: Removed Protocol (only one implementation), direct `SessionStore` class

### 6. Command System Refactoring

**From scattered command handlers to unified Command dataclass:**

```python
# Before: Multiple independent handler functions
def handle_workflow(args): ...
def handle_export(args): ...

# After: Unified Command dataclass + subcommand routing
@dataclass
class Command:
    name: str
    description: str
    subcommands: dict[str, SubCommand]
    handler: Callable
```

**Simplification:**
- Workflow commands: `7 subcommands → 4 subcommands`
- Removed `run-bg`, merged `result`/`runs` into `status`

---

## Architecture Refactoring

### 1. Layered Architecture Established

**Strict core/application separation:**

```
mocode/
├── core/          # Framework primitives (zero app dependencies)
│   ├── agent.py       # AgentLoop
│   ├── builder.py     # Agent builder
│   ├── provider.py    # Provider Protocol
│   ├── tool.py        # Tool + ToolRegistry
│   ├── hook.py        # AgentHook + HookRunner
│   ├── prompt.py      # Prompt + Section
│   ├── skill.py       # Skill + SkillManager
│   ├── virtualfs.py   # VirtualFS
│   ├── subagent.py    # SubAgent engine (moved from tools/)
│   └── compact.py     # Compression logic (moved from tools/)
├── app/           # Application layer
│   ├── cli/           # CLI interaction
│   ├── config.py      # Configuration management
│   ├── session.py     # Session management
│   ├── export.py      # Session export (split from session.py)
│   ├── utils.py       # Shared utility functions
│   └── workflow/      # Workflow engine
└── tools/         # Built-in tools
```

**Key principles:**
- `core/` never imports from `app/`
- `Provider` is a Protocol, not a base class
- Tools created via factory functions/classes with closure-captured dependencies

### 2. Tool Class Refactoring

**From factory functions to tool subclasses (two attempts):**

```
Attempt 1: Factory functions → Tool subclasses (failed, rolled back)
Attempt 2: Convert to Tool subclasses again (succeeded)
```

**Final approach:**

```python
# Before: Closure capture
def ReadTool(vfs: VirtualFS) -> Tool:
    def run(args):
        return vfs[args["path"]]
    return Tool("read", ..., run)

# After: Class inheritance
class ReadTool(Tool):
    def __init__(self, vfs: VirtualFS):
        self._vfs = vfs
        super().__init__("read", ...)
    
    async def run(self, args):
        return self._vfs[args["path"]]
```

**Merge optimizations:**
- `AppendTool` → merged into `WriteTool` (added `append` parameter)
- `CompactTool` → removed, logic moved to `core/compact.py`

### 3. Workflow Engine Split

**From monolithic file to separation of concerns:**

```
runner.py (600+ lines) → 
├── executor.py    # Node execution logic
├── scheduler.py   # DAG scheduling logic
├── hooks.py       # _WorkflowNodeHook
└── runner.py      # Main coordinator (trimmed to ~150 lines)
```

### 4. CLI Module Split

**From single main.py to modular structure:**

```
main.py (2000+ lines) →
├── app.py              # CLIApp main class
├── display.py          # Display logic
├── hook.py             # CLI hooks
├── spinner.py          # Spinner animation
├── input.py            # Input handling
├── textutils.py        # Text utilities
├── formatter.py        # Formatting utilities
├── theme.py            # Theme definitions
└── commands/           # Command modules
    ├── builtin.py      # Built-in commands
    ├── connect.py      # /connect command
    ├── workflow.py     # /workflow command
    ├── session.py      # /session command
    ├── model.py        # /model command
    ├── plan.py         # /plan command
    └── misc.py         # Miscellaneous commands
```

### 5. Search Tool Split

**From single search.py to independent modules:**

```
search.py (495 lines) →
├── glob.py     # Glob tool
├── grep.py     # Grep tool
└── utils.py    # Shared constants and utilities
```

---

## Performance Optimizations

### 1. Startup Time Optimization

**From ~1900ms to ~180ms (10× improvement):**

| Optimization | Time Saved | Method |
|-------------|-----------|--------|
| Lazy imports | ~800ms | `__getattr__` lazy loading |
| Remove httpx | ~500ms | Switch to `urllib.request` |
| Lazy tool initialization | ~200ms | Create on first call |
| Workflow lazy parsing | ~100ms | Parse YAML on first access |

**Key code:**

```python
# mocode/tools/__init__.py
def __getattr__(name: str):
    if name == "ReadTool":
        from .file import ReadTool
        return ReadTool
    # ...
```

### 2. Dependency Reduction

**Removed httpx and 6 transitive dependencies:**

```
httpx → urllib.request + asyncio.to_thread
Removed: httpcore, anyio, h11, certifi, idua, sniffio
```

---

## Feature Removals & Design Rollbacks

### 1. Goal Feature (Ultimately Removed)

**Evolution:**

```
v0.2: GoalEvaluator (complex goal evaluator)
  ↓ Simplified
v0.3 early: GoalHook + GoalTool (simplified goal management)
  ↓ Removed
v0.3 final: Task Board Workflow replaces it
```

**Removal reasons:**
- Workflow system's Task Board pattern is more flexible
- Goal feature overlaps with Workflow feature
- Simplifies core code

### 2. Fetch Endpoint Rollback

**Attempt and rollback:**

```
markdown.new → r.jina.ai (Jina Reader API) → Rolled back to markdown.new
```

**Reasons:**
- Jina API requires API key
- Incomplete test coverage
- Chose to keep it simple

### 3. Variable-Width Spinner Presets

**Added then removed:**

```
Added 5 variable-width presets (grow, typewriter, train, snake, progress)
  ↓ Removed
Retained truncation algorithm, removed specific presets
```

**Reasons:**
- Variable-width animations don't work well on all terminals
- Simplified built-in style collection
- Underlying truncation algorithm kept for extension

### 4. Other Removals

| Removed Item | Reason |
|-------------|--------|
| `SessionStore` Protocol | Only one implementation — over-abstraction |
| `tools/search.py` | Backward-compat shim with no consumers |
| `WorkflowRegistry` cache | Dynamic discovery is simpler |
| `_tick` method | Duplicated `_tick_idle` |

---

## Bug Fixes

### Critical Fixes

| Issue | Fix |
|-------|-----|
| Commands not registered at startup | Restored eager agent creation |
| HookRunner stale cache | Removed cache; create fresh closures each time |
| Tool schema missing defaults | Propagate defaults in `to_schema()` |
| Directory read error | Graceful degradation, list directory contents |
| Shell injection vulnerability | Escape environment variable values |
| Workflow Ctrl+C exits app | Now cancels workflow instead |
| Workflow spinner format misaligned | Aligned with CLI format |
| Circular imports | Extracted shared modules to `app/utils.py` |
| Backend edge reset ordering | Process in two passes |

---

## Design Philosophy Evolution

### 1. From "Monolithic" to "Compositional"

**Early design:**
- Large monolithic classes (main.py 2000+ lines)
- Functionality cohesive in a single file

**Final design:**
- Small composable modules
- Single responsibility
- Dependency injection

### 2. From "Abstract" to "Pragmatic"

**Example: SessionStore**

```python
# Before: Protocol + implementation
class SessionStore(Protocol):
    def save(self, session: Session) -> None: ...
    def load(self, session_id: str) -> Session: ...

class FileSessionStore:
    def save(self, session: Session) -> None: ...
    def load(self, session_id: str) -> Session: ...

# After: Direct class usage
class SessionStore:
    def save(self, session: Session) -> None: ...
    def load(self, session_id: str) -> Session: ...
```

**Lessons:**
- Avoid over-abstraction
- Use Protocols only when multiple implementations exist
- YAGNI principle (You Aren't Gonna Need It)

### 3. From "Feature Stacking" to "Lean Core"

**Example: Goal Feature**

```
Initial: Add GoalEvaluator (complex)
Mid-term: Simplify to GoalHook + GoalTool
Final: Remove, replace with Workflow
```

**Lessons:**
- Consider overlap with existing features before adding
- Keep the core lean
- Extend via composition, not inheritance

### 4. From "Perfect Design" to "Iterative Refinement"

**Example: Workflow Engine**

```
Phase 1: Phase/Lane/Step model (too complex)
Phase 2: DAG node graph (clean)
Phase 3: Split runner (maintainable)
```

**Lessons:**
- Build a minimum viable version first
- Iterate based on actual usage
- Don't over-design

---

## Development Lessons Learned

### 1. When to Split Modules

**Rule of thumb:** Consider splitting when a file exceeds 300 lines.

| File Size | Action |
|-----------|--------|
| < 100 lines | Keep as-is |
| 100–300 lines | Monitor, extract when needed |
| 300–500 lines | Actively split |
| > 500 lines | Must split |

### 2. Dependency Management Principles

**Rule of thumb:** Prefer the standard library.

```python
# ❌ Import third-party library
import httpx

# ✅ Use standard library
import urllib.request
import asyncio

async def fetch(url: str) -> str:
    def _fetch():
        with urllib.request.urlopen(url) as resp:
            return resp.read().decode()
    return await asyncio.to_thread(_fetch)
```

**Benefits:**
- Fewer dependency conflicts
- Faster startup
- Easier debugging

### 3. Incremental Refactoring

**Rule of thumb:** Small commits, stay reversible.

```
1. Add new code first (keep old code)
2. Update consumers to use new code
3. Confirm tests pass
4. Remove old code
```

**Example: Tool class conversion**

```python
# Step 1: Add new class
class ReadTool(Tool):
    ...

# Step 2: Update __init__.py exports
# Step 3: Run tests
# Step 4: Remove old factory function
```

### 4. Test-Driven Refactoring

**Rule of thumb:** Ensure test coverage before refactoring.

```bash
# Before refactoring
uv run pytest  # Ensure all pass

# Perform refactoring
# ...

# After refactoring
uv run pytest  # Confirm again
```

**Key:** 384 tests serve as a safety net.

### 5. Avoid Over-Abstraction

**Anti-pattern:**

```python
# ❌ Over-abstraction
class AbstractSessionStore(Protocol):
    def save(self, session: Session) -> None: ...
    def load(self, session_id: str) -> Session: ...
    def delete(self, session_id: str) -> None: ...
    def list(self) -> list[str]: ...

class FileSessionStore(AbstractSessionStore):
    # Only one implementation...
```

**Good pattern:**

```python
# ✅ Pragmatic design
class SessionStore:
    """File-based session storage."""
    def save(self, session: Session) -> None: ...
    def load(self, session_id: str) -> Session: ...
```

### 6. Documentation in Sync with Code

**Rule of thumb:** Update docs alongside important changes.

```
Code change → Update AGENTS.md → Update SKILL.md → Update README
```

**Key docs:**
- `AGENTS.md`: Architecture, conventions, development guide
- `SKILL.md`: Skill usage instructions
- `README.md`: Project overview

### 7. Performance Optimization Strategy

**Priority order:**

1. **Lazy loading**: `__getattr__` lazy imports
2. **Reduce dependencies**: Replace third-party with stdlib
3. **Cache**: Only when necessary (avoid premature optimization)
4. **Async**: Use asyncio for IO-bound operations

**Example: Startup optimization**

```python
# Before: Eagerly import all modules
from mocode.tools import ReadTool, WriteTool, ...
from mocode.core import AgentLoop, ...

# After: Lazy imports
def __getattr__(name: str):
    if name == "ReadTool":
        from .file import ReadTool
        return ReadTool
```

---

## Recommendations for Future Development

### 1. Maintain Single Responsibility

- Each module does one thing
- Avoid circular dependencies
- Use dependency injection

### 2. Test Coverage First

- New features must have tests
- Ensure tests pass before refactoring
- Run full test suite regularly

### 3. Incremental Evolution

- Small commits, frequent pushes
- Develop important features on branches
- Keep main branch stable

### 4. Timely Documentation Updates

- Update AGENTS.md with architecture changes
- Add usage examples for new features
- Record important decisions in commit messages

### 5. Continuous Performance Attention

- Startup time as a key metric
- Avoid introducing unnecessary dependencies
- Use lazy loading to optimize startup

---

## Appendix: Key Commit List

### Architecture Refactoring
- `3acdd5b`: split builtin.py into domain modules
- `b15ed92`: split tools/search.py into glob.py + grep.py
- `f006e76`: extract DAG scheduling logic from runner.py
- `0ed15c3`: extract node execution logic from runner.py
- `4e8cab8`: move SubAgent engine from tools/ to core/

### New Features
- `870596e`: add YAML-driven workflow engine
- `e9020b9`: add VirtualFS for in-memory skill content
- `45dd88d`: add plan system
- `775eb95`: register /skill:<name> commands
- `383f238`: add map node type for fan-out/fan-in

### Performance Optimizations
- `71998da`: lazy imports reduce startup from ~1.5s to ~0.18s
- `7dfa46a`: replace httpx with stdlib urllib
- `a03a1a9`: optimize startup time (~1900ms → ~400ms)

### Feature Removals
- `0862bc6`: remove Goal feature (GoalHook + GoalTool)
- `110cf6b`: remove dead code: delete mocode/tools/search.py
- `aa84972`: merge AppendTool into WriteTool

### Design Rollbacks
- `7112f0d`: Revert "convert all tool factories to Tool subclasses"
- `57c5c0c`: Revert "replace markdown.new with Jina Reader API"

---

**Document version:** v1.0  
**Last updated:** 2026-06-07  
**Applicable version:** MoCode 0.3.0
