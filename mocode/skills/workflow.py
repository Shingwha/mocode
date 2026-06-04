"""Built-in skill: Workflow — DAG-based multi-step task orchestration.

Teaches the agent how to design, create, and run MoCode Workflows so it
can guide users through the workflow lifecycle.
"""

from __future__ import annotations

from mocode.core.skill import Skill

_SKILL_CONTENT = """\
# MoCode Workflows

MoCode Workflows orchestrate multi-step tasks as a **DAG** (directed acyclic
graph). Each node is a self-contained LLM prompt. Nodes can fan-out (parallel),
fan-in (converge), branch conditionally (router), and loop with safety limits.

Defined as YAML files in `.mocode/workflows/`, run via `mocode workflow` CLI.

---

## Node Types

| Type     | Description |
|----------|-------------|
| `task`   | Has a `task` template. Sent to the LLM as a prompt. |
| `router` | No `task`. Regex-matches `routes` against upstream output. |

## Template Variables

| Variable                    | Resolves To |
|-----------------------------|-------------|
| `{{args.key}}`              | CLI argument at run time |
| `{{nodes.<id>.output}}`    | Full output of node `<id>` |
| `{{nodes.<id>.exit_code}}` | Exit code (0 = OK) |
| `{{nodes.<id>.error}}`     | Error message |
| `{{nodes.<id>.duration}}`  | Seconds elapsed |
| `{{previous}}`             | Output of the just-completed node |
| `{{env.VAR}}`              | Environment variable |

## Dependencies

- **Task nodes**: `depends` is auto-inferred from `{{nodes.<id>.*}}` in the
  `task` template. Only add explicit `depends` for router gate deps or
  ordering without data flow.
- **Router nodes**: Always need explicit `depends`.

## Loops & Safety

Back-edge routes (router pointing upstream) create loops. **Always set
`max: <N>`** on back-edge routes. Workflow-level `max_iterations` (default
100) is the global safety net.

---

## Notes

- Each node runs as an independent `mocode -p` subprocess — tool access and
  config are inherited, but state is not shared between nodes.
- Background runs survive shell exit; foreground runs are killed on Ctrl+C.
- `status` detects crashed processes (PID dead but status stuck at "running").
- Use `mocode workflow result <run_id> --json` for programmatic consumption.

---

Detailed reference:
- YAML format and examples: `read vfs://workflow/yaml-reference.md`
- CLI command manual: `read vfs://workflow/cli-reference.md`
"""

_YAML_REFERENCE = """\
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
"""

_CLI_REFERENCE = """\
# Workflow CLI Reference

> Use `mocode` directly — NOT `python -m mocode`.

## Commands

```
mocode workflow run <name> [key=value...]       # background (default), prints run_id
mocode workflow run <name> --fg [key=value...]  # foreground, blocks until done
mocode workflow list                            # list available workflows
mocode workflow show <name>                     # show DAG structure
mocode workflow status [run_id]                 # check run status (latest if omitted)
mocode workflow result [run_id]                 # print full results (--json for raw)
mocode workflow stop <run_id>                   # kill background workflow
mocode workflow runs                            # list recent runs
```

Results persist to `~/.mocode/workflow_runs/<run_id>.json`.

## Typical Usage Flow

```
1. mocode workflow run my-workflow path=.        → prints run_id, returns immediately
2. sleep 60                                      → wait (workflows take minutes to hours)
3. mocode workflow status <run_id>               → check progress (nodes done/failed/skipped)
4. mocode workflow result <run_id>               → get full output when done
```

## Guiding Users

1. Clarify steps and dependencies → write `.mocode/workflows/<name>.yaml`
2. Run in background (default) — each node spawns an LLM subprocess, so execution is slow
3. Use `sleep <N>` to wait, then `status` to poll; repeat until done
4. Use `result` to retrieve output; use `stop` to cancel if needed

## Common Patterns

- **Code review**: lint → security audit → test → report
- **Data pipeline**: extract → transform → validate → load
- **Research**: search → read → summarize → synthesize
- **Document generation**: outline → draft → review → polish
"""


def WorkflowSkill() -> Skill:
    """MoCode Workflows — DAG-based multi-step task orchestration."""
    return Skill.builtin(
        name="workflow",
        description=(
            "Design, create, and run MoCode Workflows — DAG-based multi-step "
            "task orchestration with parallel nodes, conditional routing, and "
            "loops. Use when the user describes a multi-step process, pipeline, "
            "or needs to orchestrate sequential/parallel tasks."
        ),
        content=_SKILL_CONTENT,
        virtual_files={
            "vfs://workflow/yaml-reference.md": _YAML_REFERENCE,
            "vfs://workflow/cli-reference.md": _CLI_REFERENCE,
        },
    )
