# ref · MCP 协议要点（2026-07-28 modern + ≤2025-11-25 legacy）

> 权威来源：`modelcontextprotocol.io/specification/2026-07-28/`（当前版本，2026-07-28）。
> 本文件是自包含摘录，供 `mcp` 插件实现与测试使用。**实现必须是 dual-era 客户端**
> （同时支持 modern 与 legacy），因为线上大量 server 仍是 legacy。

---

## 0. 一句话

MCP = JSON-RPC 2.0 + 传输层。**modern（≥2026-07-28）没有握手**：每个请求在
`_meta` 里自带协议版本/身份/能力；**legacy（≤2025-11-25）用 `initialize` 握手**。
client 用 `server/discover` 探测 server 属于哪个 era，再决定怎么说话。

## 1. 消息格式（JSON-RPC 2.0）

请求：`{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{...}}`（`id` 不得为 null）。
成功：`{"jsonrpc":"2.0","id":1,"result":{"resultType":"complete", ...}}`。
错误：`{"jsonrpc":"2.0","id":1,"error":{"code":-32602,"message":"...","data":{}}}`。
通知：无 `id`，无响应。

**`resultType`**（modern）：`"complete"` | `"input_required"`；**缺失一律当 `"complete"`**
（与 legacy server 兼容）。

**错误码**：
- JSON-RPC 标准：`-32700` parse、`-32600` invalid request、`-32601` method not found、
  `-32602` invalid params、`-32603` internal。
- `-32000..-32019`：legacy 实现自定义（`-32002` = resource not found，旧版；客户端仍应接受）。
- `-32020..-32099`：spec 保留。`-32020` HeaderMismatch、`-32021` MissingRequiredClientCapability、
  **`-32022` UnsupportedProtocolVersion**（`data.supported` 列出 server 支持的版本）。

## 2. 两个 era

| | Modern（≥2026-07-28） | Legacy（≤2025-11-25） |
|---|---|---|
| 版本协商 | 无握手；每请求 `_meta` | `initialize` 握手 |
| `_meta` | `io.modelcontextprotocol/protocolVersion`、`clientInfo`、`clientCapabilities` | 无 |
| 结果 | 带 `resultType` | 无（或缺省） |
| server→client 请求 | MRTR：`InputRequiredResult.inputRequests` | 直接发 JSON-RPC 请求 / 在 initialize 后发 |
| 变更通知 | `subscriptions/listen` 长连接；`_meta` 带 subscriptionId | server 主动推 `notifications/tools/list_changed` 等 |
| HTTP 会话 | **无**协议级 session；无 GET stream | `Mcp-Session-Id`；可选 GET SSE |
| 探测 | server **MUST** 实现 `server/discover` | 对未知方法回普通错误 |

## 3. `server/discover`（modern，stdio 探测首选）

请求（`params` 只有 `_meta`）：
```json
{"jsonrpc":"2.0","id":"discover-1","method":"server/discover","params":{"_meta":{
  "io.modelcontextprotocol/protocolVersion":"2026-07-28",
  "io.modelcontextprotocol/clientInfo":{"name":"mocode","version":"0.4.0"},
  "io.modelcontextprotocol/clientCapabilities":{}}}}
```
响应：
```json
{"jsonrpc":"2.0","id":"discover-1","result":{
  "resultType":"complete",
  "supportedVersions":["2026-07-28","2025-11-25"],
  "capabilities":{"tools":{},"resources":{}},
  "_meta":{"io.modelcontextprotocol/serverInfo":{"name":"ExampleServer","version":"1.0.0"}},
  "instructions":"...",
  "ttlMs":3600000,
  "cacheScope":"public"}}
```

## 4. Dual-era 探测（**冻结实现策略**）

### stdio（无 HTTP 状态码，必须探测）
1. 先发 `server/discover`，`_meta` 用首选版本 `2026-07-28`，探测超时（建议 5s）。
2. 三种结果（规范明文）：
   - 返回 `DiscoverResult` 且 `supportedVersions` 含 `2026-07-28` → **modern**，
     用 `2026-07-28` 继续。
   - 返回 recognized modern error（`-32022`/`-32021`/`-32020`）→ **modern**；
     从 `data.supported` 里选一个 ≥`2026-07-28` 的版本重试；若没有 → 回退 legacy。
   - 其它错误 / 无响应超时 → **legacy**，改走 `initialize`。
   - **不得只按某个错误码判断 legacy**；标准明确："anything else identifies a legacy server"。
3. era 是 server 属性，**按 server 配置缓存**（同进程生命周期；可选跨重启持久化）。

### HTTP
- 发一个 modern 请求（带 `MCP-Protocol-Version: 2026-07-28` 头 + body `_meta`）。
- `400` 且 body 是 recognized modern error（`HeaderMismatch`/`UnsupportedProtocolVersion`/
  `MissingRequiredClientCapability`）→ modern（按 `supported` 重试）。
- `404` + JSON-RPC `-32601` → modern server（只是方法不存在）。
- 其它 `4xx` 且无 recognized modern error body → legacy，回退 `initialize`（必要时再退 HTTP+SSE）。
- 具体见 `04-w3-mcp-http.md`。

## 5. legacy `initialize` 握手（≤2025-11-25）

```json
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{
  "protocolVersion":"2025-11-25",
  "capabilities":{},
  "clientInfo":{"name":"mocode","version":"0.4.0"}}}
```
响应：`{"result":{"protocolVersion":"2025-11-25","capabilities":{...},
"serverInfo":{"name":...,"version":...},"instructions":"..."}}`。
随后发通知：`{"jsonrpc":"2.0","method":"notifications/initialized"}`（无 id）。
- `protocolVersion` 取 server 返回值；本实现发 `"2025-11-25"`。
- `serverInfo` 在 legacy 是顶层字段（modern 在 `_meta` 里）。

## 6. modern 每请求 `_meta`（冻结）

所有 modern 请求都把这三个键放进 `params._meta`：
```json
{"io.modelcontextprotocol/protocolVersion":"2026-07-28",
 "io.modelcontextprotocol/clientInfo":{"name":"mocode","version":"0.4.0"},
 "io.modelcontextprotocol/clientCapabilities":{}}
```
（`tools/call` 的 `inputResponses`/`requestState` 见 §8。）

## 7. 方法

### 7.1 `tools/list`
`params` 可带 `cursor`。响应 `result`：`tools`（`[{name,title?,description?,inputSchema,
outputSchema?,annotations?,icons?}]`）、`nextCursor?`、`ttlMs?`、`cacheScope?`、`resultType?`。
- `inputSchema` 已是 JSON Schema object node → 直接作为 mocode `Tool.schema`。
- 循环取页直到无 `nextCursor`。忽略 `icons` / 未知字段。

### 7.2 `tools/call`
`params={"name":<原始工具名>,"arguments":<dict>}`（modern 再加 `_meta`）。
响应 `result`：`content:[ContentBlock]`、`structuredContent?`、`isError?`、`resultType?`。
- ContentBlock：`{type:"text",text}`、`{type:"image",data,mimeType}`、
  `{type:"audio",...}`、`{type:"resource",resource:{...}}`、`{type:"resource_link",...}`。
- `isError:true` ⇒ mocode 侧抛 `ToolError`。
- **`resultType:"input_required"`**（modern MRTR）⇒ mocode v1 无 elicitation UI，
  记 `ToolError("server requires user input; elicitation is not supported")`；见 §8。

### 7.3 resources（W3 可选）
- `resources/list` `{cursor?}` → `{resources:[{uri,name,description?,mimeType?}], nextCursor?}`
- `resources/templates/list` → `{resourceTemplates:[{uriTemplate,name,...}]}`
- `resources/read` `{uri}` → `{contents:[{uri,mimeType?,text?|blob?}]}`（`blob` base64）
- 错误码：新规范用 `-32602`；旧 server 可能用 `-32002`，客户端应接受。

### 7.4 通知 / 订阅
- **legacy**：server 主动推 `notifications/tools/list_changed` /
  `notifications/resources/list_changed` / `notifications/resources/updated` /
  `notifications/message`（logging）/ `notifications/progress`。
- **modern**：长连接变更通知走 `subscriptions/listen`（请求返回 SSE 流，`_meta` 带
  `io.modelcontextprotocol/subscriptionId`；先发 `notifications/subscriptions/acknowledged`）。
  **v1 不实现 `subscriptions/listen`**（W3 可选）：modern server 不推变更，工具列表在
  连接时取一次；登记遗留。
- `notifications/cancelled`（stdio）取消在途请求。

### 7.5 `ping`
`ping` → `{}`；保活，非必需。

## 8. Multi Round-Trip Requests（MRTR，modern）

server 需要用户输入时**不发自己的请求**，而是在 `tools/call` 结果里返回：
```json
{"resultType":"input_required",
 "inputRequests":{"github_login":{"method":"elicitation/create","params":{
   "mode":"form","message":"...","requestedSchema":{...}}}},
 "requestState":"<opaque base64>"}
```
client 收集输入后**用新的 JSON-RPC id** 重试原请求，带 `inputResponses` 与 `requestState`：
```json
{"method":"tools/call","id":<new>,"params":{...,"inputResponses":{
  "github_login":{"action":"accept","content":{"name":"octocat"}}},
  "requestState":"<echo>"}}
```
mocode v1 **不支持 elicitation**（无交互 UI）：收到 `input_required` 即报错。登记遗留。

## 9. stdio 传输

- `spawn(command, *args)` **不经 shell**；stdin/stdout 走 JSON-RPC，**stderr 是日志**
  （MAY capture，SHOULD NOT 当作错误）。
- framing：**newline-delimited JSON**，每条消息单行、不得含裸换行；client MUST NOT 向
  stdin 写非 MCP 消息。
- 读侧一个循环按行解析；空行跳过；坏行 report + 丢弃，不断连接。
- 取消：client 发 `notifications/cancelled` 引用请求 id。
- 关停：关 stdin → 等退出 → OS 强杀（POSIX SIGTERM→SIGKILL；Windows TerminateProcess/Job Objects）。
- server 异常退出：**SHOULD 重启**；协议无状态，在途请求丢失可对 fresh 进程重试。

## 10. streamable HTTP（W3，modern + legacy）

- 单 MCP endpoint，POST 每个 JSON-RPC 消息。
- **modern（2026-07-28）**：
  - 头：`MCP-Protocol-Version: 2026-07-28`（必须，且须与 body `_meta` 一致）、
    `Mcp-Method: <method>`（必须）、`Mcp-Name: <params.name|params.uri>`（`tools/call`/
    `resources/read`/`prompts/get` 必须）、`Content-Type: application/json`、
    `Accept: application/json, text/event-stream`。
  - **无协议级 session**（移除了 `Mcp-Session-Id`）；**无 GET stream**。
  - 响应：`application/json` 单对象 或 `text/event-stream`（请求作用域 SSE：先通知后终响应，
    终响应后关闭）；通知 POST → `202 Accepted`。
  - server→client 交互走 MRTR（§8），不在 SSE 上发独立请求。
  - 变更通知走 `subscriptions/listen`（§7.4），v1 不做。
  - `x-mcp-header`：server 可在 tool `inputSchema` 参数上标注，client **MUST** 把参数值
    镜像为 `Mcp-Param-<name>` 头；非法标注的 tool **MUST** 从 `tools/list` 结果剔除。
    （W3 可选严格执行；v1 可忽略 `x-mcp-header` 并在报告登记。）
- **legacy（≤2025-11-25）**：`Mcp-Session-Id`（initialize 响应头，后续请求回带）、
  可选 GET SSE、SSE 上可有 server→client 请求。W3 dual-era 回退路径。
- 重试：网络错误 / 408 / 429 / 5xx → 2 次退避（0.5s/1.5s）；**tools/call 不自动重试**。
- SSE 解析：`data:` 行累积到空行 dispatch；`:...` 是 keep-alive 注释，忽略；
  `event:`/`id:`/`retry:` 忽略；不支持 `Last-Event-ID` 断点续传。

## 11. 最小假 server（测试）

- stdio 假 server：`sys.executable` 起一个 Python 脚本，逐行读 stdin、写 stdout。
  按测试需要实现 **modern** 或 **legacy** 行为：
  - modern：应答 `server/discover`、`tools/list`（带 `resultType`）、`tools/call`；
    可返回 `input_required` 以测 MRTR 处理。
  - legacy：应答 `initialize` + `notifications/initialized` + `tools/list`/`tools/call`
    （无 `resultType`）。
- HTTP 假 server（W3）：stdlib `http.server`，按 path 返回 JSON 或 SSE；
  modern 读 `MCP-Protocol-Version`/`Mcp-Method` 头，legacy 读 `Mcp-Session-Id`。

## 12. 命名与映射

- mocode 工具名 = `mcp__<normalize(server)>__<normalize(tool)>`；`normalize` 把非
  `[A-Za-z0-9_]` 替换 `_`。
- `tools/call` 的 `name` 用**原始工具名**。
- 模型不可见 ≠ 不可调用：`codemode` exposure 的工具 `availability="program"`，脚本经
  dispatcher（program origin）可见。
