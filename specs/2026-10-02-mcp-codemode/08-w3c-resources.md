⏸️ 未实施 — 前置 W3a-2 合并后派工；**与 `07` 并行（写范围文件级不相交）**

# Spec 08 · W3c：resources 只读工具（波次 W3c）

> 分支 `feat/mcp-sdk-resources`；worktree 由 lead 建。
> 前置：W3a-2（`06`）已合并 master。
> 深读：`ref/mcp-protocol.md` §7.3、`ref/mocode-api.md`、`05` §1/§2（McpSession seam）、
> `01-w1-mcp.md` 的 exposure/工具构造节。
> 绝不操作主检出，绝不 merge/push/tag/checkout master。
> **并行约束**：本波只写 `builtin/mcp/tools.py`、`builtin/mcp/runtime.py`、
> 新增 `tests/test_builtin_mcp_resources.py`；`client.py`/`subscriptions.py`/
> 其它测试文件一概不碰。

## 0. 目标

已连接且 server 声明 `resources` 能力时，把该 server 的 resources 暴露为三个
只读工具。文本进 `ToolResult.content`，图片进 `details`，其它二进制落临时文件
给路径。**未声明资源的 server 不注册这些工具**。

## 1. 已核实的 SDK 事实（step-0 实测）

- `await client.list_resources(cursor=…)` → `.resources`（`uri/name/description/
  mimeType`，分页 `next_cursor`）；`await client.list_resource_templates()` →
  `.resource_templates`；`await client.read_resource(uri)` → `.contents`
  （`TextResourceContents.text` / `BlobResourceContents.blob` 等）。
- 能力来源：`client.server_capabilities.resources`（`None`/falsy = 未声明；
  modern/legacy 都可用）。W3a-1 的 `McpSession.server_capabilities` 已暴露。
- 错误：新规范 `-32602`、旧 server `-32002`（资源不存在）都出现；两者都经
  `MCPError.code` 到手。
- 若 `McpSession` 尚无 resources 直通方法（W3a-1/06 已按 D6 交付 wire 形薄封装，
  如实测核对），本波**不得改 `client.py`**——缺什么就在 `runtime.py`/`tools.py`
  内用现有 seam 组合，或停下来报告。

## 2. 冻结实现决策

| # | 决策 |
|---|---|
| W3c-D1 | 三工具（命名同 W3 ticket §1.4）：`list_mcp_resources(server?, cursor?)`、`list_mcp_resource_templates(server?, cursor?)`、`read_mcp_resource(server, uri)`；`server` 省略 = 唯一有资源的 server，多个则**必填**（缺参报错）；tag `{"mcp"}`；availability 按 exposure |
| W3c-D2 | 注册时机：`McpRuntime._on_connected`（或 `_apply_tools`）内，任一已连接 session 的 `server_capabilities.resources` 为真 → 注册这三个工具；全部资源 server 断开/无能力 → 注销。幂等（重复 connect 不重复注册） |
| W3c-D3 | exposure：取这些 server 中最宽者——任一 `direct` → `direct`；否则 `codemode`；其余按 program 处理（用 `naming.py` 现成 `canon_exposure`/`availability_for`，与 MCP 工具同管线） |
| W3c-D4 | `read_mcp_resource` 结果分流：text → content；`image/*` mime → `details["images"]`（与 `tools.py::_split_content` 同形）；其它带 `blob` 的二进制 → 写 `tempfile` 临时文件，content 给路径+大小+mime；`uri` 非本 server → ToolError（`mcp_error`） |
| W3c-D5 | list 分页：透传 `cursor`，`nextCursor` 原样回给模型（details 带 `nextCursor`）；`-32602`/`-32002` → `ToolError(..., "mcp_error")`，连接类错误沿用既有码 |

## 3. 工单（一个 commit）

### T1 resources 工具
- 按 §2 实现（`tools.py` 加构造器 + `runtime.py` 注册/注销时机）；测试
  `tests/test_builtin_mcp_resources.py`：进程内 `MCPServer` 挂 text/image/二进制
  三类 resource → 断言 content/details/临时文件路径三种分流；无 resources 能力
  的 server 不注册；exposure 映射；`-32002` 旧码路径。
- commit：`feat(mcp): expose server resources as read-only tools`

## 4. 验收

1. `uv run pytest -q` 全绿、退出码独立确认、数量 ≥ 891。
2. `uv run pytest tests/test_builtin_mcp_resources.py -q` 全绿。
3. `git diff --stat <W3a-2 合并点>` 只含 `builtin/mcp/tools.py`、`builtin/mcp/runtime.py`、`tests/test_builtin_mcp_resources.py`。
4. 无网络依赖；prompt section/`mcp_status` 不因本波变化（除工具列表自然增加）。

## 5. 禁触清单

`mocode/core/**`；`mocode/host/**` 除上述三个文件外的一切（尤其 `client.py`、`session.py`、`subscriptions.py`、`host.py`）；`README.md`；`docs/**`；`tests/test_builtin_mcp.py`、`tests/test_builtin_mcp_http.py`、`tests/test_builtin_mcp_subscriptions.py`（归 07）、`tests/test_builtin_mcp_codemode.py`；`pyproject.toml`/`uv.lock`；spec 文件。

## 6. 最终报告格式

同 `05`；取舍重点：server 参数省略规则、临时文件生命周期、注销时机、遗留
（resources 变更订阅、prompts、resource templates 展开）。
