# codemode — a Python script that calls tools

Registers one model-only `codemode` tool: the model writes a Python script,
the plugin runs it in-process, and only the script's output comes back. The
whole DSL contract lives in `description.py` — a static string fixed at
`build()` time, so a scripted model can be taught the surface without any
other prompt section.

## Module map

- `plugin.py` — the plugin and the `codemode` tool: option merging
  (`# @options:` comment vs explicit argument), the deadline wrapper, the
  store commit dance and the result build.
- `env.py` — `build_env()`: assembles the script's globals — `tools`, the
  output-pipeline names, the `store`/`load` closures, `parallel`, the
  discovery helpers, the nine read-only modules — and the facade dict
  ToolBox forgives (`tools.text` and friends bind the very same objects).
- `toolbox.py` — ToolBox: exact-name resolution (one spelling — the
  registered full name; a miss reports the candidate full names), the
  semaphore-bounded call, the built-in facade fallback, `dir(tools)`, and
  the catalogue helpers (`tool_entries`, `describe_tool_entry`).
- `result.py` — the first-class `Result` (`.ok`/`.content`/`.details`/
  `.tool`/`.json()`/`.structured` plus the Mapping protocol over the four
  wire fields), `ToolCallError`, and `parallel`/`Batch` with per-call
  failure capture.
- `store.py` — the cross-call JSON store; limits are checked at commit
  time, in serialized-JSON characters.
- `runtime.py` — the exec wrapper: the restricted builtins, the gated
  `__import__` (whitelist in `IMPORT_WHITELIST`), and the wrapper-offset
  line-number math.
- `search.py` — the frozen BM25-lite ranking (full descriptions) and the
  80-character catalogue preview applied at the return boundary.
- `output.py` — the output pipeline, head+tail truncation with the
  imperative temp-file notice, and result composition.
- `description.py` — the static tool description, the whole v2 contract.

## What it does

- `build()` registers the `codemode` tool and nothing else: no prompt
  section, no `prepare()`, no `close()` — the plugin is stateless, and the
  only per-conversation state is the plugin-state slot backing `store`.
- A call merges its options (a first-line `# @options: {...}` comment is the
  base, the explicit `options` argument wins), assembles the script's
  globals via `build_env()`, and runs the script as the body of one async
  function. On success — or `exit()` — the store commits; output composes
  into the result either way.
- **Program-origin contract**: every `tools.<name>(...)` call is a
  `dispatcher.run(..., origin="program", parent_call_id=<codemode's call>)`.
  The calls are observable on the event stream, but they never enter the
  conversation's messages and never fold into the turn's
  `tool_calls_made` count. That is what lets a script call program-only
  tools (deferred MCP tools) the model's interface does not list.
- **Results are first-class**: a successful call returns a `Result`
  (`ok`, `content`, `details`, `tool`, `status`, `error_code`, plus
  `.json()` and `.structured`); the Mapping protocol over the four wire
  fields survives from W1 (`res.get("content")`, `dict(res)`). A single
  failed call raises `ToolCallError` with the tool name — fail fast. Only
  `parallel` turns failures into data: the `Batch` it returns is a plain
  list of Results in argument order whose `.ok`/`.failed` never reorder
  anything, and its `concurrency=` overrides the global cap (published to
  the box through a context variable, so the two semaphore layers cannot
  stack or deadlock). The asymmetry — single throws, batch captures — is
  deliberate and documented as such.
- **Facade forgiveness**: when no registered tool matches, `tools.<name>`
  falls back to the built-ins (`describe_tool`, `all_tools`,
  `search_tools`, `store`, `load`, `text`, `console`, `image`, `print`,
  `exit`) and returns the built-in itself. Registered tools always win —
  an MCP tool answers only to its full `mcp__<server>__<tool>` name, so
  the bare namespace belongs to the built-ins even when a registered tool
  ends in it — `codemode` itself is still refused first, and a name no
  tool and no built-in claims raises naming the candidate full names. The
  catalogue stays strict: `dir(tools)` and `all_tools()` list registered
  tools only, and `describe_tool_entry` keeps the callable-only filter.
- **Snapshot vs live**: `all_tools()` and `search_tools()` read one snapshot
  of the program-audience registry taken at script start, and `dir(tools)`
  freezes with it. Name lookup and `describe_tool()` read the live registry
  instead: a tool registered mid-script is callable under its exact name
  and describable, but it never appears in the snapshot tables. Catalogue
  entries preview descriptions at 80 characters; ranking reads the full
  text.
- **Deadlines, two layers**: the plugin wraps the script in its own
  `asyncio.wait_for` only when a deadline is explicit (`options.timeout_ms`,
  the `@options` comment, or `plugins.codemode.timeout_s`). A fired deadline
  is a normal failure result that keeps the output already emitted, with
  `details["timed_out"] = True` and pending store writes discarded. With no
  deadline the call relies on the dispatcher's tool-timeout fallback, which
  cancels the call wholesale — partial output is lost. The tool's
  `policy()` reports the same deadline to the dispatcher, so both layers
  agree on the budget, and a turn's `CancelledError` unwinds untouched
  through either path.
- **Limits**: output past `max_output_chars` (per-call option or
  `plugins.codemode.max_output_chars`, default 12000) keeps head and tail,
  with an imperative notice in the middle — the omitted character count,
  the temp-file path, and an explicit read-before-relying instruction
  pointing at `tools.read`. Store sizes are checked at commit — one value
  up to 256KB, the whole store up to 1MB, counted as serialized JSON — and
  a breach fails the run with nothing applied.
  `plugins.codemode.max_concurrency` caps how many of a script's calls run
  at once through one per-script semaphore; absent means unlimited, and an
  unusable value is reported once per conversation and ignored.
- **Error locations**: the compiled source is
  `async def __codemode__():` plus the indented script, so a traceback line
  is one more than the line the model wrote; the reported line number minus
  that one wrapper line is the script line, and the result shows
  `Script error (line N): ...` with that line's source beneath it. Only
  frames whose filename is the synthetic `<codemode>` qualify — a
  `ToolCallError` or `CodemodeError` surfacing inside the tool box or the
  plugin keeps the plain error format, and the same offset fix applies to
  `SyntaxError`.

## The script environment

- Everything a script may touch is one globals dict assembled by
  `build_env()`: the nine read-only modules (`asyncio`, `json`, `re`,
  `math`, `datetime`, `textwrap`, `collections`, `itertools`, `functools`),
  `tools`, `text`/`console`/`print`/`image`/`exit`, the `store`/`load`
  closures, `parallel` and the three discovery helpers.
- `import` is gated, not absent: the `import` statement resolves
  `__import__` from the frame's builtins, so `RESTRICTED` carries a gated
  implementation whose whitelist is exactly the nine injected modules
  (`IMPORT_WHITELIST`). `import asyncio` and `from asyncio import gather`
  both succeed; submodules, relative imports and everything else raise
  ImportError naming the whitelist and pointing at `tools.*`.
  `__import__("os")` by hand hits the same gate.
- `RESTRICTED` (the `__builtins__` a script runs with) is a frozen safe set
  (`abs`, `all`, `sorted`, `zip`, ..., `print`) plus every builtin
  exception class collected by rule — `isinstance(value, type) and
  issubclass(value, Exception)` over `vars(builtins)` — plus `dir` and the
  gated `__import__`. The rule is also the boundary: `BaseException` and
  its non-`Exception` children (`KeyboardInterrupt`, `SystemExit`,
  `GeneratorExit`) fail the check with no special case, so a script can
  name what it catches (`except RuntimeError`) yet can never bind the class
  that would swallow the `CancelledError` unwinding a stopped or timed-out
  script.
- `print` is the one whitelisted builtin the env replaces: the script's
  `print` appends one output item (non-strings JSON-rendered like `text()`)
  instead of writing to the host's stdout, where no script reader could
  ever see it.

## Not a sandbox

The restricted builtins are a stability and resource boundary, not a
security one: the script runs inside the mocode process on the model's
behalf, the injected modules are the process's own module objects, and the
whitelist only stops a script from opening files or spawning threads *by
accident*. The model already has a shell tool — treat a script as
orchestration sugar over the tools it may call, not as a defence against
one.
