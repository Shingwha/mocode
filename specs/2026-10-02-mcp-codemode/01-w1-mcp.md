# Spec 01 · W1a：`mcp` 内置插件（stdio）（波次 W1a）

> 分支 `feat/mcp-stdio`；worktree `C:\Users\shifu\.worktrees\mocode\feat-mcp-stdio`。
> 前置：无（W0 基线）。深读：`00-overview.md` + `ref/mocode-api.md` + `ref/mcp-protocol.md`
> + `ref/pi-mcp.md` + `ref/agent-plugins-mcp-json.md`。
> **协议基线：MCP 2026-07-28（modern）+ ≤2025-11-25（legacy），实现必须 dual-era。**
> 你只读本 worktree 内文件；绝不操作主检出，绝不 merge/push/tag。

## 0. 现状事实（已核实，2026-10-02）

- `mocode/host/plugin/builtin/` 下现有 `cache_protect.py`、`default_prompts.py`、`effort.py`、
  `filesystem.py`、`help.py`、`session.py`、`skills.py`、`shell/`（包）。`builtin/__init__.py`
  是空壳（只 import `__future__`），**本波禁触**。
- `builtin/shell/plugin.py` 是 per-conversation 资源的范例：`build()` 创建 session 并注册工具，
  **把 session 挂在 Tool 上**（`tool.session = session`，见 `builtin/shell/tool.py` 末尾），
  `close(ctx)` 通过 `ctx.tools.get("bash").session` 找回并关停。**本插件照抄这个模式**。
- `Plugin` 协议：`build(ctx: BuildContext) -> None`（同步、只注册）、
  `async prepare(ctx: HostContext) -> None`（I/O）、`close(ctx: HostContext) -> None`
  （`mocode/host/plugin/base.py:38/46/56`）。
- `Tool` 构造（`mocode/core/tool.py:282`）：`name, description, schema, func, *, tags,
  summary_key, result_key, with_context, availability, policy, source`。
  `availability ∈ {"model","program","both"}`（`:293`）。`registry.register(tool)` 会把 `source`
  盖成 `builtin:mcp`（`host/plugin/context.py:56` 的 `_StampingToolRegistry`）。
- `ToolRegistry.names(audience="model"|"program")`（`core/tool.py:475`）决定可见性投影；
  `disable(name)` 对两个 audience 都不可见且拒跑。
- `ToolError(message, code="execution_error")`（`core/tool.py:20`）→ dispatcher 转成
  `status="error"` + `error:<code>: <msg>`。
- `Notice`（`core/events.py`）经 `await ctx.emit(Notice(message=..., level="warn"))` 发布
  （`host/plugin/context.py:171`）。
- `Section(name, content=None, *, priority=0, enabled=True, attrs={}, render=None,
  pinned=False, derived_from=None)`（`core/prompt.py`）；`render` 收 builder context dict。
- `ctx.plugin_sources: list[Path]`（`host/plugin/context.py:89`）是项目插件目录，loader 从不解析
  `mcp.json`（`host/plugin/loader.py:8` 注释明说 "recognised, not served yet"）。
- `ctx.plugin_config("mcp")` 返回 config.json 的 `plugins.mcp` dict（`:125`）。
- `ctx.home`（`~/.mocode`）、`ctx.cwd`（项目工作目录）均在 BuildContext。

## 1. 目标

实现 `mocode/host/plugin/builtin/mcp/` 包：连接 MCP server（**v1 仅 stdio**），把其工具注册为
mocode `Tool`，按 exposure 决定模型可见性；连接失败隔离、不阻塞首 turn、对话结束关停子进程。

## 2. 冻结的对外契约

### 2.1 文件布局（唯一允许的新增路径）

```
mocode/host/plugin/builtin/mcp/
├── __init__.py      # 只导出 PLUGIN、McpPlugin、McpRuntime（供测试）；无副作用
├── plugin.py        # McpPlugin：build/prepare/close
├── config.py        # mcp.json + 配置合并、校验；严格(插件)/扩展(mocode) 两套规则
├── naming.py        # 名称归一/去重、exposure 解析
├── rpc.py           # JSON-RPC 编解码（newline framing）
├── session.py       # StdioSession：单 server 连接
├── runtime.py       # McpRuntime：多 session、注册/注销工具、状态
└── tools.py         # mcp_tool(...)、mcp_status_tool(runtime)
```

`tests/test_builtin_mcp.py`（新增，唯一测试文件）。

### 2.2 配置来源与优先级（`config.py`）

合并顺序，**从高到低**；同名 server 高优先级整条覆盖低优先级：

1. `ctx.plugin_config("mcp").get("servers", {})`（config.json 内联）
2. `<cwd>/.mocode/mcp.json`
3. `<home>/mcp.json`（`ctx.home` = `~/.mocode`）
4. 各 `ctx.plugin_sources` 目录下的 `mcp.json`（数组顺序，前者优先）

**两套解析规则，必须分开**（见 `ref/agent-plugins-mcp-json.md`）：

**A. 插件目录 `mcp.json`（来源 4）＝ 严格按 Agent Plugins 1.0.0 标准**
- 顶层只能是 `$schema` + `mcpServers`；`$schema` 必须存在且与目标版本一致，否则该插件的
  MCP 配置**整体跳过**（不影响其它插件/组件）。
- server 条目字段仅允许标准集合：stdio = `{type, command, args, env, cwd}`；
  http = `{type, url, headers}`。单条不合法 ⇒ 跳过该条。
- `command` 是**单个可执行 token**（裸名或 `./` 插件相对路径），**不得**做占位符展开。
- `args` / `env` / `cwd` 只支持 `${PLUGIN_ROOT}` / `${PLUGIN_DATA}` 展开（单次、非递归）。
- 子进程环境必须提供 `PLUGIN_ROOT`（插件目录绝对路径）与 `PLUGIN_DATA`
  （`<plugin>/.mocode-data`，缺则创建、可写）；`env` 不得含这两个键。
- `cwd` 展开后必须落在对应根内（`${PLUGIN_DATA}`-rooted 落在 data 目录内），否则跳过该条。
- 标准之外的 pi 扩展键（`exposure` 等）：**宽容读取，未知键 report 后忽略，不拒绝整条**。

**B. mocode 自有文件（来源 1–3）＝ 允许扩展与 `${VAR}`**
- 额外支持 `enabled`（false ⇒ 保留不连接）、`timeout`（per-request 秒，默认 60）、`exposure` /
  `toolExposure`、`description`。
- `${VAR}` → `os.environ`（缺失 → 空串 + report）；**v1 不做 `!command`**。
- 相对 `cwd` 按该配置文件所在目录解析；`${PLUGIN_ROOT}`/`${PLUGIN_DATA}` 无意义 ⇒ 跳过该条并 report。

下面这个例子是 **mocode 自有文件**（带 `enabled`/`exposure` 等扩展）；插件目录的 `mcp.json`
只允许标准字段：

```jsonc
{
  "$schema": "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json",
  "mcpServers": {
    "demo": {
      "type": "stdio",                 // 有 command ⇒ stdio；有 url ⇒ streamable-http
      "command": "uvx",
      "args": ["demo-mcp"],
      "env": {"TOKEN": "${DEMO_TOKEN}"},
      "cwd": "./data",
      "enabled": true,                 // false ⇒ 保留但不连接
      "timeout": 60,                   // per-request 秒，默认 60
      "exposure": "codemode",          // direct|codemode|deferred|hidden；缺省见 2.4
      "toolExposure": {"search_*": "direct", "delete_*": "hidden"},
      "description": "一句话，进 mcp_servers section"
    }
  }
}
```

- 只接受 `mcpServers` 为 **object**；非 object / 缺字段的单条 → 跳过并 `report()`
  （可直接 `from ...loader import report`），**不阻止其它 server**。
- **v1 不实现**：`!command`、OAuth、legacy `type:"sse"`（跳过并 report，建议改用 `/mcp`）、
  streamable HTTP（见 W3）。
- 配置校验失败的单条 → 跳过；整文件 JSON 损坏 → report + 跳过该文件。

### 2.3 名称归一（`naming.py`）

- `normalize(name) = "".join(c if c.isalnum() or c == "_" else "_" for c in name)`。
- server 名比较：把 `-`/`_` 都折叠为 `_` 后比较；**视为同名**，第二个被丢弃并 report。
- 工具全名 = `f"mcp__{normalize(server)}__{normalize(tool)}"`。
- 同一 server 内两个原始工具名归一到同一全名时：后者加 `_<sha1(原始名)[:6]>` 后缀，
  并在 `tool.mcp_raw_name` 保留原始名。全名同 server 内稳定（排序后按原始名 hash，不用全局计数器）。

### 2.4 exposure → availability 映射（`naming.py`）

`default_exposure` 取 `ctx.plugin_config("mcp").get("default_exposure", "auto")`：

- `"auto"`：若 `ctx.config.plugins.get("codemode", {}).get("enabled") is True` → `"codemode"`，
  否则 `"direct"`。
- 其它取值直接作为默认。

单 server 的 `exposure` 覆盖默认；`toolExposure` 再按工具覆盖（**精确名优先于 pattern**；
pattern 里 `*` 匹配任意字符；多条 pattern 取第一条匹配；只有 `*` 通配，无正则）。
`"codemode-deferred"` 是 `"codemode"` 的别名。

| 有效 exposure | availability | 备注 |
|---|---|---|
| `direct` | `"both"` | 模型直连可见 |
| `codemode` | `"program"` | 仅脚本可调 |
| `deferred` | `"program"` | v1 等同 codemode（无 tool_search） |
| `hidden` | 先注册再 `tools.disable(full_name)` | 两 audience 都不可达且拒跑 |

### 2.5 stdio 传输 + protocol era 探测（`rpc.py` + `session.py`）

**framing**：MCP stdio 是 **newline-delimited JSON**：每条消息一个 JSON object + `"\n"`，
UTF-8，消息内不得含裸换行（`json.dumps(..., ensure_ascii=False)` 后不会有换行）。

**必须实现 dual-era**（当前规范 2026-07-28 是 modern；线上大量 server 仍是 legacy）。
细化见 `ref/mcp-protocol.md` §4–§6。

**生命周期**：
1. `spawn`：`asyncio.create_subprocess_exec(command, *args, cwd=..., env={**base, **env,
   "PLUGIN_ROOT":..., "PLUGIN_DATA":...}, stdin=PIPE, stdout=PIPE, stderr=PIPE)`。**不用 shell**。
   （mocode 自有文件无 PLUGIN_ROOT/PLUGIN_DATA 需求，按省略处理。）
2. 读循环：后台 task 按行读 stdout、解析 JSON；有 `id` → 交给对应 future；无 `id` 的
   `notifications/tools/list_changed` → 触发重列（legacy）；其它通知忽略。
3. **era 探测（一次，结果缓存于 session）**：发 `server/discover`，`params._meta` =
   `{protocolVersion:"2026-07-28", clientInfo:{name:"mocode",version:"0.4.0"},
   clientCapabilities:{}}`，探测超时 5s：
   - 返回 `DiscoverResult` 且 `supportedVersions` 含 `"2026-07-28"` → **modern**，
     `protocol_version="2026-07-28"`；
   - JSON-RPC error code ∈ `{-32020,-32021,-32022}` → **modern**；从 `data.supported` 选一个
     ≥`2026-07-28` 的版本重试 discover；没有 → 回退 legacy；
   - 其它 error / 超时 / 无响应 → **legacy**。
   **不得只按某个错误码判定 legacy**（标准明文）。
4. **modern 请求**：每个请求给 `params` 注入 `_meta`（三键，见 ref §6）；结果 `resultType`
   缺省按 `"complete"` 处理；`"input_required"` →
   `McpError("server requires user input; elicitation is not supported")`（mocode v1 无交互 UI）。
5. **legacy 握手**：`initialize`（`protocolVersion="2025-11-25"`）→ result（顶层
   `serverInfo`/`instructions`）→ 发 `notifications/initialized`；之后请求**不带** `_meta`，
   结果无 `resultType`。
6. `tools/list`：`params` 可带 `cursor`；循环取页直到无 `nextCursor`；忽略 `icons`/未知字段。
7. `tools/call`：`params={"name": <原始工具名>, "arguments": <dict>}`。
8. stderr：单独 task 消费，存环形尾部（保留最后 ~50 行）供错误报告；**不**写入 channel。
9. `close`：关 stdin → 等待 ~1s → `terminate()` → 等待 ~2s → `kill()`。
   POSIX 用 `start_new_session=True` 起进程组并对 `-pid` 终止（照抄 `builtin/shell/session.py`
   的 `_terminate` 思路）；Windows 只杀直接子进程。
10. 请求超时：`asyncio.wait_for(future, timeout=server.timeout)`；超时 → 取消并记错误。
11. 连接断开：读循环退出 → 所有 pending future 以 `McpError("server disconnected")` 结束；
    `ensure_connected()` 在下次调用时重连一次（era 缓存保留）。
12. `list_changed`：**legacy** 由通知触发重列；**modern v1 不订阅**（`subscriptions/listen`
    见 W3），工具列表在连接时取一次。登记遗留。

**错误**：JSON-RPC error `{code, message, data}` → `McpError(f"{message} (code {code})")`。
transport 解析失败 → report + 丢弃该行，不断连接。

### 2.6 工具映射（`tools.py`）

`mcp_tool(runtime, session, server_name, raw_tool, availability, disabled) -> Tool`：

- `name` = 2.3 的全名；`description` = MCP 的 `description` 或 `f"MCP tool {raw} from {server}"`。
- `schema` = MCP `inputSchema`（已是 JSON Schema object node），**原样透传**；缺失时回退
  `{"type":"object","properties":{}}`。
- `func` 是 **async**、`with_context=True`：`async def run(args, ctx)`：
  - 调 `session.call_tool(raw_name, args)`；
  - 成功 → `ToolResult(content=<拼接的文本>, details={"server","tool","content","structured_content","is_error":False})`；
  - `isError` → `raise ToolError(text, "mcp_error")`；
  - `resultType == "input_required"` → `raise ToolError("server requires user input; "
    "elicitation is not supported", "mcp_input_required")`；
  - transport/协议错误 → `raise ToolError(str(e), "mcp_transport")`。
- `tags = frozenset({"mcp", f"mcp:{normalize(server)}"})`。
- `summary_key` 用 schema 第一个 required（由 `Tool` 自动推断即可，不必显式传）。
- 在 Tool 上挂：`tool.mcp = {"server": server_name, "tool": raw_name}`。
- `availability` 由 2.4 决定；`hidden` 的注册后再 disable。
- **图片/非文本 content block**：文本块拼进 `content`；`type=="image"` 块进
  `details["images"]`（原样 block）；其它类型 JSON 进 `details["content"]`。content 文本里为图片
  放一行 `[image: <mimeType>]` 占位。

### 2.7 `mcp_status` 锚点工具（`tools.py`）

`build()` 无条件注册一个 `Tool(name="mcp_status", availability="program", ...)`，其闭包持有
`McpRuntime`，并在实例上挂 `tool.mcp_runtime = runtime`。作用：

1. `close(ctx)` 通过 `ctx.tools.get("mcp_status").mcp_runtime` 找回 runtime 并关停
   （复制 shell 的"registry 即句柄"模式，插件实例保持无状态）；
2. 供 codemode 脚本 `await tools.mcp_status()` 查看 server 状态（dict 列表）。

schema：`{"type":"object","properties":{}}`；返回 `ToolResult(content=<人类可读状态>,
details={"servers":[{"name","state","tools","error"}]})`。

### 2.8 `mcp_servers` prompt section（`plugin.py`）

`prepare()` 结束后 `ctx.prompt_sections.append(Section("mcp_servers", render=..., priority=46,
derived_from="tools"))`：列出每个**非 hidden** server 的 namespace、可达方式
（`direct` / `codemode`）与 `description`。无 server 时不渲染（`render` 返回 `""`）。
`render` 闭包读 runtime 当前状态，**非 pinned**（首次 materialize 时渲染一次）。

### 2.9 生命周期（`plugin.py`）

```python
class McpPlugin(Plugin):
    name = "mcp"
    description = "Connect to MCP servers and expose their tools"

    def build(self, ctx: BuildContext) -> None:
        runtime = McpRuntime(ctx)                 # 解析配置；不连接、不注册 server 工具
        ctx.tools.register(mcp_status_tool(runtime))

    async def prepare(self, ctx: HostContext) -> None:
        runtime = ctx.tools.get("mcp_status").mcp_runtime
        await runtime.start()                     # 见下
        ctx.prompt_sections.append(...)

    def close(self, ctx: HostContext) -> None:
        status = ctx.tools.get("mcp_status")
        runtime = getattr(status, "mcp_runtime", None)
        if runtime is not None:
            runtime.shutdown()                    # 同步：terminate/kill，不等 I/O
```

`McpRuntime.start()`：

- 对每个 enabled server 建 `StdioSession` 并注册为后台连接 task。
- 对 `direct` server：`await asyncio.wait_for(session.connect_and_register(), timeout=connect_timeout_s)`
  （默认 10s），逐个等待；失败 → report + 该 server 标记 `error`，**不 raise**。
- 其余 server：`asyncio.create_task(...)` 后台连接；task 存进 runtime，`shutdown()` 时 cancel。
- 后台 task 连上后调 `sync_tools`；`sync_tools` 里对 late 注册的工具**照常 register**
  （frozen payload 不含它，但 program audience 可见；cache-protect 会提示；这是可接受的）。
- 全部完成后：若有 `codemode`/`deferred` exposure 的工具而 `plugins.codemode.enabled` 不为真，
  `await ctx.emit(Notice("N MCP tools are reachable only through codemode, which is disabled "
  "(set plugins.codemode.enabled); run mocode with the codemode plugin to use them.", level="warn"))`
  ——**每 conversation 至多一次**。

`connect_timeout_s` / `request_timeout_s` 取自 `plugins.mcp` 配置，缺省 10 / 60。

## 3. 工单（每项一个 commit）

### T1 `config.py`：合并 + 校验 + `${VAR}`
- 实现 2.2 全部规则；数据集 `McpServerConfig`（dataclass，字段见 2.2）。
- 单测覆盖：优先级覆盖、同名整条覆盖、坏条目跳过、`${VAR}` 展开、`cwd` 包含性、`sse` reject。
- commit：`feat(mcp): load and merge MCP server configuration`

### T2 `naming.py`：名称与 exposure
- 实现 2.3 / 2.4，含 `toolExposure` 精确名 > pattern、`*` 匹配、`codemode-deferred` 别名、
  auto 判定读 `ctx.config.plugins["codemode"]["enabled"]`。
- 单测逐条（含 `mcp__dev-radius__search` 类名字、冲突 hash 稳定）。
- commit：`feat(mcp): normalize server and tool names, resolve exposure`

### T3 `rpc.py` + `session.py`：stdio 连接 + dual-era 探测
- 实现 2.5。`StdioSession` 公开：`async connect()`（含 era 探测）、
  `async list_tools() -> list[dict]`、`async call_tool(name, args) -> dict`、`async close()`、
  `era`、`protocol_version`、`server_info`、`instructions`、`state`、`last_error`、`stderr_tail`。
- 单测：用 `sys.executable` 起两个假 server 脚本（写入 `tmp_path`）：
  - **modern**：应答 `server/discover`（返回 supportedVersions 含 2026-07-28）、`tools/list`
    （带 `resultType`、分页）、`tools/call`（含一个 `isError`、一个 `input_required`）；
  - **legacy**：应答 `initialize` + `notifications/initialized` + `tools/list`/`tools/call`
    （无 `resultType`）。
  断言：era 判定正确、分页、调用映射、`isError`、`input_required` 报错、超时、断开、关停。
- commit：`feat(mcp): add dual-era stdio JSON-RPC session`

### T4 `runtime.py` + `tools.py`：注册与同步
- `McpRuntime`：持有 sessions、`sync_tools`（register/unregister/enable/disable）、`status()`。
- `mcp_tool` / `mcp_status_tool` 按 2.6 / 2.7。
- 单测：tool 名称/availability/details/错误映射、`hidden` 被 disable、list_changed 增删。
- commit：`feat(mcp): register server tools into the tool registry`

### T5 `plugin.py`：生命周期 + section + warning
- 按 2.8 / 2.9。direct 有界等待、其余后台、warning 至多一次、close 关停。
- commit：`feat(mcp): wire the plugin lifecycle and prompt section`

### T6 `tests/test_builtin_mcp.py` 收口 + README（插件目录内）
- 端到端：`plugin_host(plugins=[PLUGIN])` 起假 server，断言工具在 registry、模型
  audience 按 exposure 可见、`conversation.prepare()` 后 `mcp_servers` section 出现、close 后子进程退出。
- commit：`test(mcp): cover the plugin end to end`

## 4. 验收（完成前自测，报告真实结论）

1. `uv run pytest -q` 全绿，独立确认退出码 0，数量 **≥ 700**。
2. `uv run pytest tests/test_builtin_mcp.py -q` 全绿（独立退出码 0）。
3. `uv run python -c "from mocode.host.plugin.builtin.mcp import PLUGIN; print(PLUGIN.name)"`
   真实输出 `mcp`。
4. `grep -rn "mcp" mocode/core/` 无命中（core 零 diff）。
5. `git diff --stat` 只含 `builtin/mcp/**` 与 `tests/test_builtin_mcp.py`。
6. Windows 上假 server 子进程测试不挂死（若平台差异，按 `sys.platform` 分支并在报告说明）。

## 5. 禁触清单

- `mocode/core/**`（含 `dispatch.py`、`tool.py`、`events.py`、`hook.py`、`agent.py`）。
- `mocode/host/**` 中除 `builtin/mcp/**` 外的一切，尤其 `host.py`、`context.py`、`loader.py`、
  `runtime.py`、`config.py`、其余 builtin。
- `README.md`、`docs/**`、`AGENTS.md`、`pyproject.toml`（W2 负责）。
- 其它测试文件（只许新增 `tests/test_builtin_mcp.py`）。
- `mocode/host/plugin/builtin/codemode/**`（W1b 的领地）。
- spec 文件（归 lead）。

## 6. 最终报告格式

工单状态表｜commit 清单（hash+message）｜自测真实结论（命令 + 退出码）｜
偏差与取舍（尤其：协议版本、重连策略、Windows 进程终止差异）｜未决问题。
遇阻塞：报告后停止，不越界自救。
