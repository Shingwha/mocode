---
name: workflow
description: Design, create, and run MoCode Workflows — DAG-based multi-step task orchestration with parallel nodes, conditional routing, map fan-out, and loops. Use when the user describes a multi-step process, pipeline, or needs to orchestrate sequential/parallel tasks.
---

# MoCode Workflows

Multi-step task orchestration as a **DAG**. Each node runs an independent LLM session.

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

## Core Concepts

### Node Types

| Type     | Purpose | Key Fields |
|----------|---------|------------|
| `task`   | Prompt → LLM | `task`, `depends` |
| `router` | Regex on upstream output → activate targets | `routes`, `depends` (required) |
| `map`    | Fan-out over list, concatenate results | `items`, `item_key`, `task` |

### Template Variables

`{param}` — workflow param | `{nodes.X.output}` — node output | `{nodes.X.exit_code}` / `.error` / `.duration` | `{previous}` — last output | `{env.VAR}` | `{item}` — map item

> `{node.X.output}` works as alias for `{nodes.X.output}`.

### Dependencies

**Always add explicit `depends`** — auto-inference is a fallback.

- Router nodes: **must** specify `depends` (no task to infer from)
- Router-gated targets: add the router as a dependency
- Terminal nodes: depend on **all** routers that can activate them

### Routers

First matching route wins. `match: null` = unconditional fallback (put last). Back-edge routes create loops — **always set `max`**.

```yaml
- id: decide
  type: router
  depends: [summary]
  routes:
    - match: "FAIL"
      to: [fix]
      max: 3                    # prevents infinite loop
    - match: null
      to: [done]
```

### Map Nodes

```yaml
- id: explore
  type: map
  items: "{nodes.topics.output}"   # template → newline-separated list
  item_key: topic                  # default: "item"
  task: Write about {topic}.       # single braces
  depends: [topics]
```

Items split by newlines → each line is one child task → outputs joined with `\n---\n`.

---

## REPL Commands

| Command | Description |
|---------|-------------|
| `/workflow` | Open interactive menu (browse, run, view recent runs) |
| `/workflow list` | List available workflows |
| `/workflow show <name>` | Show DAG structure |
| `/workflow run <name> [args...]` | Run a workflow (foreground, Ctrl+C to cancel) |
| `/workflow status` | List recent runs |
| `/workflow status <run_id>` | Show run detail by ID |
| `/workflow status <name>` | Show latest run for a workflow |

- `list` has alias `ls`
- `status` accepts a `wf_*` run ID or a workflow name (resolves to latest run)
- Crashed runs (process died) are detected automatically via PID liveness check

---

## Notes

- Each node = independent LLM session, no shared state
- `concurrency` controls max parallel nodes (default 1 = serial)
- Per-node `timeout` defaults to 30 min; global `max_iterations` defaults to 100
- Results persist to `~/.mocode/workflow_runs/<run_id>.json`
- Ctrl+C during a run cancels it and shows partial results

---

## References

- Full YAML reference: `read vfs://workflow/yaml-reference.md`
- Task board pattern: `read vfs://workflow/task-board-pattern.md`
