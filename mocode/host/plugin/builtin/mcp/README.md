# mcp — the MCP builtin plugin

Connects MoCode to [MCP](https://modelcontextprotocol.io) servers over
**stdio** (v1; streamable HTTP is a later wave) and exposes their tools as
`mcp__<server>__<tool>` — with a name fold, an exposure model, and a
dual-era protocol client.

## What it does

- Reads server entries from four sources, highest priority first:
  `plugins.mcp.servers` in config.json, `<cwd>/.mocode/mcp.json`,
  `<home>/mcp.json`, and each plugin directory's `mcp.json`. A same-named
  entry replaces the lower-priority one wholesale; names differing only in
  `-`/`_` are the same name.
- mocode's own files accept `enabled`, `timeout` (per-request seconds,
  default 60), `exposure` / `toolExposure`, `description` and `${VAR}`
  expansion from the environment. `!command` values are reported and used
  literally (not run, v1). `${PLUGIN_ROOT}` / `${PLUGIN_DATA}` are
  plugin-file concepts; using them in a mocode file skips the entry.
- A plugin directory's `mcp.json` follows the Agent Plugins 1.0.0 standard
  strictly: `$schema` must be
  `https://agent-plugins.org/schemas/1.0.0/mcp.schema.json`, entries carry
  only the standard fields, `args`/`env`/`cwd` expand
  `${PLUGIN_ROOT}`/`${PLUGIN_DATA}` (the child always gets both; `env` may
  not set them; an expanded `cwd` must stay inside its root), and unknown
  fields (like `exposure`) are reported and ignored.
- Protocol: **dual-era**. Modern servers (2026-07-28) are probed with
  `server/discover` and spoken to with a per-request `_meta`; recognized
  negotiation errors (`-32020..-32022`) retry at a supported version.
  Anything else means legacy (≤2025-11-25): the `initialize` handshake plus
  `notifications/initialized`, no `_meta`. The era is cached per server;
  a dropped connection reconnects once on the next call. Legacy
  `notifications/tools/list_changed` re-syncs the tool list; modern
  subscriptions are not implemented (v1). MRTR `input_required` results
  raise an error — there is no elicitation UI.
- Tool results: text blocks become the tool's text (images leave a
  `[image: <mimeType>]` placeholder and travel in `details["images"]`),
  other blocks in `details["content"]`, plus `structured_content`,
  `server` and `tool`. `isError` raises `ToolError` (`mcp_error`).
- Exposure decides who can use a tool: `direct` — the model and programs;
  `codemode`/`deferred` — programs only (via the dispatcher's
  `origin="program"`, e.g. codemode scripts); `hidden` — registered but
  unreachable. The default is `auto`: `codemode` when the codemode plugin
  is enabled, otherwise `direct`. `toolExposure` overrides per tool —
  exact names beat `*`-patterns, first match wins.
- Lifecycle: `build()` parses configuration and registers the program-only
  `mcp_status` anchor tool holding the runtime; `prepare()` connects
  servers whose tools the model may see under a bounded wait (default 10 s)
  and the rest in the background, then adds the `mcp_servers` prompt
  section; `close()` kills every child process. If program-only tools exist
  while the codemode plugin is disabled, one warning Notice is emitted per
  conversation.
- Not implemented on purpose (v1): OAuth, `!command`, the legacy `sse`
  transport (skipped with a hint to use the streamable HTTP endpoint),
  streamable HTTP, modern `subscriptions/listen`.

## Not a sandbox

Server commands run as the mocode process's own user with its environment
plus the configured overlay. A malicious or careless server can do anything
that user can — only enable servers you trust.
