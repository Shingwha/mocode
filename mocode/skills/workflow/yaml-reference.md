# Workflow YAML Format Reference

## Full Example

```yaml
name: my-workflow
description: What this workflow does
max_iterations: 100          # optional, default 100
concurrency: 3               # optional, default 1 (max parallel nodes)

nodes:
  # ── Task node (root) ──────────────────────────────
  - id: overview
    description: Analyze project structure
    task: Analyze project structure in {args.path}
    depends: []              # root node — no dependencies

  # ── Parallel fan-out (explicit depends) ───────────
  - id: check_security
    description: Security review
    task: Review security issues in {nodes.overview.output}
    depends: [overview]

  - id: check_style
    description: Style review
    task: Review style issues in {nodes.overview.output}
    depends: [overview]

  # ── Map node (fan-out over list) ──────────────────
  - id: list_modules
    task: |
      List the top-level modules in the project, one per line.
    depends: [overview]

  - id: audit_modules
    description: Audit each module
    type: map
    items: "{nodes.list_modules.output}"
    item_key: module
    task: Audit module {module} for issues.
    depends: [list_modules]

  # ── Converge ──────────────────────────────────────
  - id: summary
    task: |
      Security: {nodes.check_security.output}
      Style: {nodes.check_style.output}
      Module audits: {nodes.audit_modules.output}
    depends: [check_security, check_style, audit_modules]

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
    task: Fix issues from {nodes.summary.output}
    depends: [decide]

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
    task: Generate final report from {nodes.summary.output}
    depends: [decide, verify]   # explicit gate on routers
```

## Field Reference

### Top-level fields

| Field            | Required | Default | Description |
|------------------|----------|---------|-------------|
| `name`           | yes      | —       | Workflow identifier (used in CLI) |
| `description`    | yes      | —       | One-line description |
| `max_iterations` | no       | 100     | Global safety limit on total node executions |
| `concurrency`    | no       | 1       | Max parallel node execution. `1` = serial. Map child tasks also respect this limit. |

### Node fields

| Field         | Required     | Default  | Description |
|---------------|-------------|----------|-------------|
| `id`          | yes         | —        | Unique node identifier |
| `type`        | no          | `"task"` | `"task"`, `"router"`, or `"map"` |
| `description` | no          | `""`     | Human-readable label |
| `task`        | task, map   | `""`     | Prompt template with `{}` variables |
| `depends`     | recommended | `[]`     | List of upstream node IDs. **Always add explicitly.** |
| `routes`      | router      | `[]`     | List of route rules (see below) |
| `items`       | map         | `""`     | Template resolving to newline-separated list |
| `item_key`    | no          | `"item"` | Variable name in `task` replaced per item value |

### Route fields

| Field   | Required | Description |
|---------|----------|-------------|
| `match` | yes      | Regex tested against concatenated dependency output. `null` = unconditional fallback. |
| `to`    | yes      | Target node ID(s). String or list. |
| `max`   | no       | Max times this route can fire (for back-edge loops). `0` = unlimited. |

## Node Types in Detail

### Task Node

The default type. Has a `task` template that is filled and sent to the LLM.

```yaml
- id: analyze
  description: Analyze code quality
  task: |
    Analyze the code in {args.path}.
    Focus on: {nodes.scan.output}
  depends: [scan]
```

### Router Node

Evaluates regex conditions against the concatenated output of its `dependencies`.
No `task` field. First matching route wins. Unmatched nodes downstream are skipped.

```yaml
- id: route_quality
  type: router
  depends: [analyze]
  routes:
    - match: "严重问题"
      to: [deep_fix]
    - match: "需改进"
      to: [improve]
    - match: null           # fallback — always matches
      to: [done]
```

**Rules:**
- Must have `routes`, must NOT have `task`
- Must have explicit `depends` (no task to infer from)
- `match: null` is the unconditional fallback (put last)
- First match wins — order matters
- Back-edge routes (targeting upstream nodes) create loops; always set `max`

### Map Node

Fans out a task over a list. Each item spawns a child `mocode -p` subprocess.

```yaml
- id: list_topics
  task: List 3 research topics, one per line.
  depends: []

- id: research
  type: map
  items: "{nodes.list_topics.output}"
  item_key: topic
  task: Research "{topic}" and summarize in 3 sentences.
  depends: [list_topics]
```

**How it works:**
1. `items` template is filled, then split by newlines into a list
2. Each non-empty line becomes one child task
3. `{item_key}` (default `"item"`) in `task` is replaced with the line value
4. All children run concurrently (governed by `concurrency`)
5. Child outputs are concatenated with `\n---\n` into `{nodes.<id>.output}`
6. If `items` resolves to empty, the node produces empty output

**Rules:**
- Must have both `items` and `task`
- Must NOT have `routes`
- `depends` is auto-inferred from both `task` and `items` templates, but always add explicit `depends` for clarity

## Depends Best Practices

1. **Always add explicit `depends`** — even when auto-inference would work.
   Explicit dependencies make the DAG structure readable and prevent subtle
   ordering issues.

2. **Router nodes require explicit `depends`** — they have no `task` to infer from.

3. **Map nodes**: add `depends` for the `items` source, even though auto-inference
   catches `{nodes.X.*}` in the `items` template.

4. **Router-gated targets**: if a node is only activated via a router's `route.to`
   (not via a data dependency), add the router as an explicit dependency:
   ```yaml
   - id: fix
     task: Fix {nodes.report.output}
     depends: [route_severity]   # gate dependency
   ```

5. **Terminal nodes in branching workflows**: depend on all routers that can
   activate them:
   ```yaml
   - id: done
     task: Generate report
     depends: [route_a, route_b]  # both routers can activate this
   ```
