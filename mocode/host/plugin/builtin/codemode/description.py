"""The codemode tool description — the manual the model reads.

Static by decision: the description is fixed at build() time and carries the
whole DSL contract, so the model can write correct scripts without any other
prompt section.
"""

from __future__ import annotations

DESCRIPTION = """\
Run a Python script that calls other tools. Only the script's output reaches
you, so use it to run calls in parallel and filter large results before they
enter the conversation.

The script runs as the body of an async function: top-level `await` and
top-level `return` are allowed. Available names:

- `await tools.<name>(args)` — call a tool. Use `tools["exact-name"]` when the
  name is not a valid Python identifier, e.g. `tools["mcp__dev_radius__search"]`.
  A successful call returns an object with `.content` (text), `.details`
  (dict), `.status`; `str(result)` is `.content`. A failed call raises; use
  `asyncio.gather(..., return_exceptions=True)` to keep the successful ones.
- `text(value)` / `console.log(...)` — append output. `return value` does the
  same.
- `image(block)` — show an image block.
- `store(key, value)` / `load(key)` — small JSON state kept across codemode
  calls. `store(key, None)` deletes it.
- `search_tools(query, limit=8)` / `describe_tool(name)` / `ALL_TOOLS` —
  discover callable tools (including tools not listed in the tool interface).
- `exit()` — end the script successfully.

`asyncio`, `json`, `re`, `math`, `datetime`, `textwrap`, `collections`,
`itertools` and `functools` are available. There is no file system, network,
timer, `open`, or `import`. `codemode` cannot call itself. A whole-script
deadline can be set with `# @options: {"timeout_ms": 60000}` on the first
line, or the `options` argument.
"""
