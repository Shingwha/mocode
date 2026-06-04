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
