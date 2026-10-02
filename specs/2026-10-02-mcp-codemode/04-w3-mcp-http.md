🚧 2026-10-03 实施中 — lead 修订：§1.6 冻结实现决策 + 写范围加 `tests/test_builtin_mcp.py` 窄化例外（T1 必然翻转两个现存断言）；分支 ff 至 master 后从 T1 开工

# Spec 04 · W3（可选）：MCP streamable HTTP + resources + subscriptions（波次 W3）

> 分支 `feat/mcp-http`；worktree `C:\Users\shifu\.worktrees\mocode\feat-mcp-http`。
> 前置：W2 已合并。深读：`ref/mcp-protocol.md` §7–§10 + `ref/agent-plugins-mcp-json.md` §4
> + W1a 工单。**本波可选**：用户未点名可跳过，不影响 W1/W2 交付。
> 绝不操作主检出，绝不 merge/push/tag。

## 0. 目标

给 `mcp` 内置插件补 **streamable HTTP** 传输（modern 2026-07-28 语义 + legacy 回退）、
只读 resources 工具，以及 modern 的变更通知订阅。**不新增依赖**（D6）：stdlib
`urllib.request` + 手写 SSE 解析，跑在 `asyncio.to_thread` 里。

## 1. 冻结点

### 1.1 modern HTTP（2026-07-28 语义，与 2025 版**不同**）

- 单 MCP endpoint，每个 JSON-RPC 请求一条 HTTP POST。
- **必须的请求头**：
  - `Content-Type: application/json`
  - `Accept: application/json, text/event-stream`
  - `MCP-Protocol-Version: <协商版本>`（值必须与 body `_meta` 的 protocolVersion 一致，
    否则 server 回 `400` + HeaderMismatch `-32020`）
  - `Mcp-Method: <method>`（所有请求）
  - `Mcp-Name: <params.name | params.uri>`（`tools/call` / `resources/read` / `prompts/get`；
    非 ASCII 不可安全表示时用 Base64 sentinel 格式）
- body `params._meta` 三键同 stdio（`protocolVersion`/`clientInfo`/`clientCapabilities`）。
- **无协议级 session**（2026-07-28 移除了 `Mcp-Session-Id`，也移除了 GET stream）。
- 响应：
  - `application/json`：单个 JSON-RPC response；
  - `text/event-stream`：请求作用域的 SSE，先 `notifications/*` 后终响应，终响应后关闭；
  - 通知 POST → `202 Accepted` 空体。
- server→client 交互走 MRTR（`InputRequiredResult`），**不在 SSE 上发独立请求**。
  收到 `input_required` → 同 stdio，报 `mcp_input_required`。
- 取消 = 关闭该请求的 SSE 流（不需要 `notifications/cancelled`）。
- `x-mcp-header`：server 可在 tool `inputSchema` 参数上标注；client **MUST** 把参数值镜像为
  `Mcp-Param-<name>` 头；非法标注的 tool **MUST** 从 `tools/list` 结果剔除。
  本波**可选严格执行**；若不做，必须在报告登记并确保至少不崩。
- 变更通知：`subscriptions/listen` 长连接 SSE（见 1.3）。

### 1.2 legacy HTTP 回退（≤2025-11-25）

- 首次用一个 modern 请求探测：
  - `400` + recognized modern error（`HeaderMismatch`/`UnsupportedProtocolVersion`/
    `MissingRequiredClientCapability`）→ modern，按 `supported` 重试；
  - `404` + JSON-RPC `-32601` → modern（方法不存在，不是回退信号）；
  - 其它 `4xx` 且无 recognized modern error body → **legacy**，回退 `initialize`。
- legacy HTTP：`initialize` 响应头带 `Mcp-Session-Id` ⇒ 后续请求回带；响应可能是
  `text/event-stream`，SSE 上**可以**出现 server→client 请求（旧语义）；可选 GET stream。
  本波只需支持"initialize + session id + 请求/响应"，GET stream 可不做（登记）。
- 风险：`sse` 类型（2024-11-05 HTTP+SSE）本组仍**跳过并 report**，不实现。

### 1.3 `subscriptions/listen`（modern 变更通知）

- POST `subscriptions/listen`，params 带通知过滤；响应是长连接 SSE。
- 先收 `notifications/subscriptions/acknowledged`；随后 `notifications/tools/list_changed` /
  `notifications/resources/updated` 等；`_meta` 带 `io.modelcontextprotocol/subscriptionId`。
- client 可为每个 server 起一个后台 listen task；断线重连需重新 listen。
- 本波**可只做 tools list_changed**（映射到 `McpRuntime.sync_tools`）；resources 变更通知可选。
- SSE keep-alive 注释行（`:` 开头）忽略；不支持 `Last-Event-ID`。

### 1.4 resources 工具

仅当连接的 server 声明 `resources` 能力时注册；exposure 取这些 server 中最宽者
（`direct` > `codemode`）：
- `list_mcp_resources(server?, cursor?)`
- `list_mcp_resource_templates(server?, cursor?)`
- `read_mcp_resource(server, uri)`：文本进 content，图片进 details，其它二进制落临时文件给路径。
- tag `{"mcp"}`；错误：新规范 `-32602`，旧 server `-32002` 也要接受。

### 1.5 配置

- 接受 `type: "streamable-http"` + `url` + `headers`。
- 插件目录文件严格按标准：`url` 绝对 HTTP(S)、非 loopback 必须 HTTPS、不得含 userinfo/fragment；
  header 名大小写敏感去重；**不得**对 url/header 名/header 值做占位符或环境变量展开。
- mocode 自有文件：允许 `${VAR}` 展开（`os.environ`）。
- `sse` 仍跳过并 report。

### 1.6 已冻结实现决策（lead 增补 2026-10-03）

lead 核实现状代码后拍板，worker 不再自行取舍：

| # | 决策 | 依据 |
|---|---|---|
| W3-D1 | `HttpSession` 放**新模块** `http.py`（同包）；`session.py` 保持 stdio 专用，不改其传输逻辑 | `session.py` 约 24KB 且全为 stdio 细节 |
| W3-D2 | session 的鸭子接口固化为包内共享 `McpSession` Protocol，`runtime.py` 里所有 `StdioSession` 注解替换为该类型；`_key_for` 的身份比较不变 | `runtime.py:31/67/88/133/145/151/154/160/192` 现写死 `StdioSession`，且无基类 |
| W3-D3 | `McpServerConfig` 增加 `transport: str`（`"stdio"`/`"streamable-http"`）与 `url: str \| None`、`headers: dict[str, str]` 字段，`command` 改可选；docstring 同步更新 | `config.py:84-114` 现 `command: str` 必填 |
| W3-D4 | `http` 与 `streamable-http` 均归一为 `"streamable-http"`；`_TRANSPORT_TYPES`（`config.py:72`）、`_HTTP_KEYS`（`config.py:66`）两个预留常量接进新解析路径，不留死常量 | W1a 预留钩子 |
| W3-D5 | `sse` 维持跳过 + report（D15 不变）；未知 type 维持 report + skip | 本组范围（legacy HTTP+SSE 不实现） |
| W3-D6 | legacy HTTP：`initialize` 响应头 `Mcp-Session-Id` 回显后续请求；GET stream 不做，登记遗留 | §1.2 |
| W3-D7 | listen task 由 `HttpSession` 内部起停，经既有 `on_tools_changed` 回调汇入 `McpRuntime.sync_tools`；断线按 bounded backoff 重新 listen，最终失败 report + `state=error`；同步 `shutdown()` 必须**非阻塞**取消（`McpRuntime.close` 走同步 teardown 路径） | `runtime.py:119-129`、`session.py` teardown 契约 |
| W3-D8 | `x-mcp-header`（`Mcp-Param-<name>` 镜像、非法标注剔除）本波**不严格**执行：不剔除工具、保证不崩；登记遗留并在 docs 写明 | 工单 §1.1 授权可选项 |
| W3-D9 | resources 工具仅在"已连接且声明 `resources` 能力"时注册；capabilities stdio 取自 `initialize` result，modern 取自 `server/discover` result（具体形态以 `ref/mcp-protocol.md` 为准）；exposure 取这些 server 中最宽者（`direct` > `codemode`，其余按 program 处理） | §1.4 |
| W3-D10 | 假 HTTP 端点统一模式：`ThreadingHTTPServer(("127.0.0.1", 0))`、`server_address` 取端口、daemon 线程 `serve_forever`、teardown `shutdown()` + `server_close()`；每个 await 用 `BOUND=15` 包裹（沿用 `tests/test_builtin_mcp.py` 的 BOUND 惯例） | 仓库无 HTTP 测试先例 |

**写范围窄化例外（本工单唯一新增授权）**：`tests/test_builtin_mcp.py` 中两个测试因 T1 必然翻转——

- `TestLoadServers.test_streamable_http_is_skipped_until_wave_w3`（现 :150，断言 `merged == {}` 且 stderr 含 `"stdio only"`）；
- `TestLoadServers.test_type_is_optional_in_mocode_files_and_inferred`（现 :164，断言 url 条目被跳过）。

允许改写为"解析成功"断言（详细 HTTP 解析用例移入 `tests/test_builtin_mcp_http.py`）；该文件**其余部分逐字节不变**，diff 中这两处之外不允许出现任何其它行。行号以合并基线为准。

## 2. 工单（每项一个 commit）

### T1 `HttpSession`（modern + legacy 回退）
- 实现 §1.1 / §1.2，与 `StdioSession` 同接口；单测用 stdlib `http.server` 起假端点：
  modern（JSON 与 SSE 两种响应、`MCP-Protocol-Version`/`Mcp-Method` 头断言、`input_required`）
  与 legacy（`initialize` + session id）。
- commit：`feat(mcp): add the streamable HTTP transport`

### T2 `subscriptions/listen`
- 后台 listen task + `sync_tools`；单测假 server 推 `tools/list_changed`。
- commit：`feat(mcp): subscribe to modern tool list changes`

### T3 resources 工具
- 实现 §1.4；单测假 server 返回 resources（文本/图片/二进制）。
- commit：`feat(mcp): expose server resources as read-only tools`

### T4 文档 + 测试收口
- `docs/plugins.md` 的 mcp 一节补 HTTP/headers/resources/订阅 与遗留（OAuth、`sse`、
  elicitation/MRTR、`x-mcp-header` 是否严格）；
  新增 `tests/test_builtin_mcp_http.py`。
- commit：`docs(mcp): document HTTP transport, resources and subscriptions`

## 3. 验收

1. `uv run pytest -q` 全绿，独立退出码 0，数量不降。
2. `uv run pytest tests/test_builtin_mcp_http.py -q` 全绿。
3. `git diff --stat` 只含 `builtin/mcp/**`、`tests/test_builtin_mcp_http.py`、`docs/plugins.md`，外加 `tests/test_builtin_mcp.py` 的窄化例外（仅 §1.6 所列两个测试可改）。
4. 无新依赖：`git diff pyproject.toml uv.lock` 为空。

## 4. 禁触清单

`mocode/core/**`；`mocode/host/**` 除 `builtin/mcp/**` 外的一切；`mocode/host/plugin/host.py`；
`README.md`；`docs/**` 中除 `plugins.md` 外的一切；其它测试文件（**唯一例外**：`tests/test_builtin_mcp.py` 仅允许按 §1.6 改写所列两个测试，文件其余部分逐字节不变）；spec 文件。

## 5. 最终报告格式

工单状态表｜commit 清单｜自测真实结论（命令 + 退出码）｜偏差与取舍（尤其 modern/legacy 判定、
SSE 解析、`x-mcp-header` 是否严格、OAuth/elicitation 遗留）｜未决问题。
