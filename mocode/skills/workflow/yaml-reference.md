# Workflow YAML Reference

## Complete Example

```yaml
name: code-review
description: Automated code review pipeline
max_iterations: 100           # default 100
concurrency: 3                # default 1 (max parallel nodes)
timeout: 1800                 # default 1800 (per-node seconds)

params:
  - path                      # required, position 1
  - focus: "general"          # optional, default "general"

nodes:
  - id: scan
    description: Scan project for issues
    task: Scan {path} for {focus} issues.
    depends: []

  # Parallel fan-out
  - id: security
    task: Review security in {nodes.scan.output}
    depends: [scan]

  - id: style
    task: Review style in {nodes.scan.output}
    depends: [scan]

  # Map fan-out
  - id: audit
    type: map
    items: "{nodes.list_modules.output}"
    item_key: module
    task: Audit {module} for {focus} issues.
    depends: [list_modules]

  - id: list_modules
    task: List top-level modules, one per line.
    depends: [scan]

  # Converge
  - id: summary
    task: |
      Security: {nodes.security.output}
      Style: {nodes.style.output}
      Audits: {nodes.audit.output}
    depends: [security, style, audit]

  # Router + loop
  - id: decide
    type: router
    depends: [summary]
    routes:
      - match: "critical"
        to: [fix]
      - match: null
        to: [done]

  - id: fix
    task: Fix issues from {nodes.summary.output}
    depends: [decide]

  - id: verify
    type: router
    depends: [fix]
    routes:
      - match: "FAIL"
        to: [fix]
        max: 3
      - match: null
        to: [done]

  # Terminal (depends on all routers that can activate it)
  - id: done
    task: Final report from {nodes.summary.output}
    depends: [decide, verify]
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
| `timeout`        | `1800`  | Per-node timeout in seconds |

---

## Params

```yaml
params:
  - direction              # string → required, position 1
  - requirement            # required, position 2
  - depth: "deep"          # dict → optional with default, position 3
```

Also accepts explicit form: `- name: direction` / `- name: depth, default: "deep"`

User invocation: `/workflow run plan src security` → `direction=src, requirement=security, depth=deep`

Params available in templates as `{direction}` — no prefix.

---

## Node Fields

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

## Route Fields

| Field   | Required | Description |
|---------|----------|-------------|
| `match` | ✓        | Regex on upstream output. `null` = fallback. |
| `to`    | ✓        | Target node ID(s). String or list. |
| `max`   |          | Max fires for back-edge loops. `0` = unlimited. |

---

## Depends Rules

1. **Always add explicit `depends`**
2. **Router nodes**: must specify `depends`
3. **Router-gated targets**: add router as dependency
4. **Terminal nodes**: depend on all routers that can activate them

```yaml
# Router-gated target
- id: fix
  task: Fix issues
  depends: [route_q]

# Terminal node
- id: done
  task: Final report
  depends: [route_a, route_b]
```
