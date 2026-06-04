---
name: workflow
description: Design, create, and run MoCode Workflows — DAG-based multi-step task orchestration with parallel nodes, conditional routing, and loops. Use when the user describes a multi-step process, pipeline, or needs to orchestrate sequential/parallel tasks.
---

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
