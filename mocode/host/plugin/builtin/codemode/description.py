"""The codemode tool description — the manual the model reads.

Static by decision: the description is fixed at build() time and carries the
whole v2 DSL contract, so the model can write correct scripts without any
other prompt section.
"""

from __future__ import annotations

DESCRIPTION = """\
Run a Python script that calls other tools; only its output comes back. The
script is an async function body: top-level `await`/`return` are legal —
never wrap code in `asyncio.run()`/`main()`; `asyncio.ensure_future` tasks
are never awaited, so await everything explicitly.

Results are first-class: `r = await tools.<name>(args)` gives a Result with
`.ok`, `.content`, `.details` (tool-specific facts), `.tool` (the resolved
name), `.json()` (content parsed as JSON, a diagnostic string on failure)
and `.structured` (an MCP tool's structuredContent, else None); it also
answers the Mapping protocol — `res.get("content")`, `dict(res)`. A SINGLE
FAILED CALL RAISES ToolCallError, with the tool name: fail fast by design.
For failures as data, batch — `rs = await parallel(tools.a(...),
tools.b(...))` returns a Batch (a list) in argument order with
`.ok`/`.failed`; a failed call never sinks the batch (its Result carries
`.error`), only bad arguments raise, and `concurrency=N` caps that batch
while overriding the global `max_concurrency` (`asyncio.gather(...,
return_exceptions=True)` is the asyncio-level equivalent).

`tools` resolves names in tiers: exact registered name
(`tools["mcp__dev_radius__search"]`), normalized form
(`tools.mcp__dev_radius__search`), then the MCP short name (`tools.search`)
when unambiguous — ambiguous short names raise listing the candidates,
exact names win. `codemode` cannot call itself. The facade forgives the
built-ins: `tools.describe_tool`/`tools.text`/`tools.store`/... return the
built-in itself; `dir(tools)` and the catalogue list registered tools only.

- `text(value)` / `console.log(...)` / `print(...)` append output in order;
  `return v` appends last on success; `image(block)` adds an image;
  `exit()` ends the script successfully.
- Past `max_output_chars` (default 12000) head and tail survive and the
  middle becomes "⚠ N chars truncated — before relying on this output,
  read the full result at <path> (e.g. via tools.read)": the full text
  waits in that temp file — read it before summarizing.
- `store(key, value)` / `load(key)` — small JSON state across calls;
  `store(key, None)` deletes. Limits: one value up to 256KB, the whole
  store 1MB, counted as JSON — no pre-truncation needed. For large
  payloads, write the file with a file-writing tool and `store` the path.
- `all_tools()` (script-start snapshot), `search_tools(query, limit=8,
  namespace=None, names_only=False)`, `describe_tool(name)` — every
  callable tool, even program-only ones. Catalogue descriptions are
  80-character previews; `describe_tool(name)` has the full text plus
  schema.
- `import` is gated to the injected modules — `import asyncio` and
  `from asyncio import gather` both work: asyncio, json, re, math,
  datetime, textwrap, collections, itertools, functools — already in
  scope, so use them directly; anything else raises ImportError pointing
  at `tools.*`. No file system, network, timers or `open`: use `tools.*`.

An explicit deadline (`options.timeout_ms`, a first-line
`# @options: {"timeout_ms": 60000}`, or `plugins.codemode.timeout_s`)
keeps the output so far (`timed_out`); with none, the agent's tool timeout
applies and partial output is lost. `plugins.codemode.max_concurrency`
caps parallel calls (default unlimited). A script error names its line —
`Script error (line N)` with that line's source — and builtin exception
classes are catchable by name.
"""
