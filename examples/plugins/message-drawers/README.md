# message-drawers

The official example of a **custom message drawer**: a plugin whose tool
publishes a `PluginMessage` and whose terminal half decides how that
message looks.

## What it does

- **Host side** (`mocode/plugin.py`) registers a `diff-demo` tool
  (`Tool(schema=..., with_context=True)`). The tool computes a small,
  fixed unified diff and publishes it as
  `PluginMessage(kind="diff-demo/patch", data={"diff": ...})`. The payload
  is plain data on the conversation's event stream — every frontend
  receives it, none is required to know what it means.
- **Terminal side** (`mocode.cli/plugin.py`) registers a drawer for that
  kind:

  ```python
  ctx.drawers.register("diff-demo/patch", draw_diff)
  ```

  `draw_diff` returns one `Line` per diff row — additions in the theme's
  `success` colour, removals in `error`, `@@` hunk headers in `info`,
  context dimmed. A frontend with no drawer for the kind falls back to the
  message's own summary line, so the host half never learns about screens.

## Try it

Install the plugin into a project (see `docs/plugins.md`), then ask the
model to call `diff-demo`, or run the loading test:

```bash
uv run pytest tests/test_content.py -q
```

## Optional: full markdown rendering

The terminal can also render settled answer blocks as full markdown
through `rich`. It is an optional dependency group and changes nothing
when absent:

```bash
uv sync --group tui   # install rich; without it everything above works unchanged
```
