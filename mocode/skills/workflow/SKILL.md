---
name: workflow
description: Design, create, and run MoCode Workflows — DAG-based multi-step task orchestration with parallel nodes, conditional routing, map fan-out, and loops. Use when the user describes a multi-step process, pipeline, or needs to orchestrate sequential/parallel tasks.
---

# MoCode Workflows

Multi-step task orchestration as a **DAG**. Each node is an independent LLM prompt
(`mocode -p`). Supports fan-out/fan-in, conditional routing, map-over-list, and loops.

YAML files live in `.mocode/workflows/`. Run via `/workflow` in the REPL.

---

## Quick Start

```yaml
name: code-review
params:                         # ← positional parameters
  - path                        # required, position 1
  - focus: "general"            # optional, position 2, default "general"

nodes:
  - id: scan
    task: Scan {path} for issues, focus on {focus}.
    depends: []

  - id: report
    task: Summarize: {nodes.scan.output}
    depends: [scan]
```

```bash
/workflow run code-review ./src security     # path=./src, focus=security
/workflow run code-review ./src              # path=./src, focus=general (default)
/workflow run code-review focus=deep ./src   # key=value overrides positional
```

---

## Node Types

| Type     | Has `task` | Has `routes` | Has `items` | Behavior |
|----------|-----------|-------------|------------|----------|
| `task`   | ✓         | ✗           | ✗          | Fill template → send to LLM |
| `router` | ✗         | ✓           | ✗          | Regex match on upstream output → activate targets |
| `map`    | ✓         | ✗           | ✓          | Fan out task over list items, concatenate results |

---

## Template Variables

All `{name}` placeholders are resolved from a flat context:

| Syntax | Source | Example |
|--------|--------|---------|
| `{param_name}` | Workflow parameter | `{path}`, `{focus}` |
| `{nodes.<id>.output}` | Node output | `{nodes.scan.output}` |
| `{nodes.<id>.exit_code}` | Exit code (0 = OK) | `{nodes.scan.exit_code}` |
| `{nodes.<id>.error}` | Error message | `{nodes.scan.error}` |
| `{nodes.<id>.duration}` | Seconds | `{nodes.scan.duration}` |
| `{previous}` | Last completed node's output | `{previous}` |
| `{env.VAR}` | Environment variable | `{env.HOME}` |
| `{item_key}` | Map item value (in map tasks only) | `{topic}`, `{item}` |

> `{node.X.output}` also works as alias for `{nodes.X.output}`.

**Key rule**: single-segment `{name}` looks up from context top level.
Multi-segment `{a.b}` walks into nested dicts.

---

## Dependencies

**Always add explicit `depends`** — auto-inference is a fallback, not a substitute.

- `task` nodes: inferred from `{nodes.X.*}` in `task`, but add explicitly
- `router` nodes: **must** specify `depends` (no task to infer from)
- `map` nodes: inferred from `task` and `items`, but add explicitly
- Router-gated targets: add the router as a dependency

```yaml
- id: route_q
  type: router
  depends: [analyze]          # required
  routes:
    - match: "critical"
      to: [fix]
    - match: null
      to: [done]

- id: fix
  task: Fix {nodes.analyze.output}
  depends: [route_q]          # gate dependency on router
```

---

## Map Nodes

```yaml
- id: topics
  task: List 3 topics about AI, one per line.

- id: explore
  type: map
  items: "{nodes.topics.output}"   # template → newline-separated list
  item_key: topic                  # default: "item"
  task: Write about {topic}.       # ← single braces, same as other vars
  depends: [topics]
```

**How it works**:
1. `items` template fills → split by newlines → each line is one item
2. `{item_key}` in `task` is replaced per item (via `fill_template`)
3. All children run concurrently (bounded by `concurrency`)
4. Outputs concatenated with `\n---\n` into `{nodes.<id>.output}`

**Rules**: must have `items` + `task`, must NOT have `routes`.

---

## Routers & Loops

```yaml
- id: decide
  type: router
  depends: [summary]
  routes:
    - match: "FAIL"
      to: [fix]
      max: 3                  # ← prevents infinite loop
    - match: null             # fallback (put last)
      to: [done]
```

- First matching route wins — order matters
- `match: null` = unconditional fallback
- Back-edge routes (target upstream) create loops — **always set `max`**
- Workflow-level `max_iterations` (default 100) is the global safety net

---

## REPL Commands

| Command | Description |
|---------|-------------|
| `/workflow list` | List available workflows |
| `/workflow show <name>` | Show DAG structure |
| `/workflow run <name> [args...]` | Run foreground (blocks) |
| `/workflow run-bg <name> [args...]` | Run background (returns immediately) |
| `/workflow status [run_id]` | Check run status |
| `/workflow result [run_id]` | View full results |
| `/workflow runs` | List recent runs |

---

## Notes

- Each node runs as independent `mocode -p` subprocess — no shared state between nodes
- Prompt passed via stdin (no command-line length limit)
- Results persist to `~/.mocode/workflow_runs/<run_id>.json`
- `concurrency` controls max parallel nodes (default 1 = serial)
- Map child tasks also respect the `concurrency` limit

---

Detailed YAML reference: `read vfs://workflow/yaml-reference.md`
