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
| reaches | `ctx` — home, cwd, config, model, tools, commands, hooks, `plugin_sources`, and (after assembly) the agent and its event stream | the terminal — `commands`, `display`, `input`, `conversation` |
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
from mocode.plugins import Command, CommandContext, CommandResult, Plugin, Section, Tool


def _git_status(args: dict, cwd) -> str:
    import subprocess
    return subprocess.run(["git", "status", "--short"], cwd=cwd,
                          capture_output=True, text=True).stdout


async def _branch(ctx: CommandContext) -> CommandResult:
    await ctx.conversation.notify("on the main branch")
    return CommandResult.text("")


class GitHelperPlugin(Plugin):
    name = "git-helper"
    description = "Git status tool and /branch command"

    def build(self, ctx):
        cwd = ctx.cwd

        def run(args: dict) -> str:
            return _git_status(args, cwd)

        ctx.tools.register(Tool(
            name="git_status",
            description="Show the working tree status of the conversation's repo.",
            schema={"type": "object", "properties": {}},
            func=run,
            tags=frozenset({"git"}),
        ))
        ctx.commands.register(
            Command("/branch", "Show the current branch", handler=_branch))
        ctx.prompt_sections.append(Section("git", "Prefer git_status over bash.", priority=45))
```

A `Tool` is a plain instance — metadata inline in the construction call, the
function closed over whatever per-conversation state it needs. There is no
subclass to write: a tool that needs its call context declares
`with_context=True` and its function becomes `(args, ctx)`. Restart MoCode. A
complete version of this — with a skill and a terminal command — lives in
[`examples/plugins/git-status`](../examples/plugins/git-status). The loader
resolves the plugin from the module: a module-level `plugin` instance wins,
then the first `Plugin` subclass the module defines itself.

## Single file vs package

Up to a few hundred lines, `mocode/plugin.py` (and `mocode.cli/plugin.py`)
is the right shape — one file, read top to bottom. Past that, split it: the
entry becomes a **package**.

```
mocode/
└── plugin/              the package — the directory is the entry
    ├── __init__.py      assembles the submodules; the loader imports this
    ├── sections.py      one contribution per submodule
    └── commands.py      submodules import each other relatively
```

The entry judgment is the same for both namespaces, in this order:

1. `<ns>/plugin.py` — the single file. When it is present it wins, and a
   `plugin/` directory beside it is ignored.
2. `<ns>/plugin/__init__.py` — the package. `None` of the two means the
   namespace ships no code, which is ordinary.

Inside the package, submodules load through **relative imports only** —
`from .commands import motd` — because each plugin's package is imported
under a name derived from the plugin's own: two plugins can both ship a
`helpers.py` and neither ever sees the other's. Never add the plugin's
directory to `sys.path` to make plain `import helpers` work: `sys.modules`
is process-wide, so same-named modules from two plugins would overwrite
each other — a cross-plugin pollution nothing would report. For the same
reason, expose the plugin instance in `__init__.py`: a module-level
`plugin = MyPlugin()` is what the loader looks for first, and only the
names `__init__.py` imported are visible on the package — a class left in a
submodule stays invisible unless you re-export or instantiate it there.

Getting the layout half-right is reported, not ignored: a `plugin/`
directory without `__init__.py`, or stray `.py` files beside no entry
at all, each produce a `[plugin]` line naming the fix instead of the plugin
quietly loading as skills-only.

None of this changes where *dependencies* come from: third-party packages
still belong in a `pyproject.toml` at the plugin root and a
[PluginVenv](#dependencies) of their own. The package organises your
files; it is not an environment. A complete worked example lives in
[`examples/plugins/multi-file`](../examples/plugins/multi-file).

## Installing plugins

Drop the directory into `.mocode/plugins/` (project) or `~/.mocode/plugins/`
(user) and it loads on the next start — or install it without leaving the
shell:

```bash
mocode plugin install <git-url | local-path>   # fetch, place by manifest name,
                                               # and set up its environment
mocode plugin install <path> --project         # into ./.mocode/plugins instead
mocode plugin list                             # what this project loads, and how
mocode plugin sync <name>                      # re-run the environment half alone
mocode plugin remove <name>                    # delete it, environment included
```

The manifest decides the directory name, so what `install` places is what
`list` shows and `sync`/`remove` address.

A git URL may name a **subdirectory** of a repository — how a collection of
plugins shares one repository:

```bash
mocode plugin install https://github.com/<you>/mocode-plugins/tree/main/kimi-search
mocode plugin install https://github.com/<you>/mocode-plugins.git#kimi-search
```

The first form checks out the ref the URL names; the `#` form takes the path
on the default branch. Both place the subdirectory's plugin under its
manifest name, like any other source.

Installing is an act of trust — a
plugin is code MoCode imports and runs; nothing executes during the install
itself, but the next start will. A plugin installed or synced here is loaded
by the *next* start; a running process does not retry loads.

## Dependencies

A plugin runs in MoCode's process, so the packages it imports come from
MoCode's Python environment by default — install them the way you installed
MoCode (`uv pip install <package>` into the same venv, or
`uv tool install mocode --with <package>`).

A plugin that wants an environment of its own ships a `pyproject.toml` at its
root — the standard declaration; the manifest schema stays closed — and the
user materialises it:

```toml
[project]
name = "git-helper"
version = "0.1.0"
dependencies = ["gitpython>=3.1"]

[tool.uv]
package = false        # the plugin is a directory, not an installable package
```

```bash
mocode plugin install <source>   # or: mocode plugin sync git-helper
```

That runs `uv sync` inside the plugin directory and creates a `.venv`
belonging to the plugin alone. When the plugin loads, MoCode *appends* that
environment's `site-packages` to `sys.path` — which means, said plainly:

* the plugin's packages resolve **only when MoCode's own environment does not
  already have them** — the host always wins;
* two plugins pinning different versions of one package do not both get their
  way: the host's version, then whichever imported first;
* this is **addition, not isolation**. A second interpreter is the only real
  isolation — that is what `mcp.json` is for when it is served.

A plugin whose import fails on a missing package is skipped with a report
saying exactly which of the two roads to take. A complete example — the tool,
the declaration, the README — lives in
[`examples/plugins/json-validate`](../examples/plugins/json-validate).

## The two stages: `build(ctx)` and `prepare(ctx)`

The lifecycle is split by **type**, not by runtime error. `build()` receives
a `BuildContext`; `prepare()` and `close()` receive a `HostContext` — the
same object once the agent exists.

| | `BuildContext` — `build()` | `HostContext` — `prepare()`, `close()`, call time |
|---|---|---|
| has | `home`, `cwd`, `config`, `model`, `plugin_sources`, `register_provider_type`, `tools`, `commands`, `hooks`, `prompt_sections`, `plugin_state()` | everything a `BuildContext` has, **plus** `agent` |
| can | register contributions, read config and model facts, create per-conversation state | everything on the left, *and* `await ctx.emit(...)`, `ctx.subscribe()`, `ctx.spawn(...)` |
| when | once per conversation, before assembly | after assembly — before the first request, at conversation end, during runs |

The agent does not exist during `build()` — every plugin contributes first,
then the loop is wired. A plugin that needs call-time abilities creates its
own objects in `build()` (a hook holding the context, a tool closing over
it, a sub-agent spawned per call) and reaches the loop through the context
it kept, which has grown into a `HostContext` by then. A tool's shorter path
is `with_context=True`: its `ToolCallContext` carries `emit` and the
resolved policy for that call.

| Contribution | API |
|---|---|
| Tools | `ctx.tools.register(Tool(...))` — relative paths should resolve against `ctx.cwd` |
| Commands | `ctx.commands.register(Command(...))` — a *shared* command, dispatchable from any frontend |
| Hooks | `ctx.hooks.append(hook)` |
| Prompt sections | `ctx.prompt_sections.append(Section(...))` — a name collision is won by the last section registered |
| Own settings | `ctx.plugin_config("name")` — the `plugins.<name>` object from config.json |
| Own state | `ctx.plugin_state("name")` — a dict that travels with the session |
| Model facts | `ctx.model` — name / `context_window` / `max_output`, readable in `build()` |
| Files it ships | `ctx.plugin_sources` — the directories the project's plugins were loaded from |
| Provider types | `ctx.register_provider_type(name, factory)` — see [providers.md](providers.md) |
| Messages to the user | `await ctx.emit(Notice(...))` at call time — a `Notice` carries its own text and level |
| Structured messages | `await ctx.emit_message(kind, data, block_id=...)` — a `PluginMessage` block; see below |
| Watching the conversation | `ctx.subscribe()` at call time — the event stream, out-of-band |
| Sub-agents | `ctx.spawn(system_prompt=..., ...)` at call time — see below |

`build()` must stay **cheap and synchronous**: registrations only. Anything
that needs I/O (discovery, connections, subprocesses) belongs in the second
pass:

```python
class MyPlugin(Plugin):
    def build(self, ctx):
        ctx.tools.register(stub_tool())              # cheap, synchronous

    async def prepare(self, ctx):
        catalog = await discover_endpoints()         # I/O lives here
        ctx.tools.register(catalog_tool(catalog))    # may still contribute
```

The two passes sit on either side of the **request surface** — the system
prompt and the offered tool interface, the two things every request carries.
History is data and comes back eagerly when a conversation is opened; the
surface is *derived state*, and the host materializes it exactly once:

- **a fresh conversation**, at the top of its first turn, *after* every
  plugin's `prepare()` has run and *before* the loop sends the first request —
  so tools and sections contributed in `prepare()` are in the very first
  request, with nothing announced;
- **a resumed conversation**, from the session file, byte-identical — the
  old turns' prefix cache survives. (`prepare()` still runs; anything that
  moved since arrives as a cache-protect notice, like any other drift.)

`PluginHost` owns the whole thing — `materialize()` is the one place the
surface is written — and `await conversation.prepare()` is the explicit entry
for an application or a test that wants the surface before any turn. The
practical consequences:

- opening a conversation never blocks on I/O, so an embedder inside a running
  event loop pays nothing;
- a plugin's `prepare()` should bound itself (`asyncio.wait_for`) — the first
  turn waits for it, and a hang there hangs the turn;
- `rebuild_prompt()` remains the deliberate re-materialization: it re-renders,
  re-pins and clears every plugin's session state, running no preparation.

### Remembering, across a resume

`ctx.plugin_state("name")` returns this plugin's own dict for this
conversation — created empty, and it *survives*: the host persists it with the
session and hands it back when the session is resumed (or swaps it when the
conversation loads a different one), and clears it on `rebuild_prompt()`, when
the model has just been re-told everything. Slots are keyed by plugin name, so
plugins never see each other's, and nothing in the host knows what any plugin
keeps in its own. It is the missing piece for anything stateful — a baseline,
a counter, an index — and `cache-protect` is its first user: the baselines its
drift notices are diffed against live there.

### One plugin instance, many builds

A plugin object is created once, and `build()` runs once per conversation.
**Keep the plugin itself stateless**: anything belonging to a conversation — a
session handle, a cache, a counter — is created *inside* `build()`, not stored
on `self`. All three shipped plugins work this way; `ShellPlugin.build` makes
a fresh session and, around it, the fresh `bash` / `bash_output` /
`kill_shell` tools — working directory, environment variables and background
jobs included. That invariant is what lets one process serve several
conversations from a single loaded plugin list. `close(ctx)` is where
anything you acquired for a conversation is released — the shell plugin kills
its background jobs there, reaching the session through the registry (the
tool owns the session, the registry owns the tool), never through state on
the plugin instance.

One more thing the session answers for, and the reason a *frontend* cares:
`session.promote()` moves a foreground command that is still running into the
background, mid-flight. The waiting `bash` call settles at once as "moved to
background as shell_N", and the command lives on as an ordinary job —
`bash_output` reads what it prints from the promotion on (everything before
already went to the live block), `kill_shell` stops it, and its completion is
announced like any job's. Called with no argument it moves *the* one
foreground call running — the shape a runtime keybinding wants; with zero
running it raises `no_running_call`, with several `ambiguous_call` (name the
call id of the one to move). The background concurrency cap applies, and a
promotion races the foreground timeout honestly: first to claim the call
wins, the loser stands down without killing anything.

## Tools

A tool is declared with a JSON Schema object node — the dialect every model
speaks natively, so what the model is offered and what the arguments are
checked against are the same document:

```python
from mocode.plugins import Tool, ToolPolicy, ToolResult

Tool(
    name="list_issues",
    description="List repository issues",
    schema={
        "type": "object",
        "properties": {
            "state": {"type": "string", "enum": ["open", "closed"]},
            "limit": {"type": "integer", "default": 50, "description": "Max issues"},
        },
        "required": [],
    },
    func=run,
    tags=frozenset({"issues"}),
    summary_key="state",         # the argument a one-line summary shows
    result_key="issue_count",    # the detail shown alongside it
    returns={"type": "object"},  # structured-output metadata for SDKs; never sent
    policy=ToolPolicy(timeout=60),  # per-tool overrides before config
)
```

The built-in checker validates the common keywords (`type`, `required`,
`properties`, `items`, `enum`, `anyOf`/`oneOf`, `default`) and fills defaults;
unknown keywords pass — forward compatibility beats false rejections. Nested
objects and arrays are first-class.

Beyond the schema, a `Tool` carries:

- **`with_context`** — declare it and the function is called as
  `(args, ctx)`, receiving its `ToolCallContext`: that is how a long-running
  tool reports progress (`await ctx.emit(ToolOutput(...))`) and reads the
  policy resolved for the call (`ctx.tool_timeout`). The declaration is
  checked at construction — a signature that cannot receive the context
  fails at import/build time, not mid-turn. Tools that don't ask for it keep
  the plain `(args) -> str` shape.
- **`availability`** — who may use it: `"model"` (offered to the model only),
  `"program"` (callable by code only, invisible to the model — the shape a
  folded deployment uses), or `"both"`, the default. Invisible to an audience
  means neither offered nor runnable.
- **`policy`** — a `ToolPolicy` (timeout, result limit), or a callable
  receiving the call's arguments and returning one; `None` fields fall
  through to the config. Resolution order: call-level over tool-level over
  config.
- **`source`** — who registered it. You do not set it: the host stamps every
  registration with the plugin's channel-prefixed name (`plugin:<manifest
  name>`; built-ins get `builtin:<name>`), so attribution is a fact of the
  path and a tool cannot claim an identity its loader cannot back. What the
  stamp buys everyone: a same-name registration from a *different* source
  raises `ToolConflictError` at registration time instead of silently erasing
  someone's work — name your tools distinctively, and collisions become loud.

A tool may return a `ToolResult` when there are facts *about* the result worth
showing — `content` is what the model reads, `details` reaches frontends
through `ToolCallFinished` and never enters the conversation:

```python
def lint(args):
    issues = run_linter(args["path"])
    return ToolResult(
        content=render(issues),          # the model reads this
        details={"issues": len(issues)}, # you read this
    )
```

### Who a tool is for — and running one yourself

When your code — not the model — needs to run a tool, do not copy the loop's
plumbing and do not call `tool.run` directly: both drift. Call the dispatcher
every call already goes through:

```python
async def _run(self, args, ctx):        # one of your tools, mid-call
    result = await self._ctx.agent.dispatcher.run(
        "read", {"path": args["path"]},
        origin="program",               # this call is yours, not the model's
        parent_call_id=ctx.tool_call_id,
    )
    return result.content               # status / details / error_code alongside
```

Hooks intercept it, availability is enforced, timeouts and cooperative
cancellation apply, the result is truncated and reported exactly as the
model's calls are — and the nested call's events are observable on the channel
while staying out of the conversation (`ToolDispatcher`'s program-origin
contract; see [ARCHITECTURE.md](ARCHITECTURE.md)).

## Prompt sections

`Section("name", content, priority=45)` contributes to the system prompt;
sections render in `(priority, insertion order)` order as XML tags. Content
may be static text, a list of child sections, or a callable receiving the
builder's context — and a section that renders itself from live state wants
the two fields made for it:

```python
Section(
    "tools-sdk",
    render=lambda _ctx: render_sdk(),  # drawn from the live registry
    pinned=True,                       # rendered once, then byte-identical
    derived_from="tools",              # lineage: cache-protect diffs it
    priority=45,
)
```

- **`pinned=True`** freezes the section's rendered bytes after the first
  render. A section that re-renders from a live registry on every build would
  churn the frozen prompt around it; the pin holds until the next
  `rebuild_prompt()`.
- **`derived_from="tools"`** declares that the text is derived from the tool
  registry — which is exactly what makes its drift visible: the `cache-protect`
  plugin diffs the *live* render of pinned `derived_from="tools"` sections and
  announces the change in its `[context update]` notice, because the pinned
  prompt itself cannot carry it.

## Hooks

A hook is the **interception** channel: it runs at a fixed point and may
change what happens. Everything a hook *watches* comes from the event stream
instead — an event is a notification, a hook is a request that expects an
answer.

| Method | When | You may |
|---|---|---|
| `before_iteration(ctx)` | before each LLM call | rewrite `ctx.messages` or `ctx.system_prompt`, `await ctx.emit(...)` |
| `before_request(ctx)` | before the request is sent | rewrite `ctx.messages` / `ctx.system_prompt` / `ctx.tools` (this one request), `await ctx.emit(...)` |
| `after_response(ctx)` | after the response is accounted | rewrite `ctx.usage` (token accounting), read `ctx.finish_reason` |
| `on_tool_start(ctx)` | before a tool runs | rewrite `ctx.tool_args`, set `ctx.deny` to veto |
| `on_tool_complete(ctx)` | after a tool runs | rewrite `ctx.tool_result`, enrich `ctx.tool_details`, read `ctx.status` |
| `on_event(event)` | every event the run publishes | observe, accumulate, ignore |

`on_event` runs *inline*: the loop waits for it, so it sees every event before
the run moves on — and so it can slow the run down. Keep it for code that must
answer; a plugin that only watches calls `ctx.subscribe()` instead, which
nobody waits for — see [embedding.md](embedding.md).

A `system_prompt` a hook writes lasts **for the rest of the run**: the loop
restores the prompt as it stood when the turn ends, so one turn's rewrite
never leaks into the next. A persona that should hold for the whole
conversation is a prompt section, not a hook. One hook raising never breaks
the loop or the other hooks — the failure is logged and the run continues.

## Sub-agents

`HostContext.spawn()` is `derive()` with the plugin-facing defaults fixed:
events **visible** on the conversation's channel, hooks **not** inherited, a
live copy of the tool set — and the parent's provider unless you pass a
`model`. It is call-time API (during `build()` there is no agent yet): a tool
that delegates closes over the context and spawns per call.

```python
class SubAgentPlugin(Plugin):
    name = "delegate"

    def build(self, ctx):
        host_ctx = ctx  # grows into a HostContext at assembly — same object

        async def delegate(args: dict, call_ctx) -> str:
            child = host_ctx.spawn(
                system_prompt="You are a focused sub-agent.",
                tools=host_ctx.tools.select(exclude_tags={"delegation"}),
            )
            result = await child.run_with_messages(
                [{"role": "user", "content": args["task"]}]
            )
            if result.had_error:              # run_with_messages never raises
                return f"error: {result.content}"
            return result.content

        host_ctx.tools.register(Tool(
            name="delegate",
            description="Delegate a task to a sub-agent",
            schema={"type": "object",
                    "properties": {"task": {"type": "string"}},
                    "required": ["task"]},
            func=delegate,
            with_context=True,
            tags=frozenset({"delegation"}),
        ))
```

The child's events on the conversation's channel reach channel subscribers
(the terminal's renderer, a logger), while each agent's `Turn` views and
`state` stay scoped to its own run — watch a child through the child.
`visible=False` gives the child a private stream instead. Nothing in `core/`
knows what a sub-agent is.

## Plugin messages — `emit_message` / `seal_message`

A `Notice` is a line of text. When what a plugin wants to say has *structure*
— progress with numbers, a completion with facts — publish it as a
`PluginMessage` instead:

```python
await ctx.emit_message("rag/index", {"done": 12, "total": 40}, block_id="rag-1")
await ctx.emit_message("rag/index", {"done": 40, "total": 40}, block_id="rag-1")
await ctx.seal_message("rag-1")
```

`kind` is the discriminator and carries the namespace: a third-party plugin
writes `"<plugin>/<type>"` (`"shell/background-done"` is the shell plugin's),
and kinds without a `/` are reserved for built-ins. `data` is the JSON-ready
payload. `block_id` addresses a **block**: messages that share one update the
same display block instead of opening a new one — the shape for progress that
keeps moving. A message without a `block_id` is a block of its own.

`seal_message(block_id)` closes a block. What a frontend does with the
addressing is its own policy, stated here so plugin authors know what to
expect: the terminal keeps a block *open* — updatable in place — only while
it is on screen; once committed to the scrollback it is immutable, and an
update arriving after the seal is appended as a follow-up block (a `✓ shell_3
completed, exit 0` line after the block that started it) rather than a
rewrite. `emit_message` is legal in `prepare()`, in `close()` and at call
time; during a turn the message is attributed to it, so the turn's readers
see it, and between turns it belongs to the conversation stream alone.

**Persistence**: plugin messages **are** persisted with the session — the
newest 200 of them, serialized with everything else. Resuming the
conversation replays them on the stream after the history, in order, so a
frontend that draws blocks rebuilds what was on screen; nothing replays
twice, and what a frontend already committed to the scrollback it treats
as history. The bound is a session's worth of display chatter, not an
archive: facts that must survive unbounded belong in `plugin_state()` or
in the conversation history.

## Context compaction — a worked example

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
        await self._ctx.emit(Compacted(old_count=old, new_count=len(ctx.messages)))


class CompactPlugin(Plugin):
    name = "compact"

    def build(self, ctx):
        ctx.hooks.append(CompactHook(ctx))
```

Again, the kernel has no idea compaction exists. Note what `build` does *not*
do: register a renderer. The event describes itself — that is what `summary()`
is for — so a terminal, a web UI and a log all display it without the plugin
knowing any of them exist.

## Changing the harness after assembly

`build()` is the one *contribution* pass, but it is not the only moment the
harness can change: the registries are live for the conversation's whole life,
and the loop reads them back on every iteration. A host application — or a
harness that reshapes itself between task batches — never restarts anything:

- **`ctx.tools`** — `register` / `unregister` / `enable` / `disable` at any
  moment. What the model is *offered* is the registry's projection, and a
  host may pin it (`ToolRegistry.freeze()`; `MoCode(freeze_interface=False)`
  opts out) so a request's tool payload stays byte-identical for the session
  and the provider's prefix cache survives. Either way the switch is live: a
  tool switched off disappears from `names()`, refuses to run (`denied:`), and
  the `cache-protect` plugin announces the change at the next turn as a
  `[context update]` notice — a state line for a switch, a unified diff for a
  rewritten schema, and the schema itself for a tool registered after the
  freeze (callable through the registry, and it enters the pinned payload at
  the next rebuild or session). A plugin that wants a *switchable* tool
  registers it disabled in `build()` and flips it later: that is a pure flag
  and costs no cache at all.
- **Prompt sections** — `ctx.prompt_sections` feeds the prompt when it is
  rendered. Changes apply at the next render: a new conversation, or
  `conversation.rebuild_prompt()` — which re-freezes the prompt, re-pins the
  tool interface, drops every pinned section's render cache and clears every
  plugin's session state, accepting the cache loss in one deliberate act. A
  resumed session keeps the prompt it ran with, byte-identical, so the
  provider's prefix cache survives; what changed since the model was last
  told arrives as a `[context update]` notice — one unified diff per moved
  part — and a change reverted before the turn that would announce it is not
  announced at all. Inside a running turn, a hook writing `ctx.system_prompt`
  in `before_iteration` is how the prompt changes — for that run.
- **Hooks** — `agent.hooks.add(hook)` takes effect at the next interception
  point. Hooks run in the order they were added — plugin load order: built-ins
  first, then each plugin directory in priority order, alphabetical inside
  one, and within a plugin the order `build()` appended them — and they share
  one context object, so a change an earlier hook made is what a later one
  sees.

The split of labour is the contract: *inside* a running turn the only writes
are the hook points (`before_iteration`, `before_request`, `on_tool_start`,
`on_tool_complete`); *between* turns, anything the public API allows. An
application that evolves its own harness — swapping tool sets, tuning
sections, adding hooks — works entirely on the second side of that line.

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

    def build(self, ctx):
        ctx.commands.register(Command("/status", "Show git status", handler=_status))
        # also: ctx.keys, ctx.input, ctx.status, ctx.header, ctx.drawers
```

A terminal plugin builds against a `CLIContext` — never the application. The
context carries what a plugin may contribute through (commands, drawers,
keys, input middleware, status and header) plus read-only views (the theme,
the conversation, the runtime UI channel), and nothing it should not touch:
there is deliberately no `ctx.app`, `ctx.display` or `ctx.renderer`. A plugin
that needs a lower ability gets it by that ability being promoted into the
context, not by a hole.

The two namespaces never import each other; when they need to cooperate, they
go through the conversation, which is the only thing they share. The
terminal's own commands are the first implementation of this interface
(`cli/plugin.py::BuiltinCommands`), so there is one way to contribute here.

### Keys, middleware and the status bar

The context's input surface splits by **when** it fires:

- `ctx.keys.add(key, handler, when="idle", description="")` — while the prompt
  is editing. The handler receives a `KeyContext` (the conversation, the UI
  channel, and the buffer being edited) and runs synchronously; returning
  `"clear"` empties the buffer afterwards. Idle bindings join the prompt's
  keybindings when the PromptSession is first built — registrations after
  that take effect when the session is next rebuilt.
- `ctx.keys.add(key, handler, when="running", description="")` — while the
  agent is mid-turn, read by a raw key loop the terminal runs over the TTY.
  The handler may be async; its `KeyContext` carries the running turn instead
  of a buffer.

Two running-time keys are built in, as the API's first real users:

| Key      | Effect                                                           |
| -------- | ---------------------------------------------------------------- |
| `Ctrl+B` | move the one running foreground command to the background        |
| `Ctrl+O` | toggle verbose tool output (landed calls keep their output tail) |

While a turn runs, `Esc` and `Ctrl+C` cancel it (the SIGINT fallback stays,
and the two are idempotent together). At an idle prompt, `Ctrl+C` clears the
line first and only exits on a second press — Claude Code semantics.

`ctx.input.use(fn)` appends to the middleware chain every submitted line
folds through before dispatch — `text -> text`; returning `None` consumes the
line, so neither a command nor the model ever sees it and the prompt simply
comes back. Registration order is run order, and a `build()`-time
registration is in effect from the first prompt.

`ctx.status.use(fn)` contributes one segment to the bottom bar. Each redraw
runs every provider against the current `StatusState` (model, cwd, running,
usage, pending approvals); a provider returns a `Segment(text, priority)` or
`None`, the merge sorts by priority (highest leftmost), joins with ` · ` and
truncates at the terminal width. The terminal itself contributes the model,
the token counts (`↑in ↓out`, once a usage has landed) and the cwd (home
contracted to `~`) — a plugin's segment sits beside them, not over them.

`ctx.header.set(lines)` prints lines above the prompt, once, into the scroll
buffer — there is no live header region. `ctx.theme` is a read-only view of
the terminal's theme; registering new style names is deliberately not v1.

`ctx.on_close(fn)` registers cleanup for when the terminal goes away —
callbacks run in reverse registration order, each in isolation, so one
failing callback never costs another plugin its release.

All of this is terminal chrome: in a pipe (`ui.is_interactive` is `False`)
status and header contributions are ignored and the key channels never start.

## Rules of the road

- **Plugins are trusted code.** Importing `mocode/plugin.py` executes it —
  same trust model as a pytest plugin.
- **Name your tools and commands distinctively.** A same-name registration
  from a different source raises `ToolConflictError` rather than quietly
  replacing the earlier one: the host stamps every tool with the plugin that
  registered it, and only a same-source reregistration — a plugin updating
  its own tool — is an overwrite. `register(tool, replace=True)` forces a
  takeover knowingly.
- **Built-in names are reserved** (`filesystem`, `shell`, `skills`,
  `default-prompts`, `session`, `help`, `cache-protect`): a third-party plugin
  cannot shadow them. Overriding built-in behaviour means disabling the
  built-in and contributing your own under a different name.
- **One plugin, one name.** Project-local beats user-global; the loser is
  skipped rather than loaded twice.
- **Failures are contained.** Import errors and exceptions from `build()` are
  reported on stderr, the plugin is skipped, and the host starts normally. A
  broken terminal plugin costs its own contributions, never the screen.
- **Order is deterministic, but it is not a dependency graph.** Built-ins load
  first, then third-party plugins: project-local before user-global, sorted by
  name inside each directory. Hooks and contributions follow that same order —
  never depend on another plugin having run first.

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
The `shell` plugin, for instance, reads two:

```jsonc
{
  "plugins": {
    "shell": {
      "max_background": 16,        // background jobs running at once
      "background_timeout": 3600   // hard ceiling per job, seconds; 0 = none
    }
  }
}
```

A background job's own `timeout` argument may lower that ceiling, never
raise it. Background output is bounded regardless (2000 lines / 256KB per
stream, oldest dropped and counted), and every job dies with the
conversation — `restart`, `close`, no exceptions.

To test a plugin against a scripted model — no network, no API key — see [testing.md](testing.md).
