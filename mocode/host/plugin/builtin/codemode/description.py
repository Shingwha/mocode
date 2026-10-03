"""The codemode tool description — the manual the model reads.

Static by decision: the description is fixed at build() time and carries the
whole DSL contract, so the model can write correct scripts without any other
prompt section.
"""

from __future__ import annotations

DESCRIPTION = """\
Run a Python script that calls other tools; only its output comes back. The
script is an async function body: top-level `await`/`return` are legal —
never `asyncio.run()`/`main()`; `asyncio.ensure_future` tasks are never
awaited. All names below are injected — use them directly, do not `import`.

- `await tools.<name>(args)` calls a tool. Own tools keep their name
  (`tools.bash`); an MCP tool answers to its folded full name, subscript or
  attribute (`tools["mcp__dev_radius__search"]`), and, when unambiguous,
  its short name (`tools.search`); ambiguous short names raise, listing
  candidates — exact names win. Success has `.content`, `.details`,
  `.status`, `.error_code` (Mapping too — `res.get("content")`); failure
  raises — `asyncio.gather(..., return_exceptions=True)` keeps successes;
  builtin exceptions are catchable by name.
- `text(value)` / `console.log(...)` / `print(...)` append output in order;
  `return value` appends last on success; `image(block)` adds an image;
  `exit()` ends the script. Past `max_output_chars` (default 12000), head
  and tail survive plus a temp-file path.
- `store(key, value)` / `load(key)` — small JSON state across codemode
  calls; `store(key, None)` deletes; commits on success, within size limits.
- `all_tools()` (script-start snapshot), `search_tools(query, limit=8,
  namespace=None, names_only=False)`, `describe_tool(name)` — every
  callable tool, even program-only ones.

`asyncio`, `json`, `re`, `math`, `datetime`, `textwrap`, `collections`,
`itertools`, `functools` are injected read-only — no file system, network,
timer, `open`, or `import`. `codemode` cannot call itself. An explicit
deadline (`options.timeout_ms`, a first-line
`# @options: {"timeout_ms": 60000}`, or `plugins.codemode.timeout_s`) keeps
the output so far (`timed_out`); with none, the agent's tool timeout applies
and partial output is lost. `plugins.codemode.max_concurrency` caps parallel
calls (default unlimited).
"""
