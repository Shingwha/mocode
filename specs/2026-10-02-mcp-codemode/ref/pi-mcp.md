# ref · pi MCP 官方文档（相关小节摘录）

> 来源：pi `docs/mcp.md`（本地安装）。mocode 的 exposure 语义与配置思路照此对齐；
> 协议 era 的现代部分以 `ref/mcp-protocol.md`（2026-07-28）为准。

Pi connects to [Model Context Protocol](https://modelcontextprotocol.io) servers over stdio or streamable HTTP and makes their tools and resources available to the model.

## Configure servers

Pi reads user-level servers from `~/.pi/agent/mcp.json` and project servers from `.pi/mcp.json`. A project entry replaces a user-level entry with the same name.

The format matches other MCP clients:

```json
{
  "mcpServers": {
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "."]
    },
    "docs": {
      "url": "https://example.com/mcp",
      "headers": {"Authorization": "Bearer ${DOCS_TOKEN}"},
      "description": "Search and read the product documentation"
    }
  }
}
```

Stdio servers use `command`, `args`, `env`, and `cwd`. Relative `cwd` values resolve against the session directory. A leading `~/` names the home directory.

HTTP servers use `url`, `headers`, and `oauth`. The legacy SSE transport is not supported.

Both server types support:

- `timeout`: per-request timeout in seconds (default 60). Progress notifications reset it.
- `enabled: false`: keep the entry without connecting to it.
- `exposure` and `toolExposure`: control how tools reach the model.
- `description`: what the server offers, in a sentence. It lists the server in the system prompt, tool search ranks the server's tools by it, and codemode's `describeNamespace()` returns it. Without it, the first line of the server instructions is used once the server connects.

Keep personal servers and servers with credentials in the user-level file. Use the project file only for servers the project requires, and only in trusted projects.

### Configuration rules

- Server names may contain only letters, digits, `_`, and `-`. Tools are named `mcp__<server>__<tool>`, with every character other than letters, digits, and `_` replaced by `_`; tools of a server whose names then collide all get a hash suffix. Server names that differ only in `-` and `_` count as the same server: a second one is rejected, and a `mcp.json` server overrides a registered one.
- `type` is optional. A `command` selects stdio and a `url` selects streamable HTTP. When present, `type` must be `stdio`, `http`, or `streamable-http`.
- `sse` is rejected. Servers that document an SSE endpoint often also provide streamable HTTP, commonly at `/mcp` instead of `/sse`.
- `command` is one executable and `args` contains its arguments. It is not a shell command string.
- `env` and `headers` values can use environment variables such as `${GITHUB_TOKEN}`. They can also run a command with `!command`, but the command must make up the whole value.
- Invalid entries are reported and skipped without preventing other servers from connecting.

### Diagnose connection problems

Pi connects every enabled server in the background when a session starts. A server's tools appear once it connects; the `codemode` description does not list them, so it does not change when servers connect. The first prompt waits up to 10 seconds only for servers with `direct` tools, which must be declared in its request. Other servers are waited for when they are needed: a codemode script waits for the servers it names and, when it calls `searchTools()` or reads `ALL_TOOLS`, for all of them. HTTP network errors and transient statuses (408, 429, and 5xx) are retried twice. A dropped connection is shown as disconnected and reconnects on the next call. When a server announces a changed tool list, new tools are added and withdrawn tools become unreachable.

Stopping a stdio server closes its stdin, sends SIGTERM, then sends SIGKILL to its process group.

## Control tool exposure

Each server tool is registered as `mcp__<server>__<tool>`. The server's `exposure` determines how the model reaches it:

| Exposure | Behavior | Typical use |
|---|---|---|
| `codemode` (default) | Callable from `codemode` scripts, but neither declared to the model nor listed in the codemode description. Scripts find tools with `searchTools()`, `describeTool()`, or `ALL_TOOLS`. | General MCP servers, especially when scripts should combine or filter calls. |
| `deferred` | Not declared until `tool_search` loads a match for the next model call. | Large servers whose tools should be called directly after discovery. |
| `direct` | Declared to the model like a built-in tool and also callable from codemode. | Small, frequently used tool sets. |
| `hidden` | Registered but unreachable. | Servers or tools that should remain unavailable. |

`codemode-deferred` is accepted as an alias for `codemode`.

Servers with `codemode` or `deferred` tools are listed in the `mcp_servers` section of the system prompt, with how their tools are reached and one line from the configured `description` or, once connected, from the server instructions.

Pi activates `codemode` when a server with `codemode` exposure connects. It activates `tool_search` for a server with `deferred` exposure. To make the model see a tool without searching, give it `direct` exposure with `toolExposure`.

`toolExposure` overrides the server exposure for individual tools. Keys are exact server tool names or patterns where `*` matches any characters. Exact names win over patterns; among patterns, the first match wins. A server with `hidden` exposure can expose only selected tools:

```json
{
  "mcpServers": {
    "github": {
      "url": "https://api.githubcopilot.com/mcp/",
      "exposure": "deferred",
      "toolExposure": {
        "search_code": "direct",
        "get_*": "codemode",
        "delete_*": "hidden"
      }
    }
  }
}
```

`pi mcp list` marks tools whose exposure differs from their server.

Tools with `codemode` or `deferred` exposure can be reached through either indirect mechanism: codemode scripts can call them, and `tool_search` can load them. Codemode calls do not depend on the active tool set, so they remain available after `/tree`, resume, and fork.

To keep `codemode` active without MCP servers, add `"defaultTools": ["+codemode"]`. To prevent automatic codemode activation, set `"autoEnableCodemode": false` beside `mcpServers`.

Text results over 20 KB reach the model with their middle removed around a `…N chars truncated…` marker. The full text is saved to a temporary file named in the result. Codemode scripts receive the complete result and can reduce it before returning output to the model.

Codemode scripts receive the complete MCP `CallToolResult`, including `content`, `structuredContent`, and `isError`. A result with `isError` resolves inside scripts but is reported as an error for direct calls. `image(result.content[0])` forwards an image block. Server instructions are not part of any tool description; scripts read them with `describeNamespace("mcp__<server>")`.

## Use resources

When a connected server offers resources, Pi adds the resource tools used by Codex and OpenCode:

- `list_mcp_resources` lists resources as JSON: `{ server?, resources: [{ server, uri, name, ... }], nextCursor? }`.
- `list_mcp_resource_templates` lists URI templates for resources the servers do not list directly.
- `read_mcp_resource` reads a resource by `server` and `uri`. Text reaches the model as text and images as images. Other binary resources are saved to temporary files, and the model receives the path.

These tools reach every enabled, non-hidden server with resources. Their exposure is the widest exposure among those servers: `direct`, then `codemode` or `deferred`.

Resources for MCP Apps, identified by `ui://` URIs or `text/html;profile=mcp-app`, are omitted because Pi does not render them.

Reading and listing resources is retried once after a transient HTTP error (408, 429, or 5xx). Tool calls are not retried because the server may already have performed them.

## Permissions

Every MCP call passes through Pi's tool pipeline. Extension `tool_call` and `tool_result` handlers, including permission gates, therefore apply to MCP tools. Calls made from codemode scripts carry the codemode call ID as `parentToolCallId`.

`pi.getAllTools()` reports the annotations declared by each server: `readOnlyHint`, `destructiveHint`, `idempotentHint`, and `openWorldHint`. Resource tools are marked read-only.

---

## mocode 取舍速查

| pi | mocode |
|---|---|
| 默认 exposure `codemode` 并自动激活 codemode | 默认 `auto`：codemode 插件启用则 `codemode`，否则 `direct`（D2） |
| `tool_search` 处理 `deferred` | v1 无 tool_search；`deferred` 等同 `codemode`（program） |
| 自动激活 codemode | 不跨插件激活；MCP 在 codemode 未启用且有 program-only 工具时发一次 warning |
| `describeNamespace()` | v1 不做 |
| OAuth / `!command` / `sse` | 不做/跳过（遗留） |
| 后台连接、direct 等 10s | 同（D9） |
| 结果 >20KB 中间截断 | codemode 自管 head+tail 截断 + 临时文件 |
