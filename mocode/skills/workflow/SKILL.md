---
name: workflow
description: Design, create, and run MoCode Workflows — DAG-based multi-step task orchestration with parallel nodes, conditional routing, map fan-out, and loops. Use when the user describes a multi-step process, pipeline, or needs to orchestrate sequential/parallel tasks.
---

# MoCode Workflows

MoCode Workflows orchestrate multi-step tasks as a **DAG** (directed acyclic
graph). Each node is a self-contained LLM prompt. Nodes can fan-out (parallel),
fan-in (converge), branch conditionally (router), map over lists, and loop
with safety limits.

Defined as YAML files in `.mocode/workflows/`, run via `mocode workflow` CLI.

---

## Node Types

| Type     | Description                                                            |
|----------|------------------------------------------------------------------------|
| `task`   | Has a `task` template. Sent to the LLM as a prompt via `mocode -p`.    |
| `router` | No `task`. Regex-matches `routes` against upstream output. First match wins. |
| `map`    | Has `items` + `task`. Fans out to N child tasks (one per item). Results are concatenated with `\n---\n`. |

## Dependencies (`depends`)

**Always add explicit `depends` on every node.** Auto-inference is a convenience
fallback, not a substitute for clear intent. Explicit `depends` makes the DAG
readable, prevents subtle ordering bugs, and is required in some cases.

| Node Type | Auto-inference | Explicit `depends` required? |
|-----------|---------------|------------------------------|
| `task`    | From `{nodes.X.*}` in `task` | **Recommended** — add even when auto-inference works |
| `router`  | None (no `task` to infer from) | **Required** — always specify `depends` |
| `map`     | From `{nodes.X.*}` in `task` and `items` | **Recommended** — especially for the `items` source |

**Router-gated targets**: When a downstream node is only activated by a router
(not via `depends`), add the router as an explicit dependency on that target:

```yaml
- id: route_quality
  type: router
  depends: [analyze]          # required
  routes:
    - match: "critical"
      to: [fix]
    - match: null
      to: [done]

- id: fix
  task: Fix {nodes.analyze.output}
  depends: [route_quality]    # gate dependency on router
```

## Template Variables

| Variable                    | Resolves To                                |
|-----------------------------|--------------------------------------------|
| `{args.key}`                | CLI argument at run time (`key=value`)     |
| `{nodes.<id>.output}`      | Full output of node `<id>`                 |
| `{nodes.<id>.exit_code}`   | Exit code (0 = OK)                         |
| `{nodes.<id>.error}`       | Error message                              |
| `{nodes.<id>.duration}`    | Seconds elapsed                            |
| `{previous}`                | Output of the just-completed node          |
| `{env.VAR}`                 | Environment variable                       |

> `{node.X.*}` also works as an alias for `{nodes.X.*}`.

## Map Nodes

Map nodes fan out a task over a list of items. Each item spawns an independent
`mocode -p` child process (concurrency controlled by workflow-level `concurrency`).

| Field      | Required | Default  | Description |
|------------|----------|----------|-------------|
| `items`    | yes      | —        | Template resolving to newline-separated list |
| `item_key` | no       | `"item"` | Variable name in `task` replaced per item |

Child outputs are concatenated with `\n---\n` into a single
`{nodes.<map_id>.output}`.

```yaml
- id: topics
  task: |
    List 3 topics about AI, one per line.

- id: explore
  type: map
  items: "{nodes.topics.output}"
  item_key: topic
  task: Write 2 sentences about {topic}.
  depends: [topics]
```

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
