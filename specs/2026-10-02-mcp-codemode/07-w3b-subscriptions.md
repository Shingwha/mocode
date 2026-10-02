✅ 2026-10-03 @ feat/mcp-sdk-subs（已合并 master `c425cbc`；925 passed exit 0，lead 合并前后各复核一次；遗留：resources 变更通知、多过滤器、listen 流经代理的长连接行为）

# Spec 07 · W3b：modern 工具变更订阅（subscriptions/listen）（波次 W3b）

> 分支 `feat/mcp-sdk-subs`；worktree 由 lead 建。
> 前置：W3a-2（`06`）已合并 master。
> 深读：官方 SDK 文档 `client/subscriptions/`（`https://py.sdk.modelcontextprotocol.io/client/subscriptions/`）、
> `ref/mcp-protocol.md` §7.4、`05` 工单 §1/§2（McpSession seam）。
> 绝不操作主检出，绝不 merge/push/tag/checkout master。
> **并行约束**：本波只写 `builtin/mcp/client.py`、新增 `builtin/mcp/subscriptions.py`、
> 新增 `tests/test_builtin_mcp_subscriptions.py`；`tools.py`/`runtime.py`/
> `tests/test_builtin_mcp.py`/`test_builtin_mcp_http.py` 归 `08` 或已合并波次所有，
> 一概不碰。

## 0. 目标

modern（`2026-07-28`）连接的 server 工具列表变更时，`McpRuntime.sync_tools()`
自动增量调和（W1a 已有 legacy 通知路径；本波补 modern 的
`subscriptions/listen` 长连接）。**只做 tools list_changed**（resources 变更
通知不做，登记遗留）。legacy 连接不起订阅（SDK 明确 `ListenNotSupportedError`
"pre-2026 连接，永不痊愈"）。

## 1. 已核实的 SDK 事实（官方文档 + step-0 旁证）

- `async with client.listen(tools_list_changed=True) as sub:`：进入即发
  `subscriptions/listen` 并等 server ack；`async for event in sub` 产出
  `ToolsListChanged()` / `ResourcesListChanged()` / `PromptsListChanged()` /
  `ResourceUpdated(uri=...)`。
- `SubscriptionLost`：优雅关闭结束 for 循环；断流抛 `SubscriptionLost`
  （未消费事件保留上限 1024）。两种都意味着流没了、无回放——需要的话重新 listen
  并 backoff（官方示例 `await anyio.sleep(1)`）。
- `ListenNotSupportedError`：pre-2026 连接，进 `listen()` 即抛，永不痊愈。
- 结束方式：退出 CM；或取消持有 CM 的 task（streamable HTTP 上 SDK 通过关闭该
  请求的流来取消）。多订阅按 subscription id 分用。
- keep-alive 由 SDK 处理；无需 `Last-Event-ID`。
- 注意 `anyio`：SDK 跑在 anyio 上，_sleep 用 `anyio.sleep`（或 asyncio 等价，
  二选一全组统一，报告选择）。

## 2. 冻结实现决策

| # | 决策 |
|---|---|
| W3b-D1 | 新模块 `subscriptions.py`：`async def watch_tools(client, on_changed: Callable[[], Awaitable[None]])`——进 `client.listen(tools_list_changed=True)`，`ToolsListChanged` → `await on_changed()`；`SubscriptionLost`/for 正常结束 → backoff 重听；`ListenNotSupportedError` → 抛给调用方一次性处理；`MCPError` → report 后抛出 |
| W3b-D2 | backoff：初值 1s、×2、上限 30s、成功事件后复位；重听无限次（会话存活即订阅），每次 `SubscriptionLost` 先 report 一次（限流：同一 session 每分钟最多一条 report） |
| W3b-D3 | `McpSession`：`connect_and_register()` 成功后，若 `protocol_version == "2026-07-28"` 且 `server_capabilities.tools` 非空 → 起后台 task 跑 `watch_tools(self._client, self._notify_tools_changed)`；`_notify_tools_changed` 调既有 `on_tools_changed(self)` 回调（汇入 `McpRuntime.sync_tools`，W1a 现成）；legacy 连接不起 task |
| W3b-D4 | `shutdown()`/`aclose()` 取消该 task（取消即退出 listen CM）；task 引用存 session，`status` 不暴露它 |
| W3b-D5 | 不动 `runtime.py`/`tools.py`（`sync_tools`/`on_tools_changed` 已存在）；SDK import 仍只在 `client.py`/`subscriptions.py` 内 |

## 3. 工单（一个 commit；删/改旧测试若有必须同 commit）

### T1 订阅
- 按 §2 实现；测试 `tests/test_builtin_mcp_subscriptions.py`：进程内 `MCPServer`
  （官方测试用法）推 `tools/list_changed` 后断言新工具被注册/旧工具被注销
  （经 `McpRuntime`/registry 断言，不断言内部）；legacy 连接断言
  `ListenNotSupportedError` 不起 task 且连接本身正常；`SubscriptionLost` 断流
  重听至少一轮（可用 hook/小延迟假 server 控制）。
- commit：`feat(mcp): subscribe to modern tool list changes`

## 4. 验收

1. `uv run pytest -q` 全绿、退出码独立确认、数量 ≥ 891。
2. `uv run pytest tests/test_builtin_mcp_subscriptions.py -q` 全绿。
3. `git diff --stat <W3a-2 合并点>` 只含 `builtin/mcp/client.py`、`builtin/mcp/subscriptions.py`、`tests/test_builtin_mcp_subscriptions.py`（外加 `builtin/mcp/__init__.py` 若需导出——仅导出，无逻辑）。
4. 无网络依赖；进程内测试为主、可加 stdio 子进程假 server。

## 5. 禁触清单

`mocode/core/**`；`mocode/host/**` 除上述三个文件外的一切（尤其 `runtime.py`、`tools.py`、`host.py`）；`README.md`；`docs/**`；`tests/test_builtin_mcp.py`、`tests/test_builtin_mcp_http.py`、`tests/test_builtin_mcp_codemode.py`、`tests/test_builtin_mcp_resources.py`（归 08）；`pyproject.toml`/`uv.lock`；spec 文件。

## 6. 最终报告格式

同 `05`；取舍重点：anyio vs asyncio sleep、report 限流、legacy 判定依据、遗留（resources 变更通知、多过滤器、重听上限）。
