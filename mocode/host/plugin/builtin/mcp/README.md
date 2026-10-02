# mcp — the MCP builtin plugin

Connects MoCode to [MCP](https://modelcontextprotocol.io) servers over
**stdio** (v1; streamable HTTP is a later wave) and exposes their tools as
`mcp__<server>__<tool>` — with a name fold, an exposure model, and a
dual-era protocol client built on the official `mcp` SDK (v2).

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
- Protocol: **dual-era**, carried by the official SDK's `Client(mode="auto")`
  — it probes `server/discover` for the current era (2026-07-28) and falls
  back to the `initialize` handshake for everything else (≤2025-11-25). The
  negotiated version is on the session; `era` is its display name
  (`modern` for 2026-07-28, `legacy` for the handshake eras). A dropped
  connection reconnects once on the next call; the SDK's client is an async
  context manager, so a reconnect is a fresh client. Legacy
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
  section; `close()` unwinds every session — the SDK's bounded shutdown
  kills each child (stdin close, grace, then the whole process tree). If
  program-only tools exist while the codemode plugin is disabled, one
  warning Notice is emitted per conversation.
- The child's stderr is a bounded tail kept in a temporary file for status
  and error reports (logging, never a protocol error); the child's
  environment is the whole process environment plus the entry's `env`
  overlay — the SDK layers `env` over a trimmed platform default, so the
  full environment is passed explicitly. Error codes are stable:
  `mcp_transport`, `mcp_timeout`, `mcp_input_required`, `mcp_closed`,
  `mcp_error`.
- Not implemented on purpose (v1): OAuth, `!command`, the legacy `sse`
  transport (skipped with a hint to use the streamable HTTP endpoint),
  streamable HTTP, modern `subscriptions/listen`.

## Not a sandbox

Server commands run as the mocode process's own user with its environment
plus the configured overlay. A malicious or careless server can do anything
that user can — only enable servers you trust.
