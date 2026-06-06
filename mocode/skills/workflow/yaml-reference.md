# Workflow YAML Reference

## Full Example

```yaml
name: code-review
description: Automated code review pipeline
max_iterations: 100           # optional, default 100
concurrency: 3                # optional, default 1 (max parallel nodes)

params:                       # optional — positional parameters
  - path                      # required, position 1
  - focus: "general"          # optional, position 2, default "general"

nodes:
  # ── Task (root) ────────────────────────────────────
  - id: scan
    description: Scan project for issues
    task: Scan {path} for {focus} issues.
    depends: []

  # ── Parallel fan-out ───────────────────────────────
  - id: security
    task: Review security in {nodes.scan.output}
    depends: [scan]

  - id: style
    task: Review style in {nodes.scan.output}
    depends: [scan]

  # ── Map (fan-out over list) ────────────────────────
  - id: list_modules
    task: List top-level modules, one per line.
    depends: [scan]

  - id: audit
    type: map
    items: "{nodes.list_modules.output}"
    item_key: module
    task: Audit {module} for {focus} issues.     # ← single braces
    depends: [list_modules]

  # ── Converge ───────────────────────────────────────
  - id: summary
    task: |
      Security: {nodes.security.output}
      Style: {nodes.style.output}
      Audits: {nodes.audit.output}
    depends: [security, style, audit]

  # ── Router (conditional branch) ────────────────────
  - id: decide
    type: router
    depends: [summary]
    routes:
      - match: "critical"
        to: [fix]
      - match: null              # fallback
        to: [done]

  # ── Loop with back-edge ────────────────────────────
  - id: fix
    task: Fix issues from {nodes.summary.output}
    depends: [decide]

  - id: verify
    type: router
    depends: [fix]
    routes:
      - match: "FAIL"
        to: [fix]
        max: 3                   # prevent infinite loop
      - match: null
        to: [done]

  # ── Terminal ───────────────────────────────────────
  - id: done
    task: Final report from {nodes.summary.output}
    depends: [decide, verify]    # explicit gate on both routers
```

---

## Top-level Fields

| Field            | Required | Default | Description |
|------------------|----------|---------|-------------|
| `name`           | ✓        | —       | Workflow identifier |
| `description`    |          | `""`    | One-line description |
| `params`         |          | `[]`    | Positional parameters (see below) |
| `nodes`          | ✓        | `[]`    | Node definitions |
| `max_iterations` |          | `100`   | Global safety limit on total executions |
| `concurrency`    |          | `1`     | Max parallel nodes (`1` = serial) |

---

## Params

Define positional parameters. Users pass them in order or as `key=value`.

```yaml
params:
  - direction              # string → required, position 1
  - requirement            # required, position 2
  - depth: "deep"          # dict → optional with default, position 3
```

Also accepts explicit dict form:

```yaml
params:
  - name: direction
  - name: depth
    default: "deep"
```

**User invocation**:

```bash
/workflow run plan 做CLI 好用           # direction=做CLI, requirement=好用, depth=deep
/workflow run plan 做CLI 好用 shallow   # depth=shallow (override)
/workflow run plan 做CLI depth=deep     # key=value also works
```

Params are available in templates as `{direction}`, `{depth}` — no prefix needed.

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
| `match` | ✓        | Regex on concatenated dependency output. `null` = fallback. |
| `to`    | ✓        | Target node ID(s). String or list. |
| `max`   |          | Max fires (for back-edge loops). `0` = unlimited. |

---

## Template Variables

Single-segment `{name}` → context top level. Multi-segment `{a.b}` → nested dict.

| Variable | Source |
|----------|--------|
| `{param}` | Workflow parameter |
| `{nodes.<id>.output}` | Node output |
| `{nodes.<id>.exit_code}` | Exit code |
| `{nodes.<id>.error}` | Error text |
| `{nodes.<id>.duration}` | Seconds |
| `{previous}` | Last completed output |
| `{env.VAR}` | Environment variable |
| `{item}` | Current map item (in map tasks) |

> `{node.X.*}` works as alias for `{nodes.X.*}`.

---

## Node Type Details

### Task

Default type. Template filled, sent to LLM.

```yaml
- id: analyze
  task: |
    Analyze {path}.
    Focus on: {nodes.scan.output}
  depends: [scan]
```

### Router

Regex conditions on upstream output. First match wins.

```yaml
- id: decide
  type: router
  depends: [analyze]
  routes:
    - match: "严重"
      to: [deep_fix]
    - match: "需改进"
      to: [improve]
    - match: null            # fallback — always last
      to: [done]
```

**Rules**: must have `routes`, must NOT have `task`, must have explicit `depends`.

### Map

Fan out over list. Each item → child subprocess.

```yaml
- id: topics
  task: List 3 topics, one per line.

- id: research
  type: map
  items: "{nodes.topics.output}"
  item_key: topic
  task: Summarize {topic} in 2 sentences.    # single braces
  depends: [topics]
```

**How it works**:
1. `items` template fills → split by newlines
2. Each line → one child task with `{item_key}` substituted
3. Children run concurrently (bounded by `concurrency`)
4. Outputs joined with `\n---\n` into `{nodes.<id>.output}`

**Rules**: must have `items` + `task`, must NOT have `routes`.

---

## Depends Rules

1. **Always add explicit `depends`** — even when auto-inference works
2. **Router nodes**: must specify `depends` (no task to infer from)
3. **Router-gated targets**: add router as dependency
4. **Terminal nodes**: depend on all routers that can activate them

```yaml
# Router-gated target
- id: fix
  task: Fix issues
  depends: [route_q]         # gate dependency

# Terminal node
- id: done
  task: Final report
  depends: [route_a, route_b]  # both routers can activate
```
