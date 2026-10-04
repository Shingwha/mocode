# The public API

> This file is the **map**. The detailed source of truth for every name below
> is its docstring in `mocode/`, which is what this document was generated
> from by hand; for the reasoning behind a shape, read
> [ARCHITECTURE.md](ARCHITECTURE.md), and for how to *use* a surface,
> [embedding.md](embedding.md) (embedders), [plugins.md](plugins.md) (plugin
> authors), [providers.md](providers.md) (provider authors) or
> [testing.md](testing.md) (tests).
>
> Signatures here are one-line sketches: required arguments first, keyword
> arguments named where they matter, and the purpose after the dash.

```
mocode            MoCode
mocode.core       the kernel (core/)
mocode.host       the embeddable layer (host/)
mocode.cli        the terminal front-end (cli/)
mocode.plugins    the plugin SDK
mocode.testing    the public test kit
mocode.providers  provider implementations
```

## `mocode.core` — the kernel

Import from here to build an agent with no config file and no application
around it.

### The loop

| Name | Signature | Purpose |
|---|---|---|
| `AgentLoop` | `AgentLoop(provider, system_prompt, tools, hooks, config=None, model=None, channel=None, prepare=None)` | the engine: one conversation, one turn at a time |
| `.start` | `.start(prompt=None) -> Turn` | the one way to execute a turn; raises if one is running |
| `.stream` | `.stream(prompt=None) -> AsyncIterator[Event]` | a view over a turn, scoped to the caller |
| `.chat` | `.chat(prompt=None) -> str` | a view over a turn, returning the final answer |
| `.run_with_messages` | `.run_with_messages(messages) -> LoopResult` | convenience wrapper: adopt a message list and run; never raises, reports `.had_error` |
| `.derive` | `.derive(*, system_prompt, tools, hooks, config, model, provider, channel)` | a nested agent inheriting *copies* of the parent's capability set |
| `.close` | `.close()` | detach the loop from its channel's inline subscribers |
| `AgentConfig` | `AgentConfig(tool_result_limit=50000, tool_timeout=240, max_iterations=0, max_tool_calls=0, max_turn_seconds=0)` | loop execution policy; budgets are per turn, 0 = unlimited |
| `IterationLimit` | raised by `chat()` when `max_iterations` cuts the turn | distinguishes "no answer" from an empty one |
| `LoopResult` | `content`, `tool_calls_made`, `messages`, `had_error` | what `run_with_messages` reports |
| `Turn` | `.id`, `.state`, `.failure`, `.cancelled`, `.done`, `.subscribe(since=None)`, `.wait() -> RunFinished \| RunFailed`, `.cancel()` | one turn's addressable, watchable, cancellable window on the channel |

### Events

| Name | Signature | Purpose |
|---|---|---|
| `Event` | `run_id`, `seq`, `type`, `to_dict()`, `summary()` | base class; subclass it with a unique `type` and a `summary()` so any frontend can show it |
| `RunStarted` | `model`, `tools` | a turn began |
| `RunFinished` | `content`, `usage`, `iterations`, `tool_calls_made`, `stop_reason` | a turn ended without an exception |
| `RunFailed` | `error`, `kind` | a turn ended on an unhandled error |
| `IterationStarted` / `IterationFinished` | `iteration` / `iteration`, `usage`, `stop_reason` | one LLM call, before and after |
| `TextDelta` / `ReasoningDelta` | `text` | a fragment of the answer / of the reasoning trace |
| `ToolCallArgsDelta` / `ToolCallStarted` / `ToolOutput` / `ToolCallFinished` | `call_id`, `name`, `arguments` / `call_id`, `name`, `args`, `origin`, `parent_call_id` / `call_id`, `text`, `stream` / `call_id`, `name`, `status`, `result`, `error_code`, `duration`, `details`, `origin`, `parent_call_id` | a tool call's four moments, one identity |
| `Notice` | `message`, `level` | a line a plugin or the host wants to say |
| `PluginMessage` | `kind`, `data`, `block_id`, `sealed` | a structured plugin entry; `block_id` addresses a display block (`mocode.core.events`, and `mocode.plugins`) |
| `StopReason` / `ToolStatus` | `"completed" \| "max_iterations" \| "max_tool_calls" \| "time_budget" \| "cancelled"` / `"ok" \| "error" \| "timeout" \| "denied" \| "not_found"` | the two closed vocabularies |
| `TOOL_OK` … `TOOL_NOT_FOUND` | the five `status` constants, as names | no string parsing on either side |

### The channel

| Name | Signature | Purpose |
|---|---|---|
| `EventChannel` | `EventChannel(*, replay=1000, backlog=1000)` | one ordered stream per conversation |
| `.subscribe` | `.subscribe(since=0) -> Subscription` | a reader; out-of-band, never holds the model up |
| `.publish` | `await .publish(event) -> int` | publish and get the `seq` back |
| `.inline` | `.inline(callback) -> unsubscribe` | an in-band reader — the channel awaits it; this is how hooks watch |
| `.closed` / `.seq` | properties | whether the channel is shut down, and the newest `seq` |
| `Subscription` | `dropped`, `lagging`, `await .get()`, `.take()`, `.close()`, async iteration | one reader's bounded, honest view |

### Tools

| Name | Signature | Purpose |
|---|---|---|
| `Tool` | `Tool(name, description, schema, func, *, tags=frozenset(), summary_key="", result_key="", with_context=False, availability="both", policy=None, source="")` | a callable tool; `schema` is a JSON Schema object node |
| `ToolResult` | `content`, `details` | what a tool returns when a bare string is not enough — `content` for the model, `details` for you |
| `ToolError` | `ToolError(message, code="execution_error")` | a tool's failure, carried through the ordinary result pipeline |
| `ToolConflictError` | raised by a same-name registration from a different source | a loud collision instead of a silent overwrite |
| `ToolPolicy` | `timeout: int \| None = None`, `result_limit: int \| None = None` | per-tool overrides before config |
| `ToolRegistry` | `.register(tool, replace=False)`, `.unregister(name)`, `.get(name)`, `.all()`, `.names(audience="model")`, `.enable(name)`, `.disable(name)`, `.all_schemas(audience="model")`, `.select(audience="model", include_tags=None, exclude_tags=None, include_names=None, exclude_names=None)`, `.pinned`, `.freeze(schemas=None)` | the source of truth for what is callable and offered |
| `ToolDispatcher` | `.run(name, args, *, origin="model", timeout=None, parent_call_id=None)` | the one execution path for a tool call, model-origin and program-origin alike |
| `DispatchResult` | `status`, `content`, `details`, `error_code`, `duration`, `call_id` | what the dispatcher reports back |
| `split_result` | `split_result(result) -> (content, details)` | normalize a tool's return value |

### Prompt

| Name | Signature | Purpose |
|---|---|---|
| `Prompt` | `.register(section)`, `.unregister(name)`, `.get(name)`, `.all()`, `.names()`, `.enable(name)`, `.disable(name)`, `.render(section)`, `.build(wrap="system-prompt")` | section-based assembly, in `(priority, insertion order)` |
| `Section` | `Section(name, content=None, *, priority=0, enabled=True, attrs={}, render=None, pinned=False, derived_from=None)` | one piece of a prompt; `pinned` freezes its bytes, `derived_from="tools"` declares lineage |

### Hooks

| Name | Purpose |
|---|---|
| `AgentHook` | override `before_iteration(ctx)`, `before_request(ctx)`, `after_response(ctx)`, `on_tool_start(ctx)`, `on_tool_complete(ctx)`, `on_event(event)` |
| `HookRunner` | the fan-out: fans out with error isolation, `.add(hook)` takes effect at the next interception point |
| `IterationContext` | `messages`, `iteration`, `system_prompt`, `emit` — `messages` and `system_prompt` are writable |
| `RequestContext` | `messages`, `system_prompt`, `tools`, `model`, `emit` — the last look at the request about to go out |
| `ResponseContext` | `usage`, `finish_reason`, `iteration` — `usage` is writable |
| `ToolCallContext` | `tool_name`, `tool_args`, `tool_call_id`, `origin`, `parent_call_id`, `deny`, `status`, `error_code`, `tool_result`, `tool_details`, `tool_timeout`, `tool_result_limit`, `emit`, `cancel_event`, `.cancelled` |

### Providers

| Name | Purpose |
|---|---|
| `Provider` | the protocol: `model`, `retry_policy`, `is_retriable(exc)`, `stream(messages, system, tools, max_tokens, effort)` — structural, not inherited |
| `ModelSpec` | `name`, `context_window`, `max_tokens`, `efforts`, `effort` — model *facts*, distinct from `AgentConfig`'s loop policy |
| `Chunk` / `ToolCallDelta` / `ToolCall` / `Usage` | the streaming DTOs a provider yields |
| `Response` / `StreamAccumulator` | what a chunk stream adds up to; the accumulator's job, never a provider's |
| `RetryPolicy` | `max_attempts`, `base_delay`, `max_delay`, `jitter`, `honor_retry_after` — a provider's own retry numbers |
| `with_retry_stream` | `with_retry_stream(provider, *args, policy=None, deadline=None)` — retry orchestration around the first chunk |
| `RetryDeadlineExceeded` | raised when the caller's budget ends the retries — a budget endgame, not a provider failure |
| `Effort` / `EFFORTS` | the reasoning-effort level and the default triple `low / medium / high` |

### Transcript

The message-dict format, in one place (added in W2). Everything here works on
the OpenAI-format dicts in `agent.messages`, so the same code runs on the
caller's side or inside a hook.

| Name | Purpose |
|---|---|
| `assistant_message(content, *, tool_calls=None, reasoning=None)` | build an assistant message dict |
| `tool_call_dicts(tool_calls)` | `ToolCall` objects as message-dicts |
| `tool_result(call_id, content)` | the `role="tool"` message that answers a call |
| `is_user` / `is_assistant` / `is_tool_result` | which kind of message is this |
| `text_of(msg, sep=" ")` / `reasoning_of(msg)` | the text, or the reasoning trace, of one message |
| `content_parts(content)` | the text parts of a possibly-multipart content |
| `tool_calls_of(msg)` / `tool_call_id` / `tool_call_name` / `tool_call_arguments` / `tool_call_args` | read a message's calls without hand-walking the schema |
| `tool_call_by_id(messages, call_id)` | the assistant message holding one call |
| `answered_call_id(msg)` | the call a tool message answers |
| `IMAGE_PLACEHOLDER` | `"[image]"` — what an image part reads as in text |

### Live state

| Name | Purpose |
|---|---|
| `RunState` | the events folded into a snapshot: `status`, `model`, `iteration`, `content`, `answer`, `reasoning`, `tool_calls`, `usage`, `last_usage`, `error`, `apply(event)`, `to_dict()` |
| `ToolCallState` | `call_id`, `name`, `args`, `status`, `result`, `details`, `error_code`, `duration`, `output`, `.output_text`, `.done` — `status` is `"forming"` while the model streams the arguments, `"running"` while it executes, then a terminal `TOOL_*` value |

## `mocode.host` — the embeddable layer

### Runtime and conversations

| Name | Signature | Purpose |
|---|---|---|
| `MoCode` | `MoCode(*, config=None, home=None, plugin_dirs=None, freeze_interface=True)` | the process runtime: config, plugin loading per project, the session store |
| `.new_conversation` | `.new_conversation(*, cwd=None, provider=None, model=None, commands=None, session=None)` | open a conversation |
| `.resume` | `.resume(session_id) -> Conversation \| None` | re-open a stored one wherever its project was |
| `.plugins_for` / `.plugin_sources_for` | `(cwd)` | what a project loads, cached per project |
| `.provider_for` | `(key, model) -> Provider` | build a provider for a pair |
| `.register_provider_type` | `(type_name, factory)` | the provider-type registry, before the first `new_conversation` |
| `.set_default_model` | `(key, model)` | **the only thing that writes config.json** |
| `Conversation` | see below | one project, one model, one history, one stream |

| `Conversation` member | Purpose |
|---|---|
| `.run(prompt)` / `.stream(prompt)` / `await .chat(prompt)` | the three ways to run a turn, none of them a new path through the loop |
| `.prepare()` | materialize the request surface now |
| `.subscribe(since=seq)` | read the stream out-of-band |
| `.notify(text, level="info")` | publish a `Notice` from your application |
| `.state` / `.messages` / `.tools` / `.commands` / `.model` / `.busy` / `.id` | what the conversation is, right now |
| `.set_model(key, model)` / `.set_effort(level)` | this conversation only — no file is written |
| `.save(title=None)` / `.session()` / `.list_sessions()` / `await .new_session(messages=None)` / `await .load_session(session)` | the session lifecycle |
| `.rebuild_prompt()` | re-render, re-pin and forget the plugin state — the one deliberate cache loss |
| `.await .aclose()` / `.close()` | the full close, async or the sync emergency path |
| `.host` (a `PluginHost`) | `build_all` / `prepare_all` / `materialize` / `rebuild` / `close` run here; `.failures` lists the plugins that did not load |

### Config

| Name | Purpose |
|---|---|
| `Config` | `.load()`, `.provider`, `.model`, `.agent` (an `AgentConfig`), `.providers`, `.plugins`, `.foreign`, `.model_spec(key, model)` |
| `ProviderEntry` | `name`, `api_key`, `base_url`, `models`; `.api_key_for(key)`, `.label(key)`, `.model_ids()`, `.model(model)` |
| `ModelEntry` | `id`, `name`, `context_window`, `max_tokens`, `efforts`, `effort`; `.retry_policy()` |
| `env_var_for(provider_key)` | the environment variable a provider key resolves to |

The table of which value is owned by which entry — and what an absent one
means — is [README.md](../README.md#configuration).

### Sessions

| Name | Purpose |
|---|---|
| `Session` | pure data: `id`, `created_at`, `updated_at`, `workdir`, `messages`, `title`, `model`, `provider`, `system_prompt`, `tool_schemas`, `plugin_state`, `plugin_messages`, `metadata` |
| `SessionStore` | `.list(workdir)`, `.list_all()`, `.find(id)`, `.save(workdir, session)`, `.delete(workdir, session_id)` |
| `load_session_file(path)` | read an exported JSON session back in |

### Export

| Name | Purpose |
|---|---|
| `export_session(path, session, system_prompt="")` | write a session as resumable JSON |
| `export_session_md(session, path, system_prompt="")` | write a session as readable Markdown |
| `render_session_md(session, system_prompt="")` | the Markdown as a string |

### Plugin framework

Everything in this table lives in `mocode.host.plugin` (the names an embedder
uses most also sit on `mocode.host`); a plugin author imports `Plugin`,
`BuildContext` and `HostContext` from the SDK instead.

| Name | Purpose |
|---|---|
| `Plugin` | `name`, `description`, `build(ctx)`, `async prepare(ctx)`, `close(ctx)` — an instance is stateless, `build()` runs once per conversation |
| `BuildContext` | `home`, `cwd`, `config`, `model`, `plugin_sources`, `register_provider_type`, `tools`, `commands`, `hooks`, `prompt_sections`, `plugin_config(name)`, `plugin_state(name)` |
| `HostContext` | the same object with `agent` attached: `emit(event)`, `emit_message(kind, data, block_id="")`, `seal_message(block_id)`, `subscribe(since=None)`, `spawn(*system_prompt, tools, model, visible=True)` |
| `PluginHost` | `build_all` / `prepare_all` / `materialize` / `rebuild` / `assemble(provider, config)` / `close`; `.failures` |
| `load_plugins` | `load_plugins(*, plugin_dirs, config, reserved=()) -> LoadedPlugins` — the per-project half, cached by `MoCode` |
| `LoadedPlugins` | `plugins`, `sources`, `tool_sources` |
| `builtin_plugins` | the eight plugins MoCode ships, in a fixed (prompt-stable) order |
| `default_plugin_dirs(cwd, home)` | where a project's plugins are looked for, most specific first |
| `PluginSpec` | one discovered plugin, before its code is imported: `name`, `description`, `version`, `directory`, `module`, `source` |
| `discover` / `load_plugin` / `read_manifest` | discovery, import and manifest parsing |
| `namespace_dir(source, namespace)` | the directory a plugin ships for a namespace, or `None` — offered, never read |
| `HOST_NAMESPACE` / `MANIFEST` | `"mocode"` and `"plugin.json"`, in one place |

### Builtin plugins

| Name | Contributes |
|---|---|
| `filesystem` | `read`, `write`, `edit` — real filesystem, relative paths into the project |
| `shell` | `bash`, `bash_output`, `kill_shell` — one persistent bash session per conversation |
| `skills` | the `skill` tool, `/skill:<name>` commands, the prompt's skills section; `SkillManager.register` adds a skill in code |
| `default-prompts` | the four sections a fresh prompt starts with: `guidelines`, `agents`, `environment`, `time` |
| `session` | `/export`, `/clear` |
| `help` | `/help` |
| `effort` | `/effort` |
| `cache-protect` | pins the request prefix and announces its drift |

## `mocode.cli` — the terminal front-end

The terminal is a consumer of `host/`, not a special case in the loop. Import
from here to write for the terminal; anything that works in every frontend is
a host plugin instead.

| Name | Purpose |
|---|---|
| `CLIApp` | the REPL: assembly, input, command dispatch and Ctrl-C |
| `CLIPlugin` | the terminal's own extension surface — `build(cli)` contributes chrome nothing else can honour |
| `CLIRenderer` | a pure consumer: subscribes to the conversation's stream and draws it |
| `Display` | terminal primitives: styled lines, the prompt, in-place rewrite; `.info` / `.warn` / `.error` / `.print` / `.render` / `.format` |
| `Theme` | the terminal's whole appearance in one dataclass, keyed by what a line means |

## `mocode.plugins` — the plugin SDK

A plugin author imports from here and nowhere else — it is a re-export of the
`core/` and `host/` surfaces a plugin is allowed to touch, so a plugin never
reaches into `mocode.core`:

| Group | Names |
|---|---|
| plugin and context | `Plugin`, `BuildContext`, `HostContext`, `Conversation` |
| tools | `Tool`, `ToolRegistry`, `ToolResult`, `ToolError`, `ToolConflictError`, `ToolPolicy`, `ToolCallContext`, `ToolDispatcher`, `DispatchResult` |
| commands | `Command`, `CommandContext`, `CommandResult`, `CommandRegistry`, `Kind`, `CONTINUE`, `EXIT` |
| hooks | `AgentHook`, `HookRunner`, `IterationContext`, `RequestContext`, `ResponseContext` |
| prompt | `Prompt`, `Section` |
| events | `Event`, `RunStarted`, `RunFinished`, `RunFailed`, `IterationStarted`, `IterationFinished`, `TextDelta`, `ReasoningDelta`, `ToolCallArgsDelta`, `ToolCallStarted`, `ToolOutput`, `ToolCallFinished`, `Notice`, `PluginMessage`, `TOOL_*`, `ToolStatus`, `RunState` |
| loop | `AgentConfig`, `Turn`, `LoopResult`, `IterationLimit`, `EventChannel`, `Subscription` — the loop itself is the host's to assemble (`mocode.core` if you build one yourself) |
| providers | `Provider`, `Chunk`, `Usage`, `ToolCall`, `ToolCallDelta`, `ModelSpec`, `Effort`, `EFFORTS`, `RetryPolicy`, `RetryDeadlineExceeded`, `StreamAccumulator` |
| transcript | `IMAGE_PLACEHOLDER`, `assistant_message`, `tool_result`, `tool_call_dicts`, `text_of`, `reasoning_of`, `content_parts`, `is_user`, `is_assistant`, `is_tool_result`, `tool_calls_of`, `tool_call_id`, `tool_call_name`, `tool_call_arguments`, `tool_call_args`, `tool_call_by_id`, `answered_call_id` |

## `mocode.testing` — the public test kit

The scripted model and the reading helpers. Importing this package pulls in
`mocode.core`: it is a testing dependency, never a runtime one, and it is not
reachable from `import mocode`.

| Name | Purpose |
|---|---|
| `MockProvider` | replay canned responses as chunk streams, and record every request in `.calls`; `chunk_size=` splits text the same way |
| `SlowProvider` | a `MockProvider` whose turn never finishes on its own — cancellation, timeout and concurrent-turn tests |
| `say(text, finish_reason=…)` | a plain-text answer: the entry that ends a script |
| `call_tool(name, args)` | asks for one tool call; `tool_call_response(name, args="{}", call_id="c1")` is the same from an argument string |
| `response_to_chunks` / `ARG_FRAGMENT` | how a response is split into chunks; the fragment size tool arguments arrive in |
| `collect(agen)` | drain an async iterator into a list |
| `terminal(events)` | the one event that ended the turn — `RunFinished` or `RunFailed` |
| `events_of_type(events, cls)` | the events of one class, in stream order |
