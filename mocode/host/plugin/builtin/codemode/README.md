# codemode — a Python script that calls tools

Registers one model-only `codemode` tool: the model writes a Python script,
the plugin runs it in-process, and only the script's output comes back. The
whole DSL contract lives in `description.py` — a static string fixed at
`build()` time, so a scripted model can be taught the surface without any
other prompt section.

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
- **Snapshot vs live**: `all_tools()` and `search_tools()` read one snapshot
  of the program-audience registry taken at script start, and the ToolBox's
  normalized-name and short-name maps freeze with it. Exact-name lookup and
  `describe_tool()` read the live registry instead (the latter through the
  callable-only filter): a tool registered mid-script is describable and
  exactly callable, but it never appears in the snapshot tables.
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
  `plugins.codemode.max_output_chars`, default 12000) keeps head and tail
  and writes the full text to a temp file the result points at. Store sizes
  are checked at commit — a breach fails the run with nothing applied.
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
  closures and the three discovery helpers. There is deliberately no
  `import`: an enumerable surface is what makes the contract writable and
  testable — `__import__` is simply absent from the restricted builtins.
- `RESTRICTED` (the `__builtins__` a script runs with) is a frozen safe set
  (`abs`, `all`, `sorted`, `zip`, ..., `print`) plus every builtin
  exception class collected by rule — `isinstance(value, type) and
  issubclass(value, Exception)` over `vars(builtins)` — plus `dir`. The
  rule is also the boundary: `BaseException` and its non-`Exception`
  children (`KeyboardInterrupt`, `SystemExit`, `GeneratorExit`) fail the
  check with no special case, so a script can name what it catches
  (`except RuntimeError`) yet can never bind the class that would swallow
  the `CancelledError` unwinding a stopped or timed-out script.
- `print` is the one whitelisted builtin the env replaces: the script's
  `print` appends one output item (non-strings JSON-rendered like `text()`)
  instead of writing to the host's stdout, where no script reader could
  ever see it.
- `ToolOutcome` stays a plain dataclass with the four fields
  (`content`/`details`/`status`/`error_code`) and adds the Mapping
  protocol on those keys, so `res.content`, `res.get("content")` and
  `dict(res)` all work; a failed call raises `ToolCallError`, which is why
  `gather(..., return_exceptions=True)` is the idiomatic fan-out.

## Not a sandbox

The restricted builtins are a stability and resource boundary, not a
security one: the script runs inside the mocode process on the model's
behalf, the injected modules are the process's own module objects, and the
whitelist only stops a script from opening files or spawning threads *by
accident*. The model already has a shell tool — treat a script as
orchestration sugar over the tools it may call, not as a defence against
one.
