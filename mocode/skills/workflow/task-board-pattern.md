# Task Board Pattern

A loop workflow that uses a Markdown file as a shared task board. Nodes read the board, claim tasks one at a time, execute, and update progress.

Use this when you have a list of tasks to execute sequentially, each with its own implement → verify → commit cycle, and you want visible progress tracking.

---

## How It Works

```
┌──────────┐     ┌──────────┐     ┌──────────┐
│ do_task  │────▶│  check   │────▶│   done   │
│          │◀────│ (router) │     │ (report) │
└──────────┘     └──────────┘     └──────────┘
   loop              ALL_DONE
```

1. **do_task** — reads the board, finds the next unclaimed task, claims it, executes, updates the board
2. **check** (router) — if more unclaimed tasks exist, loops back; otherwise proceeds to done
3. **done** — generates a summary report

---

## Board Format

The board is a Markdown file at `{run_dir}/board.md` (inside the run output directory).

### Required

- **Checkboxes** — `- [ ]` for pending, `- [x]` for done
- **Status field** — `Status: unclaimed` / `Status: in-progress` / `Status: done`

### Optional (flexible)

Add whatever tracking fields make sense for your task type:

```markdown
- [ ] **A1** — Task title
  - Description: what needs to be done
  - Status: unclaimed
```

After completion, the node fills in results. Fields depend on task type:

**Example — refactoring task:**

```markdown
- [x] **A1** — Eliminate 11 levels of nesting
  - Approach: early return + extract methods
  - Status: done
  - Changes:
    - Refactored deep branches into guard clauses
    - Extracted _render_node_content()
  - Files: mocode/app/cli/workflow_renderer.py
  - Tests: all passed (120 passed)
  - Commit: a1b2c3d
```

**Example — research task:**

```markdown
- [x] **R1** — Research CLI interaction patterns
  - Status: done
  - Scope: oclif, commander, click
  - Findings:
    - oclif uses plugin architecture, good for large projects
    - click has the simplest decorator-based API
  - Reference: https://click.palletsprojects.com/
  - Conclusion: recommend click-style API design
```

---

## Workflow Template

```yaml
name: task-board
description: Iterative task execution with board-based progress tracking
params:
  - board                           # path to task board markdown file
concurrency: 1
max_iterations: 20

nodes:
  - id: do_task
    description: Read board, claim next unclaimed task, execute, update board
    task: |
      You are an engineer. The project directory is the current working directory.

      ## Read Board
      Read `{run_dir}/board.md`, find the first `- [ ]` task.
      If no unclaimed tasks remain, output "ALL_DONE" and stop.

      ## Claim
      Change `Status: unclaimed` to `Status: in-progress`, save the file.

      ## Execute
      Implement according to the task description. Follow project conventions.

      ## Verify
      Run relevant tests to ensure nothing is broken.
      If tests fail, fix and retry (max 3 attempts).

      ## Commit
      After tests pass, git commit.

      ## Update Board
      Count total tasks in `{run_dir}/board.md`.

      **If ≤ 8 tasks:**
      1. `- [ ]` → `- [x]`
      2. `Status: in-progress` → `Status: done`
      3. Fill in completion details directly in board.md below the task

      **If > 8 tasks:**
      1. `- [ ]` → `- [x]`
      2. `Status: in-progress` → `Status: done`
      3. Write full execution details to `{run_dir}/tasks/<task-id>.md`
      4. In board.md, only update the status — keep it as a summary table:
         `| ID | Task | Status | Detail |`

      ## Output
      Report: completed task, what was done, test results, commit hash.
    depends: []

  - id: check_more
    type: router
    depends: [do_task]
    routes:
      - match: "ALL_DONE"
        to: [done]
      - match: null
        to: [do_task]
        max: 15

  - id: done
    description: Generate summary
    task: |
      Read `{run_dir}/board.md`, summarize all completed tasks into a brief report.
    depends: [check_more]
```

---

## Output Directory

Each run produces a directory at `{run_dir}/` containing:

```
{run_dir}/
├── run.json           # Run metadata (auto-managed)
├── board.md           # Task board with status tracking
└── tasks/             # Per-task detail files (only when > 8 tasks)
    ├── A1-refactor-auth.md
    └── A2-add-tests.md
```

---

## Tips

- **`concurrency: 1`** — keep serial to avoid board file conflicts
- **`max_iterations`** — set higher than task count as safety net
- **Router `max`** — set to task count to prevent infinite loops
- **Board path** — `{run_dir}/board.md` is auto-created in the run output directory
- **Adaptive layout** — ≤ 8 tasks: details inline in board.md; > 8 tasks: board.md is a summary table, full details go to `tasks/<id>.md`
- **Failure handling** — if a task can't be completed after retries, revert status back to `unclaimed` and report the error
