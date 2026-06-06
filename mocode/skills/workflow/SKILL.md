---
name: workflow
description: Design, create, and run MoCode Workflows — DAG-based multi-step task orchestration with parallel nodes, conditional routing, map fan-out, and loops. Use when the user describes a multi-step process, pipeline, or needs to orchestrate sequential/parallel tasks.
---

# MoCode Workflows

Multi-step task orchestration as a **DAG**. Each node runs an independent LLM session.
Supports fan-out/fan-in, conditional routing, map-over-list, and loops.

YAML files live in `.mocode/workflows/`. Run via `/workflow` in the REPL.

---

## Quick Start

```yaml
name: code-review
params:
  - path                      # required, position 1
  - focus: "general"          # optional, default "general"

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
/workflow run code-review ./src              # focus=general (default)
/workflow run code-review focus=deep ./src   # key=value overrides positional
```

---

## Top-level Fields

| Field            | Default | Description |
|------------------|---------|-------------|
| `name`           | —       | Workflow identifier (required) |
| `description`    | `""`    | One-line description |
| `params`         | `[]`    | Positional parameters |
| `nodes`          | `[]`    | Node definitions (required) |
| `concurrency`    | `1`     | Max parallel nodes |
| `max_iterations` | `100`   | Global safety limit on total node executions |
| `timeout`        | `1800`  | Per-node timeout in seconds (30 min) |

---

## Node Types

| Type     | `task` | `routes` | `items` | Behavior |
|----------|--------|----------|---------|----------|
| `task`   | ✓      | ✗        | ✗       | Fill template → LLM |
| `router` | ✗      | ✓        | ✗       | Regex on upstream output → activate targets |
| `map`    | ✓      | ✗        | ✓       | Fan-out over list, concatenate results |

### Node Fields

| Field         | task | router | map | Default  | Description |
|---------------|------|--------|-----|----------|-------------|
| `id`          | ✓    | ✓      | ✓   | —        | Unique identifier |
| `type`        |      |        |     | `"task"` | `"task"`, `"router"`, `"map"` |
| `description` |      |        |     | `""`     | Human-readable label |
| `task`        | ✓    | ✗      | ✓   | `""`     | Prompt template |
| `depends`     | rec  | **req**| rec | `[]`     | Upstream node IDs |
| `routes`      | ✗    | ✓      | ✗   | `[]`     | Route rules |
| `items`       | ✗    | ✗      | ✓   | `""`     | Template → newline-separated list |
| `item_key`    | ✗    | ✗      |     | `"item"` | Variable name per item |

---

## Template Variables

| Syntax | Source | Example |
|--------|--------|---------|
| `{param}` | Workflow parameter | `{path}`, `{focus}` |
| `{nodes.<id>.output}` | Node output | `{nodes.scan.output}` |
| `{nodes.<id>.exit_code}` | Exit code (0 = OK) | `{nodes.scan.exit_code}` |
| `{nodes.<id>.error}` | Error message | `{nodes.scan.error}` |
| `{nodes.<id>.duration}` | Seconds | `{nodes.scan.duration}` |
| `{previous}` | Last completed output | `{previous}` |
| `{env.VAR}` | Environment variable | `{env.HOME}` |
| `{item_key}` | Map item value (map tasks only) | `{topic}`, `{item}` |

> `{node.X.output}` works as alias for `{nodes.X.output}`.
> Single-segment `{name}` → context top level. Multi-segment `{a.b}` → nested dict.

---

## Dependencies

**Always add explicit `depends`** — auto-inference is a fallback, not a substitute.

- `task` nodes: inferred from `{nodes.X.*}` in `task`, but add explicitly
- `router` nodes: **must** specify `depends` (no task to infer from)
- `map` nodes: inferred from `task` and `items`, but add explicitly
- Router-gated targets: add the router as a dependency
- Terminal nodes: depend on **all** routers that can activate them

```yaml
- id: route_q
  type: router
  depends: [analyze]            # required
  routes:
    - match: "critical"
      to: [fix]
    - match: null
      to: [done]

- id: fix
  task: Fix {nodes.analyze.output}
  depends: [route_q]            # gate dependency on router

- id: done
  task: Final report
  depends: [route_q]            # terminal must depend on router
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
  task: Write about {topic}.       # single braces
  depends: [topics]
```

**Mechanics**:
1. `items` template fills → split by newlines → each line is one item
2. `{item_key}` replaced per item in `task`
3. Children run concurrently (bounded by `concurrency`)
4. Outputs joined with `\n---\n` into `{nodes.<id>.output}`

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
      max: 3                    # prevents infinite loop
    - match: null               # fallback (put last)
      to: [done]
```

- First matching route wins — order matters
- `match: null` = unconditional fallback
- Back-edge routes (target upstream) create loops — **always set `max`**
- Workflow-level `max_iterations` (default 100) is the global safety net

### Route Fields

| Field   | Required | Description |
|---------|----------|-------------|
| `match` | ✓        | Regex on concatenated dependency output. `null` = fallback. |
| `to`    | ✓        | Target node ID(s). String or list. |
| `max`   |          | Max fires (for back-edge loops). `0` = unlimited. |

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

- Each node runs as an independent LLM session — no shared state between nodes
- Results persist to `~/.mocode/workflow_runs/<run_id>.json`
- `concurrency` controls max parallel nodes (default 1 = serial)
- Map child tasks also respect the `concurrency` limit
- Per-node `timeout` defaults to 30 minutes; override via top-level `timeout` field

---

Detailed YAML reference: `read vfs://workflow/yaml-reference.md`
