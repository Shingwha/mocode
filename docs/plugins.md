# Writing plugins

A plugin contributes to MoCode. What it can contribute, and who can use the
result, depends on where it puts the code — because a plugin directory follows
the [Agent Plugins](https://agent-plugins.org) standard:

```
git-helper/
├── plugin.json                  the manifest: name, version, description
├── skills/<name>/SKILL.md       portable skills — any compatible client
├── mcp.json                     MCP servers (recognised, not served yet)
├── mocode/plugin.py             contributions to the agent — every frontend
└── mocode.cli/plugin.py         contributions to the terminal — this frontend only
```

The root belongs to the standard; everything client-specific lives under a
directory named for the namespace that defines it. Another client reading this
directory picks up `skills/`, ignores `mocode/` and `mocode.cli/`, and vice
versa.

**Two surfaces, and the difference is who can use the result.**

| | `mocode/plugin.py` | `mocode.cli/plugin.py` |
|---|---|---|
| interface | `Plugin.build(ctx)` | `CLIPlugin.build(cli)` |
| contributes | tools, prompt sections, hooks, shared commands | chrome: picker commands, keybindings |
| reaches | `ctx` — home, cwd, config, model, tools, commands, hooks, `plugin_sources`, the agent | the terminal — `commands`, `display`, `input`, `conversation` |
| works in | every MoCode frontend | this one |

A plugin is built once per **conversation**, against a context that describes
that conversation's project. It never learns whether a terminal is attached:
anything it wants to say goes out as an event, and whatever is watching draws
it.

## Anatomy

```
./.mocode/plugins/git-helper/     project-local, wins on name conflicts
~/.mocode/plugins/git-helper/     user-global
```

A single `<name>.py` file works too, for a plugin with no portable parts.
Discovery never imports anything: only enabled plugins get executed.

### plugin.json

```json
{
  "$schema": "https://agent-plugins.org/schemas/v1.json",
  "name": "git-helper",
  "version": "0.1.0",
  "description": "Git status tool and /branch command"
}
```

`name` is required — a unique id used for enable/disable, logging and conflict
resolution; lowercase alphanumerics, `-`, `.`, 1–64 characters, no `--` or
`..`. Everything else (`version`, `description`, `author`, `license`,
`homepage`, `repository`, `keywords`, `extensions`) is optional identity and
display. The schema is closed: unknown top-level fields are reported and
ignored, and a manifest that violates it rejects the plugin rather than
half-loading it. There is no `enabled` field — enabling is config (see the
end of this document).

### mocode/plugin.py

```python
from mocode.plugins import Plugin, Tool

class GitStatusTool(Tool):
    def __init__(self, cwd):
        super().__init__(
            name="git_status",
            description="Show the working tree status of the conversation's repo.",
            params={},
            func=self._run,
            tags=frozenset({"git"}),
        )
        self._cwd = cwd

    def _run(self, args: dict) -> str:
        import subprocess
        return subprocess.run(["git", "status", "--short"], cwd=self._cwd,
                              capture_output=True, text=True).stdout


class GitHelperPlugin(Plugin):
    name = "git-helper"
    description = "Git status tool and /branch command"

    def build(self, ctx):
        ctx.tools.register(GitStatusTool(ctx.cwd))
```

Restart MoCode. A complete version of this — with a skill and a terminal
command — lives in [`examples/plugins/git-status`](../examples/plugins/git-status).
The loader resolves the plugin from the module: a module-level `plugin`
instance wins, then the first `Plugin` subclass the module defines itself.

## What `build(ctx)` can do

| Contribution | API |
|---|---|
| Tools | `ctx.tools.register(tool)` — relative paths should resolve against `ctx.cwd` |
| Commands | `ctx.register(Command(...))` — a *shared* command, dispatchable from any frontend |
| Hooks | `ctx.hooks.append(hook)` |
| Prompt sections | `ctx.prompt_sections.append(Section(...))` — framework sections win a name collision |
| Own settings | `ctx.plugin_config("name")` — the `plugins.<name>` object from config.json |
| Model facts | `ctx.model` — name / `context_window` / `max_output`, readable in `build()` |
| Files it ships | `ctx.plugin_sources` — the directories the project's plugins were loaded from |
| Messages to the user | `await ctx.emit(Notice(...))`, or `ctx.conversation.notify(...)` at call time |

`ctx.agent` is `None` during `build()` — the agent does not exist yet. Anything
that needs it holds `ctx` and reads `ctx.agent` at call time; `ctx.emit` works
at call time too, during a run or between runs.

### One plugin instance, many builds

A plugin object is created once, and `build()` runs once per conversation.
**Keep the plugin itself stateless**: anything belonging to a conversation — a
session handle, a cache, a counter — is created *inside* `build()`, not stored
on `self`. All three shipped plugins work this way; `ShellPlugin.build` makes
a fresh `BashTool`, and with it a fresh working directory and environment.
That invariant is what lets one process serve several conversations from a
single loaded plugin list. `close(ctx)` is where anything you acquired for a
conversation is released.

## Hooks

A hook is the **interception** channel: it runs at a fixed point and may
change what happens. Everything a hook *watches* comes from the event stream
instead — an event is a notification, a hook is a request that expects an
answer.

| Method | When | You may |
|---|---|---|
| `before_iteration(ctx)` | before each LLM call | rewrite `ctx.messages` or `ctx.system_prompt`, `await ctx.emit(...)` |
| `on_tool_start(ctx)` | before a tool runs | rewrite `ctx.tool_args`, set `ctx.deny` to veto |
| `on_tool_complete(ctx)` | after a tool runs | rewrite `ctx.tool_result`, enrich `ctx.tool_details`, read `ctx.status` |
| `on_event(event)` | every event the run publishes | observe, accumulate, ignore |

`on_event` runs *inline*: the loop waits for it, so it sees every event before
the run moves on. A hook that only watches for its own purposes may also
subscribe (`ctx.agent.channel.subscribe()`), which nobody waits for — see
[embedding.md](embedding.md).

A `system_prompt` a hook writes is permanent for the conversation — it becomes
the agent's prompt from then on, across turns. Write it once to give an
application a persona, or recompute it every iteration to inject something
that changes. One hook raising never breaks the loop or the other hooks.

## Changing the harness after assembly

`build()` is the one *contribution* pass, but it is not the only moment the
harness can change: the registries are live for the conversation's whole life,
and the loop reads them back on every iteration. A host application — or a
harness that reshapes itself between task batches — never restarts anything:

- **`ctx.tools`** — `register` / `unregister` / `enable` / `disable` at any
  moment. Each provider request is built from the registry as it stands
  (`all_schemas()` is read per call), so a change takes effect on the **next
  iteration**, mid-run included.
- **Prompt sections** — `ctx.prompt_sections` feeds the prompt when it is
  rendered. Changes apply at the next render: a new conversation, or
  `conversation.rebuild_prompt()`. Inside a running conversation, a hook
  writing `ctx.system_prompt` in `before_iteration` is how the prompt changes.
- **Hooks** — `agent.hooks.add(hook)` takes effect at the next interception
  point. Hooks run in the order they were added and share one context object,
  so a change an earlier hook made is what a later one sees.

The split of labour is the contract: *inside* a running turn the only writes
are the hook points (`before_iteration`, `on_tool_start`, `on_tool_complete`);
*between* turns, anything the public API allows. An application that evolves
its own harness — swapping tool sets, tuning sections, adding hooks — works
entirely on the second side of that line.

## Three worked examples

### Tool scoping — a restricted tool set

`Tool.tags` plus `ToolRegistry.select` replace name-based filtering:

```python
safe_tools = ctx.tools.select(exclude_tags={"shell", "fs-write"})
```

### A sub-agent tool

The kernel's `derive()` gives you an independent agent: a fresh history, a
*copy* of the parent's capability set and policy, and by default the parent's
provider — override any of them per child:

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
            channel=parent.channel,       # let the host's readers see the nested work
        )
        result = await child.run_with_messages([{"role": "user", "content": args["task"]}])
        if result.had_error:              # run_with_messages never raises
            return f"error: {result.content}"
        return result.content
```

The child's events on the parent's channel reach channel subscribers (the
terminal's renderer, a logger), while the parent's own `Turn` views and
`state` stay scoped to the parent's run — watch a child through the child.
Nothing in `core/` knows what a sub-agent is.

### Context compaction

Usage is *observed* from the event stream; the message list is *rewritten*
through the hook:

```python
@dataclass
class Compacted(Event):
    type = "compacted"       # the discriminator a consumer switches on
    old_count: int = 0
    new_count: int = 0

    def summary(self) -> str:            # so any frontend can show it
        return f"compacted {self.old_count} → {self.new_count}"


class CompactHook(AgentHook):
    def __init__(self, ctx):
        self._ctx = ctx
        self._tokens = 0

    async def on_event(self, event):
        if isinstance(event, IterationFinished) and event.usage:
            self._tokens += event.usage.prompt_tokens

    async def before_iteration(self, ctx):
        window = self._ctx.model.context_window if self._ctx.model else None
        if not window or self._tokens <= window * 0.8:
            return
        old = len(ctx.messages)
        ctx.messages[:] = await summarize(self._ctx.agent.provider, ctx.messages)
        self._tokens = 0
        await ctx.emit(Compacted(old_count=old, new_count=len(ctx.messages)))


class CompactPlugin(Plugin):
    name = "compact"

    def build(self, ctx):
        ctx.hooks.append(CompactHook(ctx))
```

Again, the kernel has no idea compaction exists. Note what `build` does *not*
do: register a renderer. The event describes itself — that is what `summary()`
is for — so a terminal, a web UI and a log all display it without the plugin
knowing any of them exist.

## Contributing to the terminal

Chrome belongs to the frontend, so it goes in that frontend's namespace:

```python
from mocode.cli import CLIPlugin
from mocode.host.command import CONTINUE, Command


async def _status(ctx):
    # Terminal-only: it can print a block, not just a one-line notice.
    await ctx.conversation.notify("Working tree:\n" + git("status", "--short"))
    return CONTINUE


class GitHelperCLI(CLIPlugin):
    name = "git-helper.cli"

    def build(self, cli):
        cli.commands.register(Command("/status", "Show git status", handler=_status))
        # also: cli.display, cli.input, cli.conversation, cli.runtime
```

The two namespaces never import each other; when they need to cooperate, they
go through the conversation, which is the only thing they share. The
terminal's own commands are the first implementation of this interface
(`cli/plugin.py::BuiltinCommands`), so there is one way to contribute here.

## Reporting a result the model doesn't need

A tool returns a string, and the model reads it. When there is also something
*about* the result worth showing — an exit code, how many lines came back —
return a `ToolResult` instead:

```python
class LintTool(Tool):
    def __init__(self):
        super().__init__(
            name="lint",
            description="Lint a file",
            params={"path": {"type": "string", "description": "File to lint"}},
            func=self._run,
            summary_key="path",     # from the arguments
            result_key="issues",    # from the details, on the same line
        )

    def _run(self, args: dict) -> ToolResult:
        issues = run_linter(args["path"])
        return ToolResult(
            content=render(issues),          # what the model reads
            details={"issues": len(issues), "clean": not issues},
        )
```

The terminal shows `✓ lint  src/a.py · issues=3`; `details` reaches every
consumer through `ToolCallFinished` and `state`, and never enters the
conversation. A hook may enrich it in `on_tool_complete` by writing to
`ctx.tool_details`.

## Rules of the road

- **Plugins are trusted code.** Importing `mocode/plugin.py` executes it —
  same trust model as a pytest plugin.
- **Name your tools and commands distinctively.** A later registration with
  the same name replaces an earlier one.
- **Built-in names are reserved** (`filesystem`, `shell`, `skills`): a
  third-party plugin cannot shadow them. Overriding built-in behaviour means
  disabling the built-in and contributing your own tool under a different
  name.
- **One plugin, one name.** Project-local beats user-global; the loser is
  skipped rather than loaded twice.
- **Failures are contained.** Import errors and exceptions from `build()` are
  reported on stderr, the plugin is skipped, and the host starts normally. A
  broken terminal plugin costs its own contributions, never the screen.
- **Order is not guaranteed.** Built-ins load in a fixed order, third-party
  plugins sorted by name. Never depend on another plugin having run first.

## Disabling plugins

```jsonc
{
  "plugins": {
    "shell": { "enabled": false },
    "git-helper": { "enabled": true, "api_token": "..." }
  }
}
```

The config governs every plugin, built-ins included. Any keys other than
`enabled` are handed to the plugin untouched via `ctx.plugin_config("<name>")`.
