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

  # Fan-out with each
  - id: list_modules
    task: |
      List top-level modules, one per line.
      Use [MODULE] tags:
      [MODULE]
      module_name
    depends: [scan]

  - id: audit
    each: "{nodes.list_modules.MODULE}"
    as: module
    task: Audit {module} for {focus} issues.
    depends: [list_modules]

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

| Field         | task | task+each | router | Default  | Description |
|---------------|------|-----------|--------|----------|-------------|
| `id`          | ✓    | ✓         | ✓      | —        | Unique identifier |
| `type`        |      |           | `"router"` | `"task"` | Only router needs declaration |
| `description` |      |           |        | `""`     | Human-readable label |
| `task`        | ✓    | ✓         | ✗      | `""`     | Prompt template |
| `depends`     | rec  | rec       | **req** | `[]`     | Upstream node IDs |
| `routes`      | ✗    | ✗         | ✓      | `[]`     | Route rules |
| `each`        | —    | ✓         | —      | `""`     | List source (resolves to list or splits by newlines) |
| `as`          | —    | **req**   | —      | `""`     | Iteration variable name |

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

---

## `[TAG]` Output Protocol

Nodes can output structured sections using `[TAG]` markers. Downstream nodes reference them by tag name.

```
[ISSUE]
src/auth.py:42 — SQL injection risk
User input directly concatenated into SQL query

[ISSUE]
src/config.py:15 — hardcoded secret

[FILE]
src/auth.py
src/config.py

[VERDICT]
Overall risk: HIGH
```

**Rules**:
- Tag line: `[TAG]` at line start, TAG matches `[a-zA-Z_]\w*`
- Content: from tag line to next tag line (or EOF), stripped
- Same-name tags → merged into `list[str]`
- Text before the first tag is ignored

**References**:

| Expression | Result | Type |
|------------|--------|------|
| `{nodes.scan.output}` | Raw output text | `str` |
| `{nodes.scan.ISSUE}` | All ISSUE sections | `list[str]` (joined with `\n---\n` in templates) |
| `{nodes.scan.ISSUE[0]}` | First ISSUE | `str` |
| `{nodes.scan.VERDICT[0]}` | First VERDICT | `str` |

---

## Template Variables

| Expression | Resolves to |
|------------|-------------|
| `{param_name}` | Workflow param value |
| `{nodes.X.output}` | Raw node output |
| `{nodes.X.TAG}` | `[TAG]` sections (list, joined with `\n---\n`) |
| `{nodes.X.TAG[0]}` | First `[TAG]` section |
| `{nodes.X.exit_code}` | Node exit code |
| `{nodes.X.error}` | Node error message |
| `{nodes.X.duration}` | Node execution duration |
| `{previous}` | Last completed node output |
| `{env.VAR}` | Environment variable |
| `{run_dir}` | Run output directory path (e.g. `~/.mocode/runs/wf_abc123`) |
| `{as_var}` | Each iteration variable (inside fan-out) |
