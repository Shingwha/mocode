# Workflow YAML Format Reference

## Full Example

```yaml
name: my-workflow
description: What this workflow does
max_iterations: 100          # optional, default 100

nodes:
  # ── Task node (root) ──────────────────────────────
  - id: overview
    description: Analyze project structure
    task: Analyze project structure in {{args.path}}

  # ── Parallel fan-out (depends auto-inferred) ──────
  - id: check_security
    task: Review security issues in {{nodes.overview.output}}
  - id: check_style
    task: Review style issues in {{nodes.overview.output}}

  # ── Converge ──────────────────────────────────────
  - id: summary
    task: |
      Security: {{nodes.check_security.output}}
      Style: {{nodes.check_style.output}}

  # ── Router (conditional branching) ────────────────
  - id: decide
    type: router
    depends: [summary]
    routes:
      - match: "critical"
        to: [fix]
      - match: null          # fallback
        to: [done]

  # ── Loop with back-edge ───────────────────────────
  - id: fix
    task: Fix issues from {{nodes.summary.output}}
  - id: verify
    type: router
    depends: [fix]
    routes:
      - match: "FAIL"
        to: [fix]
        max: 3               # prevent infinite loop
      - match: null
        to: [done]

  # ── Terminal ──────────────────────────────────────
  - id: done
    task: Generate final report from {{nodes.summary.output}}
    depends: [decide, verify]   # explicit gate on routers
```

## Field Reference

### Top-level fields

| Field            | Required | Default | Description |
|------------------|----------|---------|-------------|
| `name`           | yes      | —       | Workflow identifier (used in CLI) |
| `description`    | yes      | —       | One-line description |
| `max_iterations` | no       | 100     | Global safety limit on total node executions |

### Node fields

| Field         | Required | Description |
|---------------|----------|-------------|
| `id`          | yes      | Unique node identifier |
| `type`        | no       | `task` (default) or `router` |
| `description` | no       | Human-readable description |
| `task`        | task     | Prompt template with `{{}}` variables |
| `depends`     | no       | List of upstream node IDs |
| `routes`      | router   | List of `{match, to, max?}` objects |

### Route fields

| Field   | Required | Description |
|---------|----------|-------------|
| `match` | yes      | Regex to match against upstream output. `null` = fallback |
| `to`    | yes      | List of downstream node IDs |
| `max`   | no       | Max times this route can fire (for back-edge loops) |
