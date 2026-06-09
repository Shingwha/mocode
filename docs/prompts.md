# MoCode Prompt System — Developer Reference

> Covers the prompt building infrastructure in `core/prompt.py` and the three prompt modules in `prompts/`: `app.py` (system prompt assembly), `compact.py` (compression templates), and `subagent.py` (delegated sub-agent prompts).

---

## Table of Contents

1. [Overview](#overview)
2. [Section — Atomic Prompt Unit](#section--atomic-prompt-unit)
3. [Prompt — Section-Based Builder](#prompt--section-based-builder)
4. [XML vs Text Mode](#xml-vs-text-mode)
5. [Callable Sections](#callable-sections)
6. [Prompts Layer](#prompts-layer)
   - [app.py — System Prompt Assembly](#apppy--system-prompt-assembly)
   - [compact.py — Compression Prompts](#compactpy--compression-prompts)
   - [subagent.py — Delegated Sub-Agent Prompts](#subagentpy--delegated-sub-agent-prompts)
7. [Writing Custom Prompts](#writing-custom-prompts)
8. [Design Notes](#design-notes)

---

## Overview

MoCode uses a **section-based prompt building system** to compose LLM system prompts from structured, prioritized blocks. Each block is a `Section` dataclass; a `Prompt` holds a collection of sections and renders them as either XML or plain text.

The prompt system lives in two layers:

| File | Role |
|------|------|
| `mocode/core/prompt.py` | Defines the `Section` dataclass and `Prompt` builder — the core primitives. |
| `mocode/prompts/app.py` | Builds the main agent system prompt from tools, skills, environment, and AGENTS.md. |
| `mocode/prompts/compact.py` | Provides the compression system prompt and user template for context summarization. |
| `mocode/prompts/subagent.py` | Layers sub-agent identity and guidelines onto the parent agent's system prompt. |
| `mocode/prompts/__init__.py` | Re-exports all public prompt symbols for convenient imports. |

The `Prompt` class is also used directly by the `Agent` fluent builder (`core/builder.py`) and by various internal components that need structured prompt composition.

---

## Section — Atomic Prompt Unit

```python
@dataclass
class Section:
    name: str                            # tag name (e.g. "guidelines", "tools")
    content: Content                     # str | list[Section] | Callable[[dict], str]
    priority: int = 0                    # lower = earlier in output
    enabled: bool = True                 # skip disabled sections during build
    attrs: dict[str, str] = field(...)   # XML attributes (e.g. {"name": "read"})
```

`Content` is a union type:

```python
Content = str | list["Section"] | Callable[[dict[str, Any]], str]
```

This means a section's content can be:

| Content type | Behavior |
|---|---|
| `str` | Rendered as-is |
| `list[Section]` | Recursively rendered as child sections |
| `Callable[[dict], str]` | Called with the prompt's context dict at build time |

### Section Naming

Section names become XML tag names in XML mode. Use lowercase, hyphenated names:

```python
Section("guidelines", "Be concise", priority=10)
Section("agent", content, attrs={"source": "global", "path": "/path/to/AGENTS.md"})
```

### Attributes

The `attrs` dict adds XML attributes to the rendered tag. In XML mode:

```xml
<agent source="global" path="C:\Users\user\.mocode\AGENTS.md">
  ...content...
</agent>
```

In text mode, attributes are shown parenthetically: `agent (source=global, path=...)`.

### Nested Sections

A section can contain a list of child sections:

```python
tools_section = Section("tools", [
    Section("tool", "Read a file", attrs={"name": "read"}),
    Section("tool", "Write a file", attrs={"name": "write"}),
], priority=40)
```

In XML mode, this renders as:

```xml
<tools>
  <tool name="read">Read a file</tool>
  <tool name="write">Write a file</tool>
</tools>
```

---

## Prompt — Section-Based Builder

```python
class Prompt:
    def __init__(self, sections: list[Section] | None = None) -> None: ...
```

`Prompt` is a dict-backed section manager (keyed by `Section.name`). Sections are registered, queried, and rendered through a fluent API.

### Registration

```python
prompt = Prompt()
prompt.register(Section("role", "You are helpful.", priority=10))
prompt.register(Section("tools", tools_content, priority=40))
```

Or pass sections to the constructor:

```python
prompt = Prompt([
    Section("role", "You are helpful.", priority=10),
    Section("tools", tools_content, priority=40),
])
```

Sections are **name-unique** — registering a section with the same name replaces the previous one.

### Querying

```python
prompt.get("tools")        # → Section or None
prompt.all()                # → list[Section] (all registered)
prompt.enable("tools")      # → self (set enabled=True)
prompt.disable("tools")     # → self (set enabled=False)
prompt.unregister("tools")  # → removed Section or None
```

### Context Injection

Context variables are passed via the `context()` method and become available to callable sections:

```python
prompt.context(cwd="/home/user", tools=registry, version="0.3")
```

The context dict is stored internally and passed to every callable section's function at build time.

### Building

```python
output = prompt.build(fmt="xml", wrap="system-prompt")
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `fmt` | `"text"` | `"xml"` or `"text"` — determines rendering mode |
| `wrap` | `None` | Wrapper tag name (XML mode only). Defaults to `"system-prompt"`. Set to `None` for unwrapped output. |

**Section ordering:** Sections are sorted by `(priority, name)` — stable tie-breaking by alphabetical name. This means:

- Lower priority numbers appear first.
- Sections with the same priority are sorted alphabetically by name.

After sorting, disabled sections and sections with empty content are skipped.

---

## XML vs Text Mode

### XML Mode (`fmt="xml"`)

Sections become XML tags. The full output is wrapped in a root element:

```xml
<system-prompt>

<guidelines>
Be concise and direct
</guidelines>

<tools>
<tool name="read">Read a file</tool>
<tool name="write">Write a file</tool>
</tools>

</system-prompt>
```

- Empty content sections produce self-closing tags: `<tag></tag>`
- Callable content is resolved before rendering
- Nested section lists are rendered recursively with double-newline separation
- Attributes are rendered as `key="value"` on the opening tag

### Text Mode (`fmt="text"`)

Sections are rendered as indented text with name prefixes:

```
guidelines:
Be concise and direct

tools:
  tool (name=read): Read a file
  tool (name=write): Write a file
```

- Multi-line content is indented under the section name
- Nested sections get additional indentation levels
- Attributes appear in parentheses after the section name

### When to Use Which

| Mode | Use case |
|------|----------|
| XML | LLM system prompts — structured, parseable by the model |
| Text | Debugging, logging, non-XML contexts |

---

## Callable Sections

Callable sections defer their content generation to build time. The callable receives the prompt's context dict and returns a string or list of sections:

```python
def _render_tools(ctx: dict[str, Any]) -> list[Section]:
    registry = ctx["tools"]
    return [Section("tool", t.description, attrs={"name": t.name}) for t in registry.all()]

prompt.register(Section("tools", _render_tools, priority=40))
prompt.context(tools=my_registry)
```

At build time, `prompt.context(tools=my_registry)` stores the registry, and when `build()` renders the "tools" section, it calls `_render_tools({"tools": my_registry, ...})` to get the actual content.

This pattern is the backbone of MoCode's prompt assembly — it separates section structure (defined at module load time) from content generation (deferred to build time when runtime objects are available).

---

## Prompts Layer

### app.py — System Prompt Assembly

`build_system_prompt()` constructs the main LLM system prompt used by `CLIApp`. It creates a `Prompt` with sections ordered by priority to maximize LLM prefix cache hit rate.

```python
def build_system_prompt(
    tools: Any = None,
    skill_manager: Any = None,
    vfs: Any = None,
    cwd: str = "",
    home: str = "",
    config_path: str = "",
    skills_dir: str = "",
    sessions_dir: str = "",
    **ctx: Any,
) -> str:
```

#### Section Priority Table

The ordering is designed so that **stable content** (doesn't change between sessions) comes first, and **dynamic content** (tools, skills) comes later. This maximizes the prefix cache hit rate for LLM providers that cache based on prompt prefix.

| Priority | Section name | Source | Content |
|----------|-------------|--------|---------|
| 10 | `guidelines` | Hardcoded in `_render_guidelines()` | Four core behavior rules |
| 20 | `agents` | AGENTS.md files (global + project) | User/project-specific instructions |
| 30 | `environment` | Runtime paths (cwd, home, config, etc.) | Working environment details |
| 35 | `vfs` | VirtualFS instance (if non-empty) | VFS usage guidance |
| 40 | `tools` | ToolRegistry | Tool name/description pairs |
| 50 | `skills` | SkillManager | Skill name/description/path |
| 60 | `workflows` | Hardcoded usage guide | /workflow command reference |

#### AGENTS.md Injection

The `agents` section (priority 20) reads AGENTS.md from two locations:

1. **Global:** `~/.mocode/AGENTS.md` — applies to all projects
2. **Project:** `./AGENTS.md` — project-specific (in the working directory)

Both files are optional. If neither exists, a hint is injected instead:

```
No AGENTS.md files found yet. You can create them to provide persistent
instructions. Common sections: project overview, build/test commands, code style,
testing instructions, security considerations.
```

When AGENTS.md files exist, they are rendered as nested `<agent>` tags with source metadata:

```xml
<agents>
  <header>The following instructions are loaded from AGENTS.md files...</header>
  <agent source="global" path="C:\Users\user\.mocode\AGENTS.md">
    ...global instructions...
  </agent>
  <agent source="project" path="C:\Users\user\project\AGENTS.md">
    ...project instructions...
  </agent>
</agents>
```

#### Tools Section

Each registered tool becomes a `<tool>` child:

```xml
<tools>
  <tool name="read">Read a file and return its contents with line numbers...</tool>
  <tool name="write">Write content to a file...</tool>
  <tool name="bash">Run a shell command in a persistent bash session...</tool>
</tools>
```

#### Skills Section

Each discovered skill becomes a `<skill>` child:

```xml
<skills>
  <skill name="workflow" path="vfs://workflow/">Design, create, and run MoCode Workflows...</skill>
  <skill name="find-docs" path="C:\Users\user\.mocode\skills\find-docs">Retrieves authoritative...</skill>
</skills>
```

#### Workflows Section

Contains a static usage guide for the `/workflow` command — individual workflow definitions are **not** included in the system prompt. The LLM discovers them on demand via `/workflow list`.

#### Full Build Sequence

```
build_system_prompt()
  → creates Section list with priority-sorted lambdas
  → wraps in Prompt(sections)
  → calls Prompt.context(**kwargs) to inject runtime objects
  → calls Prompt.build(fmt="xml") to render
  → returns complete XML string
```

The resulting string is passed directly to the LLM as the system message.

---

### compact.py — Compression Prompts

`compact.py` provides two exports used by `CompactHook` and `core/compact.py` during context compression:

#### `summary_system_prompt`

A pre-built `Prompt` instance (not a function — it's a module-level constant):

```python
summary_system_prompt = Prompt([
    Section("role", "...", priority=10),
    Section("principle", "...", priority=20),
    Section("preserve", "...", priority=30),
    Section("discard", "...", priority=40),
    Section("output-format", "...", priority=50),
])
```

Sections, in priority order:

| Priority | Section | Purpose |
|----------|---------|---------|
| 10 | `role` | "You are a conversation compression assistant..." |
| 20 | `principle` | "Extract core, discard noise" — the compression philosophy |
| 30 | `preserve` | Priority-ordered list of what to keep (requirements, decisions, errors, paths) |
| 40 | `discard` | What to throw away (pleasantries, redundant facts, long code blocks, full outputs) |
| 50 | `output-format` | The structured output template with `[Intent]`, `[Done]`, `[State]`, `[Blockers]`, `[Notes]` |

Built with `summary_system_prompt.build(fmt="xml")` to produce the system prompt for the compression LLM call.

#### Output Format

The compression prompt enforces a specific structured output:

```
[Intent] What the user wants to achieve (1-3 sentences).

[Done] Key actions completed. One line per action, concise.
       Format: action — file/module affected.

[State] Current project state: modified files, build/test status, known issues.

[Blockers] Unresolved errors, failed attempts, open questions.

[Notes] User preferences, technical decisions, constraints (if any).
```

#### `COMPACT_USER_TEMPLATE`

A string template with a `{messages_text}` placeholder:

```python
COMPACT_USER_TEMPLATE = """
Compress the following conversation into a concise summary following the structured format above.

Guidelines:
1. Focus on: what the user wants, what was done, what's pending
2. Omit intermediate tool outputs, exploratory dead ends, and verbose code
3. Be concise but precise on file paths, function names, and error messages
4. Use short phrases, not full sentences — every word must earn its place

Conversation to compress:

{messages_text}
"""
```

At compression time, `{messages_text}` is replaced with a formatted text representation of the conversation history (preprocessed to truncate large tool results and arguments).

#### Usage in CompactHook

```python
# From hooks/compact.py:
async def on_compact(self, ctx: CompactContext) -> None:
    ctx.messages[:] = await compact_messages(
        self._agent.provider,
        ctx.messages,
        summary_system_prompt.build(fmt="xml"),   # system message
        COMPACT_USER_TEMPLATE,                     # user template
    )
```

---

### subagent.py — Delegated Sub-Agent Prompts

`build_subagent_prompt()` constructs the system prompt for a delegated sub-agent by layering sub-agent-specific sections onto the parent agent's full system prompt.

```python
def build_subagent_prompt(parent_prompt: str) -> str:
```

#### Prompt Layering

The sub-agent prompt is built in three layers:

```
<identity>                          ← NEW: sub-agent identity (first thing LLM sees)
  You are a sub-agent executing a
  specific task delegated by the
  parent agent. Your output is
  returned to the parent agent —
  it is not shown to the user
  directly.
</identity>

{parent_prompt}                     ← INHERITED: full parent system prompt
  (AGENTS.md, environment, tools,
  skills, guidelines — everything)

<guidelines>                        ← NEW: sub-agent execution rules
  - Focus on completing the task.
    No greetings, no summaries
  - Use tools freely. Diagnose
    and retry before reporting back
  - Return clear, concise results
  - You cannot ask follow-up questions
  - Follow the parent agent's
    guidelines from AGENTS.md
</guidelines>
```

This design ensures the sub-agent:

1. **Knows it's a sub-agent** — the `<identity>` section (prepended first so it's the highest-priority information for the LLM)
2. **Has full context** — AGENTS.md, environment, tools, skills, and all other context from the parent
3. **Follows sub-agent rules** — the `<guidelines>` section (appended last) adds execution constraints specific to delegated tasks

#### Identity Text

```
You are a sub-agent executing a specific task delegated by the parent agent.
Your output is returned to the parent agent — it is not shown to the user directly.
```

#### Sub-Agent Guidelines

```
- Focus on completing the task. No greetings, no summaries, no unnecessary explanations
- Use tools freely. If something fails, diagnose and retry before reporting back
- Return clear, concise results: file paths, key data, or brief status
- If the task cannot be completed, state what is missing briefly
- You cannot ask follow-up questions. Work autonomously
- Follow any special requirements in the task description precisely
- Follow the parent agent's guidelines, conventions, and constraints from AGENTS.md
```

---

## Writing Custom Prompts

### Building a Simple Prompt

```python
from mocode.core.prompt import Prompt, Section

prompt = Prompt([
    Section("role", "You are a code reviewer.", priority=10),
    Section("rules", "Be constructive. Focus on correctness.", priority=20),
])

output = prompt.build(fmt="xml")
# → <system-prompt>\n\n<role>\nYou are a code reviewer.\n</role>\n\n...
```

### Using Callable Sections

```python
from mocode.core.prompt import Prompt, Section

def render_file_list(ctx: dict) -> str:
    files = ctx.get("files", [])
    return "\n".join(f"- {f}" for f in files)

prompt = Prompt([
    Section("role", "Review these files:", priority=10),
    Section("files", render_file_list, priority=20),
])

output = prompt.context(files=["main.py", "utils.py"]).build(fmt="xml")
```

### Combining Static and Dynamic Sections

```python
from mocode.core.prompt import Prompt, Section

# Static sections (defined at module level)
ROLE = Section("role", "You are a documentation generator.", priority=10)
STYLE = Section("style", "Use Markdown. Be concise.", priority=20)

# Dynamic section (content generated at build time)
def tools_section(ctx: dict) -> list[Section]:
    tools = ctx.get("tools", [])
    return [Section("tool", t["desc"], attrs={"name": t["name"]}) for t in tools]

# Build at runtime
prompt = Prompt([ROLE, STYLE, Section("tools", tools_section, priority=40)])
output = prompt.context(tools=[
    {"name": "read", "desc": "Read files"},
    {"name": "write", "desc": "Write files"},
]).build(fmt="xml")
```

### Overriding Built-in Sections

Since sections are name-unique, you can replace any section:

```python
from mocode.prompts.app import build_system_prompt
from mocode.core.prompt import Prompt, Section

# Build the standard prompt, then modify it
prompt = Prompt([
    Section("guidelines", "Custom guidelines", priority=10),
    Section("role", "You are a specialized agent.", priority=5),  # higher priority = earlier
])
```

### Using Text Mode for Debugging

```python
prompt = Prompt([
    Section("role", "You are helpful.", priority=10),
    Section("tools", [
        Section("tool", "Read files", attrs={"name": "read"}),
        Section("tool", "Write files", attrs={"name": "write"}),
    ], priority=20),
])

# Debug with text mode
print(prompt.build(fmt="text"))
# → role:
#    You are helpful.
#   tools:
#     tool (name=read): Read files
#     tool (name=write): Write files
```

### Disabling Sections Temporarily

```python
prompt.disable("workflows")  # omit workflows from the prompt
output = prompt.build(fmt="xml")  # workflows section is skipped

prompt.enable("workflows")   # re-enable for next build
```

### Using the Agent Builder with Prompts

```python
from mocode.core.builder import Agent
from mocode.core.prompt import Prompt, Section

# Option 1: Pass a string
agent = Agent().provider(p).prompt("You are helpful.").build()

# Option 2: Pass a Prompt instance
prompt = Prompt([Section("role", "You are helpful.", priority=10)])
agent = Agent().provider(p).prompt(prompt).build()

# Option 3: Pass a list of sections
sections = [Section("role", "You are helpful.", priority=10)]
agent = Agent().provider(p).prompt(sections).build()

# Option 4: With context for callable sections
agent = (Agent()
    .provider(p)
    .prompt(prompt)
    .prompt_context(tools=my_registry, cwd="/project")
    .prompt_format("xml")
    .build())
```

---

## Design Notes

### Priority-Based Ordering

The priority system serves two purposes:

1. **Logical ordering** — Guidelines (10) before agents (20) before tools (40) creates a natural reading flow for the LLM.
2. **LLM prefix cache optimization** — Stable content (guidelines, AGENTS.md) has lower priority numbers and appears first. This means the prefix of the system prompt is the same across sessions, maximizing cache hits for providers that support prompt caching.

### Dict-Backed Section Storage

`Prompt` stores sections in a `dict[str, Section]` rather than a `list[Section]`. This means:

- Registering a section with the same name **replaces** the previous one (no duplicates).
- Lookup by name is O(1).
- The `unregister` method returns the removed section, enabling temporary removal.
- Sorting happens at build time, not registration time.

### Callable Sections vs Pre-rendered Content

Callable sections are evaluated at `build()` time, not at `register()` time. This allows:

- Sections to be registered early with deferred content generation.
- Context variables to be injected after registration via `context()`.
- The same section definition to produce different content based on context.

### The `attrs` Dict

Attributes serve as metadata on section tags. They are used extensively in `app.py` to attach source information (file paths, tool names, skill names) to sections without putting that data in the content. The LLM can reference these attributes to understand where instructions come from.

### Lazy Evaluation in `build_system_prompt()`

`build_system_prompt()` creates section objects with lambda/content functions at call time, but those functions are only evaluated when `Prompt.build()` is called. This means:

- The `Prompt` object is created immediately.
- Content functions (which may read files, query registries, etc.) are deferred.
- Context injection (`context()`) happens between creation and build.

This two-phase approach keeps the API clean while allowing deferred evaluation.

### Sub-Agent Prompt Concatenation

Unlike the main system prompt (which uses `Prompt` for structured XML output), `build_subagent_prompt()` uses simple string concatenation. This is intentional — the sub-agent prompt needs to embed the parent's already-rendered XML prompt as a raw string, not re-render it. The `<identity>` and `<guidelines>` tags are prepended/appended manually to avoid nesting issues.

---

## Quick Reference

### Import Patterns

```python
# Core prompt primitives
from mocode.core.prompt import Prompt, Section

# Pre-built prompts
from mocode.prompts import summary_system_prompt, COMPACT_USER_TEMPLATE
from mocode.prompts import build_system_prompt, build_subagent_prompt

# Individual modules
from mocode.prompts.app import build_system_prompt
from mocode.prompts.compact import summary_system_prompt, COMPACT_USER_TEMPLATE
from mocode.prompts.subagent import build_subagent_prompt
```

### Section Priority Ranges

| Range | Convention |
|-------|-----------|
| 0–19 | Core identity and rules (role, guidelines) |
| 20–29 | User/project instructions (AGENTS.md) |
| 30–39 | Environment and filesystem context |
| 40–49 | Tool definitions |
| 50–59 | Skill definitions |
| 60–69 | Workflow guidance |

### Build Modes

| Mode | Method | Output |
|------|--------|--------|
| XML | `prompt.build(fmt="xml")` | `<system-prompt>\n\n<guidelines>...</guidelines>\n\n</system-prompt>` |
| XML (custom wrap) | `prompt.build(fmt="xml", wrap="custom")` | `<custom>...</custom>` |
| XML (no wrap) | `prompt.build(fmt="xml", wrap=None)` | `<guidelines>...</guidelines>\n\n<tools>...</tools>` |
| Text | `prompt.build(fmt="text")` | Indented plain text |
