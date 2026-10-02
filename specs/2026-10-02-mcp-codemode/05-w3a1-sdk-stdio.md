✅ 2026-10-03 @ feat/mcp-sdk-stdio（T1 done @ `45f4ca6`；lead 修订写范围后 T2 重派 —— W2 集成测试是重构消费者，见 §3/§5）

# Spec 05 · W3a-1：MCP stdio 客户端改用官方 SDK（波次 W3a-1）

> 分支 `feat/mcp-sdk-stdio`；worktree 由 lead 建。
> 前置：master @ `fda301f`（`build: depend on the official mcp sdk`，已含 `mcp==2.2.0`）。
> 深读：`ref/mcp-protocol.md`（语义）、`ref/mocode-api.md`（API 事实）、
> 本文 §2 的 SDK 核实事实（**以本文为准**，不信记忆）。
> 绝不操作主检出，绝不 merge/push/tag/checkout master。

## 0. 目标

把 `mcp` 内置插件的 **stdio 传输**从 W1a 手写的 `StdioSession` 换成官方 SDK 的
`Client`。**本波只动 stdio**：config 解析不变（`http`/`streamable-http`/`sse`
条目仍走 `config.py` 现有 report+skip 分支）；命名、exposure、runtime 注册调和、
prompt section、`mcp_status`、codemode 警告全部不变。

最高约束：对外行为零变化——不启用 MCP server 时一切与今天逐字节一致；启用 stdio
server 时工具名、availability、事件、错误码、超时行为与 W1a 交付保持一致。

## 1. 已核实的 SDK 事实（step-0 实测，2026-10-03；发现与此不符**以你实测为准并报告**）

- 包：`mcp==2.2.0`（`uv add mcp` 已落在 master）。`from mcp import Client`；
  `Client` 是 dataclass，字段：`server`（位置参数）+ keyword-only
  `raise_exceptions=False, read_timeout_seconds=None, mode="auto",
  prior_discover=None, input_required_max_rounds=10, client_info=None, ...`。
- **生命周期**：`async with Client(...) as client:` 即全部——进入时连接+协商，
  退出时断开；**不能复用、没有 `connect()/close()` 对**。属性 `client.session /
  protocol_version / server_info / server_capabilities / instructions` 仅在
  context 内可读。
- **协商**：`mode="auto"`（默认）先发 `server/discover` 探测，失败回退
  `initialize` 握手；`client.protocol_version` 报结果（`"2026-07-28"` 或
  `"2025-11-25"` 等）。实测：进程内 server 协商出 `2026-07-28`；一个真实托管
  streamable-HTTP server 协商出 `2025-11-25`（回退路径真实可用）。
- **stdio**：`from mcp.client.stdio import stdio_client, StdioServerParameters`；
  `StdioServerParameters(command: str, args: list[str] = [], env: dict|None =
  None, cwd: str|Path|None = None, encoding="utf-8", encoding_error_handler=
  "strict")`（pydantic BaseModel）。`stdio_client(server, errlog=sys.stderr)` 是
  async CM，yield `(read_stream, write_stream)`。
- **env 语义（关键差异）**：`env` 是"叠加在 `get_default_environment()` 之上"，
  而 **Windows 的默认集是裁剪过的**（仅 PATH/TEMP/USERPROFILE 等）。今天 mocode
  的行为是"全量进程环境 + 条目 env + PLUGIN_ROOT/PLUGIN_DATA"——**必须显式传
  `env={**os.environ, **entry_env, **plugin_tokens}`**，否则 Windows 上 server
  拿不到完整环境。这是行为保持的硬要求。
- **stdout/stderr**：stdout 走协议流；stderr 默认进 `errlog`。W1a 的
  `session.stderr_tail` 用于 status/错误报告——用一个带 `write/flush` 的环形缓冲
  对象作 `errlog` 保住尾迹（保留最后 N 行）。
- **进程终止**：POSIX `start_new_session=True` + SIGTERM→SIGKILL；Windows
  Job Object 硬杀 + `close_process_job` 收尸。
- **工具/结果**：`await client.list_tools()` → `result.tools`，每项 `.name /
  .description / .input_schema`（dict，JSON Schema）；分页 `cursor=` /
  `next_cursor`。`await client.call_tool(name, args)` → `CallToolResult`：
  `.content`（`TextContent/ImageContent/...` 块）、`.structured_content`、
  `.is_error`（工具报错是**结果**不是异常）。
- **input_required（MRTR）**：SDK 内建重试驱动（默认 `input_required_max_rounds=
  10`），轮数耗尽抛 `InputRequiredRoundsExceededError`；手工驱动用
  `client.session.<method>(..., allow_input_required=True)`。mocode 无交互 UI：
  设 `input_required_max_rounds=1`，把 `InputRequiredRoundsExceededError`（及驱动
  产生的 `MCPError`）映射为 `ToolError(..., "mcp_input_required")`——与 W1a 口径一致。
- **异常**：`from mcp.shared.exceptions import MCPError`（带 `.code` 等）。
- **依赖已知**：23 包随 SDK 装入（含 starlette/uvicorn/sse-starlette 等服务端栈）；
  `idna` 3.13→3.20；全量门禁 891 passed exit 0（lead 已实测）。

## 2. 冻结实现决策（不再自行取舍）

| # | 决策 |
|---|---|
| W3a-1-D1 | 新模块 `mocode/host/plugin/builtin/mcp/client.py` 放 `McpSession`；**删除** `session.py` 与 `rpc.py`（消费者归零；`__init__.py` 导出同步） |
| W3a-1-D2 | SDK import **只进 `client.py` 函数内或模块内惰性路径**，绝不进包 `__init__` 顶层链（`import mocode` <1ms 是硬不变量；`uv run python -X importtime -c "import mocode"` 验证无 `mcp`） |
| W3a-1-D3 | `McpSession` 保持 runtime 的既有调用面：async `connect_and_register() / ensure_connected() / list_tools() / call_tool() / close()`；同步 `shutdown()`（非阻塞：取消后台任务）；属性 `config/name/state/last_error/tools/instructions/era/protocol_version/server_info/stderr_tail` + 新增 `server_capabilities`；构造回调 `on_connected/on_tools_changed` 不变；session 永不直接碰 registry |
| W3a-1-D4 | `connect_and_register()` = 构造 target → `await Client(target, mode="auto", read_timeout_seconds=cfg.timeout or 60, input_required_max_rounds=1, client_info=mocode).__aenter__()`（存 CM 供 close 退栈）→ 读 `server_capabilities` → `await list_tools()` → `on_connected(session, tools)`。runtime 的 `connect_timeout_s` 用 `asyncio.wait_for` 包住它（同 W1a） |
| W3a-1-D5 | `era` 由 `protocol_version` 推导：`"2026-07-28"` → modern，其余 → legacy（`mcp_status`/prompt 只读展示） |
| W3a-1-D6 | `McpSession.list_tools()` 返回 **wire 形 dict**：`{"name": t.name, "description": t.description or "", "inputSchema": t.input_schema}`（键名与 W1a `tools.py` 期望一致，含 `inputSchema` 驼峰）；`call_tool()` 返回 `{"content": [块 dict...], "structuredContent": ..., "isError": bool}`（块 dict 用 SDK 模型的 `model_dump(by_alias=True)` 或等价，保 `mimeType`/`data` 驼峰），使 `tools.py::_split_content` 与错误映射**零改动或最小改动** |
| W3a-1-D7 | 错误映射（`tools.py::run`）：`MCPError` → `ToolError(str, code or "mcp_error")`；连接类失败（`OSError`/`ValueError`/CM 进入异常）→ 与 W1a 相同的 session `state=error`+`last_error`+`connect_timeout` 超时报 `mcp_timeout`；`CancelledError` 透传。**错误码集合不变**：`mcp_transport/mcp_timeout/mcp_input_required/mcp_closed/mcp_error` |
| W3a-1-D8 | stdio env 显式传 `{**os.environ, **条目 env}`，插件目录条目再注入 `PLUGIN_ROOT/PLUGIN_DATA`（W3a-1-D… §1 env 语义）；`cwd` 用 `cfg.cwd` |
| W3a-1-D9 | runtime：`start()` 构造 `McpSession`（当前 config 只产 stdio，无需分支；`06` 再按 `cfg.transport` 分派）；注解 `StdioSession` → `McpSession`；`shutdown()` 同步取消 + best-effort；新增 `async aclose()` 供 `plugin.close()` await（`McpPlugin.close` 已是 async——如实测；同步 `shutdown()` 若无调用方则删） |
| W3a-1-D10 | 测试 seam：**协议形态类测试用进程内 `MCPServer`**（`from mcp.server import MCPServer`，官方测试用法，实测 2026-07-28 + tools/call 可用）；**stdio 集成测试沿用 `tmp_path` + `sys.executable` 子进程假 server**（换行 JSON-RPC  wire 不变，`tests/_mcp_fake.py` 的脚本与新写的脚本都行）；`TestModernSession/TestLegacySession/TestEraNegotiation` 三个内部机制类**按新 seam 重写为结果断言**（协商出的 protocol_version、工具注册结果、错误映射、超时），不再断言内部探测细节 |

## 3. 工单（T1 原子 commit；T2 为 lead 修订写范围后的范围外消费者修复）

### T1 SDK stdio 客户端
- 按 §1/§2 实现；删 `session.py`/`rpc.py`；`tools.py` 按 D6/D7 对齐；`runtime.py` 按 D9；`__init__.py` 导出更新；`mcp/README.md`（插件目录内设计文档）同步更新。
- 测试：`tests/test_builtin_mcp.py` 的 session 三类重写（进程内 + 子进程假 server）；`TestSyncTools/TestToolMapping/TestExposureMapping/TestPluginLifecycle/TestEndToEnd` 与配置/命名类**尽量原样保留**（它们断言对外行为，正是行为保持的护栏）；`tests/test_builtin_mcp_codemode.py` 不动也应全绿。
- commit：`refactor(mcp): rebuild the stdio client on the official sdk`

### T2 W2 集成测试适配（lead 修订，2026-10-03）
- **背景**：`tests/test_builtin_mcp_codemode.py`（W2 交付）是重构的**范围外消费者**——它
  `import ...mcp.session`（D1 已删该模块）、依赖 `session._proc`（SDK 不暴露进程句柄）、
  其 ECHO fake 的 `tools/list` 缺 2026-07-28 强制的 `ttlMs`/`cacheScope`。三处不修则
  全量门禁收集期即红。worker 已验证修复补丁（896 passed, exit 0，未提交）。
- **做什么**：把已验证补丁落为本分支第二个 commit——仅限三处耦合：
  ① `_mcp_fake.py`：ECHO fake 的 `tools/list` 补 `ttlMs`/`cacheScope` + pidfile 辅助；
  ② `test_builtin_mcp_codemode.py`：`STATE_CLOSED` import 改 `...mcp.client`；
  ③ 同文件 `TestClose`：`session._proc` 观测改 pidfile + 平台感知存活探测
  （Windows `OpenProcess(SYNCHRONIZE)`，POSIX `os.kill(pid, 0)`）。
  **两文件其余部分逐字节不变。**
- commit：`test(mcp): rewire the codemode integration tests onto the sdk client`

## 4. 验收（完成前自测，报告给真实结论）

1. `uv run pytest -q` 全绿、退出码独立确认（`; echo $?`，禁管道吞）、数量 ≥ 891。
2. `uv run pytest tests/test_builtin_mcp.py tests/test_builtin_mcp_codemode.py -q` 全绿。
3. `uv run python -X importtime -c "import mocode"` 输出无 `mcp`；`uv run python -c "import time, mocode"` 计时 <1ms（连续 3 次）。
4. `git diff --stat e778b2d` 只含 `mocode/host/plugin/builtin/mcp/**`、`tests/test_builtin_mcp.py`，外加 T2 授权的 `tests/_mcp_fake.py`、`tests/test_builtin_mcp_codemode.py`（仅三处耦合）；`git diff e778b2d -- mocode/core mocode/host/plugin/host.py pyproject.toml uv.lock README.md docs specs` 为空。
5. Windows 平台分支处按 `sys.platform` 处理（SDK 已内建 Job Object 语义；你的测试只断言"直接子进程被终止"这类跨平台事实）。

## 5. 禁触清单

`mocode/core/**`；`mocode/host/**` 除 `builtin/mcp/**` 外的一切（含 `host.py`）；`README.md`；`docs/**`；其它测试文件（**唯一例外**：`tests/_mcp_fake.py` 与 `tests/test_builtin_mcp_codemode.py` 仅允许 T2 所列三处耦合修改，其余部分逐字节不变）；`pyproject.toml`/`uv.lock`（依赖已由 lead 落在 master）；spec 文件。

## 6. 最终报告格式

工单状态表｜commit 清单（hash+message）｜自测真实结论（命令+退出码+关键输出）｜偏差与取舍（尤其：与 §1/§2 的出入及实测依据、`input_required` 映射的实测行为、stderr_tail 方案、测试重写的取舍）｜未决问题。遇阻塞报告后停止，不越界自救。
