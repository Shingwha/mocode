# MoCode Skills System — Developer Reference

Skills are MoCode's mechanism for giving the agent domain-specific knowledge on demand. Each skill is a bundle of instructions and optional reference files that the agent can load at runtime via the `skill` tool or via `/skill:<name>` REPL commands.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────┐
│  System Prompt (prompts/app.py)                         │
│  ├── <skills> section lists all skill names/descriptions │
│  └── <vfs> section explains how to read vfs:// files     │
├─────────────────────────────────────────────────────────┤
│  SkillTool (tools/skill.py)                             │
│  └── LLM-callable: skill(name="workflow") → content     │
├─────────────────────────────────────────────────────────┤
│  SkillManager (core/skill.py)                           │
│  ├── discover() — scans skill_dirs for SKILL.md         │
│  ├── register() — programmatic (built-in) skills        │
│  ├── get(name) → Skill                                  │
│  └── _mount_to_vfs() — mounts reference files           │
├─────────────────────────────────────────────────────────┤
│  VirtualFS (core/virtualfs.py)                          │
│  └── Dict-backed vfs:// files for read/glob/grep tools  │
├─────────────────────────────────────────────────────────┤
│  CLI Commands (app/cli/commands/skill.py)               │
│  └── /skill:<name> → loads skill content into context   │
└─────────────────────────────────────────────────────────┘
```

### Data Flow

1. **Startup**: `CLIApp._build_agent()` creates a `SkillManager` with `~/.mocode/skills` as the discovery directory and a shared `VirtualFS`.
2. **Discovery**: `SkillManager.discover()` scans for subdirectories containing `SKILL.md`, parses frontmatter, and creates `Skill` objects.
3. **Built-in registration**: Programmatic skills (e.g., `WorkflowSkill`) are registered via `SkillManager.register()` and also mounted into the VFS.
4. **Prompt injection**: `_render_skills()` in `prompts/app.py` adds a `<skills>` section to the system prompt listing every skill's name and description. This lets the LLM know what skills exist and when to use them.
5. **On-demand loading**: When the LLM calls the `skill` tool, `SkillTool._execute()` looks up the skill by name and returns the full body content (SKILL.md after frontmatter). Reference files are accessible separately via `read(path='vfs://skill-name/file.md')`.
6. **REPL shortcut**: Users can type `/skill:workflow` directly in the REPL to inject the skill's content as a user message.

---

## Core Types (core/skill.py)

### `SkillMetadata`

```python
@dataclass
class SkillMetadata:
    name: str               # Unique identifier (used as VFS directory name)
    description: str        # One-line description (shown in system prompt)
    attrs: dict = {}        # Extra frontmatter keys beyond name/description
```

Parsed from YAML frontmatter via `SkillMetadata.from_dict()`. Unknown keys are preserved in `attrs`.

### `Skill`

```python
@dataclass
class Skill:
    metadata: SkillMetadata
    path: Path | None = None       # Real filesystem directory (for directory-discovered skills)
    vfs_uri: str | None = None     # Virtual URI (e.g., "vfs://workflow/")
    _content: str | None = None    # Cached SKILL.md body (loaded lazily)
```

Key properties and methods:

| Member | Description |
|--------|-------------|
| `base_dir` | Human-readable location string (`path` or `vfs_uri`) |
| `load_content()` | Returns the SKILL.md body text (after frontmatter). Caches on first call. |

Exactly one of `path` or `vfs_uri` is set:
- **Directory-discovered skills** have `path` set (points to the skill directory on disk).
- **Built-in skills** set both `path` (for VFS mounting) and `vfs_uri` (for display).

### `SkillManager`

```python
class SkillManager:
    def __init__(self, skill_dirs: list[Path] | None = None, *, vfs: VirtualFS | None = None):
        ...
```

| Method | Description |
|--------|-------------|
| `discover()` | Scans `skill_dirs` for subdirectories containing `SKILL.md`. Clears discovered skills (but not registered ones). Called automatically in `__init__`. |
| `register(skill)` | Adds a programmatic skill. Skipped if a discovered skill with the same name exists. Mounts reference files into VFS. |
| `get(name) → Skill \| None` | Looks up by name (discovered skills first, then registered). |
| `all() → list[Skill]` | Returns all skills (discovered first, then registered). |
| `all_metadata() → list[SkillMetadata]` | Metadata-only variant of `all()`. |
| `names() → list[str]` | Flat list of all skill names. |

**Two-tier lookup**: Discovered skills (`_skills`) take priority over registered/builtin skills (`_builtin_skills`). This means a user can override a built-in skill by placing a higher-priority `SKILL.md` in `~/.mocode/skills/`.

---

## SKILL.md Format

Every skill is a directory containing a `SKILL.md` file. The file uses YAML frontmatter for metadata and Markdown for instructions.

### Template

```markdown
---
name: my-skill
description: One-line description shown in the system prompt.
---

# My Skill

Instructions for the agent go here. This content is returned verbatim
when the agent calls `skill(name="my-skill")` or when the user types
`/skill:my-skill`.

## What This Skill Does

- Step 1: ...
- Step 2: ...

## Reference

For the full API reference, read: `read vfs://my-skill/api-reference.md`
```

### Frontmatter Rules

| Field | Required | Description |
|-------|----------|-------------|
| `name` | Yes | Unique skill identifier. Used as the tool argument, REPL command suffix, and VFS path prefix. Must be valid as a directory name. |
| `description` | Yes | One-line description. Displayed in the `<skills>` section of the system prompt. The LLM uses this to decide when to invoke the skill. |
| (other) | No | Any extra YAML keys are preserved in `SkillMetadata.attrs`. Use for custom metadata. |

**Parsing behavior:**
- Frontmatter is delimited by `---` on the first and second occurrences in the file.
- If no frontmatter is found, the skill is skipped during discovery.
- If the `name` field is missing or empty, the skill is skipped.
- YAML parsing uses `yaml.safe_load` (PyYAML dependency).

### Body Content

Everything after the closing `---` is the skill body. This is what the `SkillTool` returns to the LLM. Common conventions:

- **Instructions**: Step-by-step guidance for the LLM.
- **Reference links**: Point to VFS-hosted reference files (e.g., `read vfs://my-skill/api.md`).
- **Examples**: Code samples, templates, or patterns.

---

## VirtualFS Integration

### How Reference Files Are Mounted

When `SkillManager._mount_to_vfs(skill)` runs:

1. Walks the skill's `path` directory recursively (`rglob("*")`).
2. Skips `SKILL.md`, `__init__.py`, `.pyc` files, and `__pycache__/` directories.
3. Reads each remaining file as UTF-8 text.
4. Registers it in the `VirtualFS` at `vfs://<skill-name>/<relative-path>`.

**Example**: The `workflow` skill directory:

```
mocode/skills/workflow/
├── SKILL.md                    # Skipped (skill instructions)
├── __init__.py                 # Skipped (Python package file)
├── yaml-reference.md           # Mounted at vfs://workflow/yaml-reference.md
└── task-board-pattern.md       # Mounted at vfs://workflow/task-board-pattern.md
```

### Accessing Virtual Files

The `read`, `glob`, and `grep` tools all support `vfs://` paths natively:

```
# LLM can call these directly:
read(path='vfs://workflow/yaml-reference.md')
glob(pattern='**/*', path='vfs://')
grep(pattern='router', path='vfs://workflow/')
```

The `VirtualFS` class is a simple `collections.abc.Mapping` backed by a `dict[str, str]`:

```python
class VirtualFS(collections.abc.Mapping):
    def add(self, path: str, content: str) -> None:    # Register a file
    def remove(self, path: str) -> bool:                # Remove a file
    def exists(self, path: str) -> bool:                # Check existence
    def __getitem__(self, path: str) -> str:            # Read content
```

All paths are auto-normalized to include the `vfs://` prefix.

### VFS in the System Prompt

When the VFS is non-empty, `prompts/app.py` adds a `<vfs>` section explaining to the LLM how to access virtual files. This section teaches the agent the `read`, `glob`, and `grep` commands with `vfs://` paths.

---

## Built-in Skills

### Workflow Skill

**Location**: `mocode/skills/workflow/`
**Name**: `workflow`

The only built-in skill shipped with MoCode. It teaches the agent how to design, create, and run MoCode Workflows (DAG-based multi-step task orchestration).

| File | VFS Path | Purpose |
|------|----------|---------|
| `SKILL.md` | — (instructions) | Full workflow reference: node types, template variables, routers, fan-out, `[TAG]` protocol, REPL commands |
| `yaml-reference.md` | `vfs://workflow/yaml-reference.md` | Detailed YAML schema for workflow definition files |
| `task-board-pattern.md` | `vfs://workflow/task-board-pattern.md` | A reusable pattern for task-board-style workflows |

**Registration** in `CLIApp._build_agent()`:

```python
from ...skills import WorkflowSkill

self._skill_mgr = SkillManager([self.home / "skills"], vfs=self._vfs)
self._skill_mgr.register(WorkflowSkill())
```

### Adding a New Built-in Skill

Follow this four-step process:

1. **Create the directory** at `mocode/skills/<name>/` with a `SKILL.md` and any reference files.

2. **Add a factory function** in `mocode/skills/<name>/__init__.py`:

```python
from pathlib import Path
from mocode.core.skill import make_builtin_skill

def MySkill():
    """Description shown in the system prompt."""
    return make_builtin_skill(Path(__file__).parent, default_name="my-skill")
```

3. **Export it** from `mocode/skills/__init__.py`:

```python
from .my_skill import MySkill

__all__ = [
    "WorkflowSkill",
    "MySkill",
    "make_builtin_skill",
]
```

4. **Register it** in `mocode/app/cli/app.py` inside `_build_agent()`:

```python
from ...skills import WorkflowSkill, MySkill

self._skill_mgr.register(WorkflowSkill())
self._skill_mgr.register(MySkill())
```

The `make_builtin_skill()` helper:
- Reads `SKILL.md` frontmatter for name/description.
- Pre-loads the body content (avoids re-reading on each `load_content()` call).
- Sets both `path` (for VFS mounting) and `vfs_uri` (for display).

---

## How to Create Custom User Skills

User skills live in `~/.mocode/skills/`. The `SkillManager` scans this directory automatically at startup.

### Directory Layout

```
~/.mocode/skills/
└── my-api-helper/
    ├── SKILL.md              # Required — metadata + instructions
    ├── api-reference.md      # Optional — mounted at vfs://my-api-helper/api-reference.md
    ├── examples/             # Optional — subdirectories are supported
    │   └── basic-usage.md    # Mounted at vfs://my-api-helper/examples/basic-usage.md
    └── templates/
        └── request.yaml      # Mounted at vfs://my-api-helper/templates/request.yaml
```

### SKILL.md Example

```markdown
---
name: my-api-helper
description: Helps interact with the MyAPI REST service. Use when the user mentions MyAPI endpoints, authentication, or data fetching.
---

# MyAPI Helper

This skill provides guidance for working with the MyAPI REST service.

## Authentication

All requests require a Bearer token. Get one via:
```bash
curl -X POST https://api.example.com/auth/token -d '{"user":"..."}'
```

## Common Endpoints

- `GET /v1/items` — List items
- `POST /v1/items` — Create an item
- `GET /v1/items/{id}` — Get item by ID

For the full endpoint reference, read: `read vfs://my-api-helper/api-reference.md`
```

### Rules and Constraints

1. **One skill per directory**: Each subdirectory of a skill directory is treated as a potential skill. Only directories containing `SKILL.md` are loaded.

2. **Name uniqueness**: If a discovered skill and a built-in skill share the same name, the discovered skill wins (discovered skills take priority in lookup). Programmatic registration is skipped for duplicate names.

3. **File size**: All reference files are loaded into memory and injected into the VFS. Keep individual files reasonably sized. There is no built-in limit, but the LLM's context window is the practical constraint.

4. **Text files only**: `_mount_to_vfs()` reads files as UTF-8 text. Binary files (images, PDFs, etc.) will be skipped if they fail `read_text(encoding="utf-8")`.

5. **No Python files**: `__init__.py` and `.pyc` files are explicitly skipped during VFS mounting.

6. **Encoding**: Files must be valid UTF-8. Files with other encodings (e.g., GBK) will be silently skipped.

---

## CLI Integration

### REPL Commands

For each skill, a `/skill:<name>` command is auto-registered:

```
/skill:workflow          → Injects workflow skill content as a user message
/skill:my-api-helper     → Injects my-api-helper content
```

The command handler:
1. Calls `skill.load_content()` to get the SKILL.md body.
2. Wraps it as: `[Skill:<name> — instructions below, do NOT call the skill tool]\n\n<body>`
3. If the user provides args (`/skill:workflow create a DAG for...`), appends them after a separator.

### Skill Tool (LLM-callable)

The `skill` tool is available to the LLM during agent execution:

```
Tool: skill
Parameters:
  name: string (required) — The skill name to load
Description: "Load a skill by name. Use when the user's request matches a skill's
             description. Returns the skill's instructions for you to follow."
```

The tool returns:
```
Base directory: <base_dir>

<SKILL.md body content>
```

If the skill is not found, it raises `ToolError` with a list of available skills.

### System Prompt Injection

Skills appear in the `<skills>` section of the system prompt as:

```xml
<skills>
<skill name="workflow" path="vfs://workflow/">Design, create, and run MoCode Workflows...</skill>
<skill name="my-api-helper" path="/home/user/.mocode/skills/my-api-helper">Helps interact with...</skill>
</skills>
```

The LLM sees all skill names and descriptions upfront. It uses the `skill` tool to load full instructions when it determines a skill is relevant to the user's request.

---

## Programmatic Usage

### Creating a Skill in Code

```python
from mocode.core.skill import Skill, SkillMetadata

skill = Skill(
    metadata=SkillMetadata(name="inline", description="A programmatically created skill"),
    _content="# Inline Skill\n\nDo the thing.",
)
skill_mgr.register(skill)
```

Programmatic skills don't have a `path`, so no reference files are mounted into the VFS. The body content is provided directly via `_content`.

### Using `make_builtin_skill()`

```python
from pathlib import Path
from mocode.core.skill import make_builtin_skill

# Creates a Skill from a directory with SKILL.md
skill = make_builtin_skill(Path("./my-skill-dir"), default_name="my-skill")

# The skill now has:
#   skill.metadata.name == "my-skill" (from frontmatter or default_name)
#   skill.metadata.description == "..." (from frontmatter)
#   skill.path == Path("./my-skill-dir")
#   skill.vfs_uri == "vfs://my-skill/"
#   skill._content == "<body text>" (pre-loaded)
```

### Building a SkillManager

```python
from pathlib import Path
from mocode.core.skill import SkillManager
from mocode.core.virtualfs import VirtualFS

vfs = VirtualFS()
mgr = SkillManager(
    skill_dirs=[Path.home() / ".mocode" / "skills"],
    vfs=vfs,
)

# Discover user skills + register built-ins
mgr.register(WorkflowSkill())

# Query
for skill in mgr.all():
    print(f"{skill.metadata.name}: {skill.metadata.description}")

# Load content
skill = mgr.get("workflow")
if skill:
    instructions = skill.load_content()
```

---

## File Reference

| File | Purpose |
|------|---------|
| `mocode/core/skill.py` | `SkillMetadata`, `Skill`, `SkillManager`, `make_builtin_skill()`, SKILL.md parsing |
| `mocode/core/virtualfs.py` | `VirtualFS` — dict-backed in-memory filesystem |
| `mocode/tools/skill.py` | `SkillTool` — LLM-callable tool for loading skills |
| `mocode/app/cli/commands/skill.py` | `make_skill_command()` — creates `/skill:<name>` REPL commands |
| `mocode/prompts/app.py` | `_render_skills()` — injects skill list into system prompt |
| `mocode/skills/__init__.py` | Built-in skill exports and `make_builtin_skill` re-export |
| `mocode/skills/workflow/` | Built-in workflow skill (SKILL.md + reference docs) |
