# MoCode 0.3 — AGENTS.md

## Development Commands

```bash
uv sync                    # Install all deps (including dev, via --dev)
uv run pytest              # Run all tests (3619 lines across 13 files, ~228 tests)
uv run pytest -xvs         # Fast fail with verbose output
uv run pytest tests/test_builder.py -xvs                               # Single file
uv run pytest tests/test_builder.py::TestBuilder::test_minimal_build   # Single test
uv run pytest -k "test_tool" -xvs                                      # Pattern match
uv run mocode              # Launch interactive CLI
```

Build system: hatchling. Dependency manager: uv. Python >= 3.12. No linter configured.

## Architecture

### Dependency flow (zero cycles)

```
mocode/core/           ← zero deps on app/, providers/, tools/, hooks/
  ├── builder.py       Fluent builder: Agent().provider().prompt().tools().hooks().config().build()
  ├── agent.py         AgentLoop — the core chat engine (_loop method)
  ├── provider.py      @runtime_checkable Provider Protocol + Response/ToolCall/Usage DTOs
  ├── tool.py          Tool + ToolRegistry (instance-scoped, schema generation)
  ├── hook.py          AgentHook base class + HookRunner (per-hook error isolation)
  ├── prompt.py        Section-based Prompt builder with priority, nested sections, xml/text format
  └── skill.py         SkillManager — directory-based skill discovery (SKILL.md + YAML frontmatter)

mocode/providers/      OpenAI-compatible provider implementation
mocode/tools/          Factory functions returning Tool instances with closures
mocode/hooks/          Built-in hooks: CompactHook (auto 80% threshold), GoalHook
mocode/prompts/        System prompt definitions for main agent, subagent, compact
mocode/app/            Application layer: Config, Session, CLI (CLIApp, Display, Input, Commands)
```

### Core loop lifecycle (AgentLoop._loop)

1. `before_iteration` hook → LLM call via `provider.call()` → `on_response` hook
2. If tool_calls: run all in parallel (`asyncio.gather`), each gets its own `AgentHookContext` copy → `after_tools` hook
3. If no tool_calls: append assistant message → `after_iteration` hook
4. Repeat until no tool calls, `max_iterations` reached, or `continue_loop` is False

### Key architectural patterns

**Composition root.** CLIApp._build_agent() is the single composition root that wires everything together: provider, tools, hooks, prompt, and config. No dependency injection framework.

**Two agent layers.** SubAgent is NOT a separate process — it creates an isolated AgentLoop sharing the parent's provider, with a filtered ToolRegistry (blocks `sub_agent` and `compact` tools to prevent recursion).

**Workflow engine is external.** The workflow system (mocode/app/workflow/) is a separate concern from the agent loop. It spawns `mocode -p` subprocesses per step, with its own control flow (goto, lanes, phases). Not part of core/.

**Prompt is rebuilt on every `/resume`/`/clear`.** `_build_prompt()` re-reads AGENTS.md files each time, so changes take effect immediately without restart.

### Prompt section ordering

Sections are ordered by priority (stable → dynamic) to maximize prefix cache hit rate:
1. `guidelines` (priority 10) — static behavioral rules
2. `agents` (priority 20) — AGENTS.md content (read from disk)
3. `environment` (priority 30) — cwd, home, config paths
4. `tools` (priority 40) — ToolRegistry descriptions
5. `skills` (priority 50) — SkillManager metadata

### Session persistence

- Sessions auto-save after each chat turn (dirty tracking via `mark_dirty()`)
- Filename: `session_{uuid4().hex[:12]}.json` under `sessions/{workdir_sha256[:16]}/`
- `save_if_dirty()` on SIGINT ensures no data loss on abrupt exit

## Code Conventions

### Imports and module structure

- `from __future__ import annotations` at the top of every module
- Public API surface: `mocode.core` re-exports all core types; `mocode.tools` re-exports all tool factories
- Tests import from public API only (`mocode.core`, `mocode.tools`), never from internal submodules

### Tool factories (the most important pattern)

Every tool is a **factory function** (not a class) that returns a `Tool` instance:

```python
def ReadTool() -> Tool:
    def _read(args: dict) -> str:
        ...
    return Tool("read", _READ_DESC, _READ_PARAMS, _read)
```

The `Tool` class supports both sync and async functions (`inspect.iscoroutinefunction`). Config is captured via closure at factory time.

### Tool descriptions are dual-use

Every tool's `description` string serves double duty:
1. Becomes the OpenAI function schema `description` field
2. Becomes the `<tools>` section text in the system prompt

Keep descriptions precise and self-contained — they are the only documentation the LLM sees.

### Tool param conventions

```python
{
    "path": {"type": "string", "description": "..."},
    "offset": {"type": "integer", "description": "...", "default": 1},  # has default → optional
    "all": {"type": "boolean", "description": "...", "optional": True},  # explicit optional
    "output_mode": {"type": "string", "description": "...", "enum": ["content", "files"]},
}
```

- `"default"` — makes parameter optional (default value injected if missing)
- `"optional": True` — makes parameter optional without a default
- `"enum"` — list of valid values
- Params without `default` or `optional` are required

### Fluent builder pattern

```python
agent = (Agent()
    .provider(OpenAIProvider(api_key=key, model=model))
    .prompt(prompt_str)
    .tools([ReadTool(), BashTool()])
    .hooks([CLIDisplayHook(display)])
    .config(AgentConfig(max_tokens=8192))
    .build())
```

`.prompt()` accepts: `str`, `Prompt` instance, or `list[Section]`. If a list, sections are registered into a new Prompt.

### Hook system

Hooks use a class-based lifecycle with **per-hook error isolation** — one failing hook does not crash other hooks:

```python
class MyHook(AgentHook):
    async def before_iteration(self, ctx: AgentHookContext) -> None:
        # ctx.messages is mutable — modify in place
        pass
    async def on_response(self, ctx): ...    # read ctx.response/final_content/usage
    async def after_tools(self, ctx): ...     # read ctx.tool_calls/tool_results
    async def after_iteration(self, ctx): ...
    async def on_tool_start(self, ctx): ...   # read ctx.tool_name/tool_args
    async def on_tool_complete(self, ctx): ... # read ctx.tool_result/tool_error
    async def on_compact(self, ctx): ...
```

### Slash commands

Commands implement the `Command` Protocol (`@runtime_checkable`):

```python
class MyCommand:
    name = "/mycommand"
    description = "Does something"
    aliases = ("mycommand",)  # bare-word aliases for non-interactive mode

    async def run(self, ctx: CommandContext) -> CommandResult:
        ...
        return CommandResult.CONTINUE
```

Return `CommandResult.text("...")` to send text to the agent as a silent prompt.

### Config model

- `ModelEntry` can be `str` (simple) or `dict` with `name` + optional `extra_body`
- `ProviderEntry.from_dict()` handles both
- `Config.from_dict()` uses `fields(cls)` introspection to forward known fields, allowing forward-compatible configs

### Testing patterns

- **One test class per component**: `TestBuilder`, `TestChat`, `TestPrompt`, `TestTool`, `TestAgentHook`, etc.
- **MockProvider** — a local class (not imported) with `model` property + `async call()` returning canned `Response` objects
- **For tool tests**: register a MockProvider returning responses in sequence (tool-call response first, then final response)
- **MockAgent** pattern for hooks/tools that need an agent reference: minimal class with `.provider` attribute
- `@pytest.mark.asyncio` for all async tests
- `tmp_path` for filesystem tests
- `unittest.mock.patch` and `MagicMock` for complex mocking (CLIApp tests)

### Config precedence

Config file at `~/.mocode/config.json`. Structure:

```json
{
  "active_provider": "openai",
  "active_model": "gpt-4o",
  "providers": {
    "openai": {
      "name": "OpenAI",
      "api_key": "sk-...",
      "base_url": null,
      "models": [{"name": "gpt-4o"}, {"name": "gpt-4o-mini"}]
    }
  }
}
```

Models can be plain strings or dicts with `extra_body` for provider-specific params (e.g., `temperature`, `top_p`).

### AgentConfig defaults

| Field | Default | Description |
|---|---|---|
| `max_tokens` | 8192 | Max response tokens |
| `tool_result_limit` | 25000 | Truncation limit for tool results |
| `tool_timeout` | 240 | Per-tool timeout in seconds |
| `max_iterations` | 0 | Unlimited loop iterations (0 = no limit) |
