# Writing plugins

A plugin contributes tools, slash commands, hooks and prompt sections to the host. The built-in capabilities — `filesystem`, `shell`, `skills`, `cli` — are written against exactly this API, so anything you can do in a plugin is the same thing MoCode does for itself.

## Anatomy

```
./.mocode/plugins/git-helper/     project-local, wins on name conflicts
~/.mocode/plugins/git-helper/     user-global
├── PLUGIN.md                     metadata (and docs for humans)
└── plugin.py                     code
```

A single `<name>.py` file works too, if you don't need documentation or resources. Discovery never imports anything: only enabled plugins get executed.

### PLUGIN.md

```markdown
---
name: git-helper
description: Git status tool and /branch command
version: 0.1.0
enabled: true
entrypoint: GitHelperPlugin
---

Free-form notes for whoever reads this later.
```

| Field | Required | Meaning |
|---|---|---|
| `name` | yes | Unique id — used for enable/disable, logging, conflict resolution |
| `description` | yes | Shown in listings and error messages |
| `version` / `author` | no | Display only |
| `enabled` | no | `false` disables it; `config.json` overrides this |
| `entrypoint` | no | Class (or instance) to use from `plugin.py` |

Without `entrypoint`, the loader takes the module-level `plugin` instance, and failing that the first `Plugin` subclass defined in the file.

### plugin.py

```python
from mocode.plugins import Plugin, Tool

class GitStatusTool(Tool):
    def __init__(self):
        super().__init__(
            name="git_status",
            description="Show the working tree status of the current git repository.",
            params={},
            func=self._run,
            tags=frozenset({"git"}),
        )

    def _run(self, args: dict) -> str:
        import subprocess
        return subprocess.run(["git", "status", "--short"],
                              capture_output=True, text=True).stdout


class GitHelperPlugin(Plugin):
    name = "git-helper"
    description = "Git status tool and /branch command"

    def build(self, ctx):
        ctx.tools.register(GitStatusTool())
```

Restart MoCode. A complete version of this lives in [`examples/plugins/git-status`](../examples/plugins/git-status).

## What `build(ctx)` can do

| Contribution | API | Notes |
|---|---|---|
| Tools | `ctx.tools.register(tool)` | share the store with `ctx.tools.select(...)` |
| Commands | `ctx.register(Command(...))` | interactive shell only in practice — slash commands still work in `-p` mode if they return a prompt |
| Hooks | `ctx.hooks.append(hook)` | see the lifecycle below |
| Prompt sections | `ctx.prompt_sections.append(Section(...))` | framework sections win a name collision |
| Own settings | `ctx.plugin_config("name")` | the `plugins.<name>` object from `config.json` |
| Model facts | `ctx.model` | name / `context_window` / `max_output`; known before assembly, so readable in `build()` |

`ctx.agent` is `None` during `build()` — the agent does not exist yet. Anything that needs it should hold `ctx` and read `ctx.agent` at call time.

## Hooks

Subclass `AgentHook` and override what you need:

| Method | When | You may |
|---|---|---|
| `before_iteration(ctx)` | before each LLM call | rewrite `ctx.messages`, `await ctx.emit(...)` |
| `on_response(ctx)` | after each response | read usage, reasoning, final content |
| `on_tool_start(ctx)` | before a tool runs | rewrite `ctx.tool_args`, set `ctx.deny` to veto |
| `on_tool_complete(ctx)` | after a tool runs | rewrite `ctx.tool_result`, read `ctx.status` |
| `after_tools(ctx)` | after a batch | rewrite messages |
| `after_iteration(ctx)` | when the loop ends | read totals |
| `on_event(event)` | any `ctx.emit` | render events, ignore the ones you don't know |

One hook raising never breaks the loop or the other hooks.

## Three worked examples

### Tool scoping — a restricted tool set

`Tool.tags` plus `ToolRegistry.select` replace name-based filtering:

```python
safe_tools = ctx.tools.select(exclude_tags={"shell", "fs-write"})
```

### A sub-agent tool

The kernel's `derive()` gives you an independent agent sharing the parent's provider:

```python
class SubAgentTool(Tool):
    def __init__(self, ctx):
        self._ctx = ctx
        super().__init__(name="sub_agent", description="Delegate a task to a sub-agent",
                         params={"task": {"type": "string", "description": "Task"}},
                         func=self._run, tags=frozenset({"delegation"}))

    async def _run(self, args: dict) -> str:
        parent = self._ctx.agent
        child = parent.derive(
            system_prompt=parent.system_prompt + "\n\nYou are a focused sub-agent.",
            tools=self._ctx.tools.select(exclude_tags={"delegation"}),  # no recursion
            config=parent.config.replace(max_iterations=50, tool_result_limit=0),
        )
        result = await child.run_with_messages([{"role": "user", "content": args["task"]}])
        return result.content
```

Nothing in `core/` knows what a sub-agent is.

### Context compaction

A hook can rewrite the message list directly; the event channel tells the UI:

```python
from dataclasses import dataclass
from mocode.plugins import AgentHook, Plugin

@dataclass
class Compacted:
    old_count: int
    new_count: int

class CompactHook(AgentHook):
    def __init__(self, ctx, threshold=0.8):
        self._ctx, self._threshold = ctx, threshold
        self._tokens = 0

    async def before_iteration(self, ctx):
        window = (self._ctx.model.context_window if self._ctx.model else None) or 0
        if not window:                       # unknown model → nothing to reason about
            return
        if ctx.usage:
            self._tokens = ctx.usage.prompt_tokens
        if self._tokens <= window * self._threshold:
            return
        old = len(ctx.messages)
        ctx.messages[:] = await summarize(self._ctx.agent.provider, ctx.messages)
        self._tokens = 0
        await ctx.emit(Compacted(old, len(ctx.messages)))

class CompactPlugin(Plugin):
    name = "compact"

    def build(self, ctx):
        ctx.hooks.append(CompactHook(ctx))
        if ctx.display:
            ctx.display.add_event_renderer(
                Compacted, lambda e: f"compacted {e.old_count} → {e.new_count} messages"
            )
```

Again, the kernel has no idea compaction exists; it only transports the event.

## Rules of the road

- **Plugins are trusted code.** Importing `plugin.py` executes it — same trust model as a pytest plugin. Only install plugins you would run yourself.
- **Name your tools and commands distinctively.** A later registration with the same name replaces an earlier one.
- **Built-in names are reserved** (`filesystem`, `shell`, `skills`, `cli`): a third-party plugin cannot shadow them. Overriding built-in behaviour means disabling the built-in and contributing your own tool under a different name.
- **One plugin, one name.** Project-local beats user-global; the loser is skipped rather than loaded twice.
- **Failures are contained.** Import errors and exceptions from `build()` are reported on stderr, the plugin is skipped, and the host starts normally.
- **Order is not guaranteed.** Built-ins load in a fixed order, third-party plugins sorted by name. Never depend on another plugin having run first.

## Disabling plugins

```jsonc
{
  "plugins": {
    "shell": { "enabled": false },
    "git-helper": { "enabled": true, "api_token": "..." }
  }
}
```

`config.json` wins over `PLUGIN.md`. Any keys other than `enabled` are handed to the plugin untouched via `ctx.plugin_config("<name>")`.
