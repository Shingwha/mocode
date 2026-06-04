"""Built-in skill: Workflow — DAG-based multi-step task orchestration.

Teaches the agent how to design, create, and run MoCode Workflows so it
can guide users through the workflow lifecycle.
"""

from __future__ import annotations

from mocode.core.skill import Skill

_SKILL_CONTENT = """# MoCode Workflows

MoCode Workflows let you orchestrate multi-step, multi-agent tasks as a
**directed acyclic graph (DAG)** — each node is a self-contained prompt
sent to the LLM, and nodes can fan-out (parallel), fan-in (converge),
route conditionally, and even loop with back-edges.

They are defined as YAML files in `.mocode/workflows/` and run via the
`/workflow run <name>` command or the `mocode workflow` CLI.

---

## Quick Start

Write a `.mocode/workflows/<name>.yaml` file (see YAML Format below), then:

```
# Interactive
/workflow run my-workflow path=.

# CLI (background by default — prints run_id, exits immediately)
mocode workflow run my-workflow path=.

# CLI (foreground — block until done)
mocode workflow run my-workflow --fg path=.
```

---

## YAML Format

```yaml
name: my-workflow
description: What this workflow does
max_iterations: 100          # optional, default 100

nodes:
  # ── Root task node ─────────────────────────────────
  - id: overview
    description: Analyze project structure
    task: Analyze project structure in {{args.path}}

  # ── Parallel nodes (depends auto-inferred) ─────────
  - id: check_security
    description: Review security issues
    task: Review security issues in {{nodes.overview.output}}

  - id: check_style
    description: Review style issues
    task: Review style issues in {{nodes.overview.output}}

  # ── Converge node ──────────────────────────────────
  - id: summary
    description: Generate final report
    task: |
      Security: {{nodes.check_security.output}}
      Style: {{nodes.check_style.output}}

  # ── Router node (conditional branching) ────────────
  - id: decide
    type: router
    depends: [summary]
    routes:
      - match: "critical"
        to: [fix]
      - match: null          # fallback
        to: [done]

  # ── Router-gated node ──────────────────────────────
  - id: fix
    description: Fix critical issues
    task: Fix issues from {{nodes.summary.output}}

  # ── Router with loop (back-edge) ───────────────────
  - id: verify
    type: router
    depends: [fix]
    routes:
      - match: "FAIL"
        to: [fix]
        max: 3               # max 3 retries
      - match: null
        to: [done]

  # ── Terminal node ──────────────────────────────────
  - id: done
    description: Write final output
    task: Generate final report from {{nodes.summary.output}}
    depends: [decide, verify]   # explicit gate dependency on routers
```

---

## Node Types

| Type     | Description                                                           |
|----------|-----------------------------------------------------------------------|
| `task`   | Has a `task` template string. Sent to the LLM as a prompt.            |
| `router` | No `task`. Evaluates `routes` — regex-matched against dependency output. |

---

## Template Variables

Reference upstream node outputs and other context in `task` templates:

| Variable                      | Resolves To                                    |
|------------------------------|-------------------------------------------------|
| `{{args.key}}`               | CLI argument passed to `/workflow run`          |
| `{{nodes.<id>.output}}`      | Full output text of node `<id>`                 |
| `{{nodes.<id>.exit_code}}`   | Exit code (0 = OK)                              |
| `{{nodes.<id>.error}}`       | Error message (if any)                          |
| `{{nodes.<id>.duration}}`    | Execution time in seconds                       |
| `{{previous}}`               | Output of the node that just completed           |
| `{{env.VAR}}`                | Environment variable                            |

---

## Depends Inference

**Task nodes**: `depends` is **auto-inferred** from `{{nodes.<id>.*}}`
references in the `task` template. You only need explicit `depends` for:

1. **Gate dependencies on routers** — routers have no `output` to reference
2. **Ordering constraints without data flow** (e.g., cleanup after all nodes)

**Router nodes**: ALWAYS need explicit `depends` — there is no task template
to infer from.

---

## Loops & Safety

Back-edge routes (a router pointing back to an upstream node) create loops.
**Always set `max: <N>` on back-edge routes** to prevent infinite loops.

The workflow-level `max_iterations` (default 100) is a global safety net.

---

## CLI Commands

**IMPORTANT:**
- Use `mocode` directly in bash — do NOT use `python -m mocode`. The `mocode` executable is already on PATH.
- Workflows run for a **very long time** (each node spawns an LLM subprocess). Always run in background (default) unless the user explicitly asks to wait. Use `--fg` only when foreground execution is specifically requested.

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

Results are persisted to `~/.mocode/workflow_runs/<run_id>.json`.

---

## How to Guide Users

When a user describes a multi-step task suitable for orchestration:

1. Ask clarifying questions about the steps and their dependencies
2. Write a `.mocode/workflows/<name>.yaml` file directly
3. Run with `/workflow run <name>`
4. Inspect results and iterate if needed

Common patterns to suggest:
- **Code review pipeline**: lint → security audit → test → report
- **Data pipeline**: extract → transform → validate → load
- **Research workflow**: search → read → summarize → synthesize
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
    )
