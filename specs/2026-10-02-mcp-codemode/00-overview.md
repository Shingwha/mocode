✅ 2026-10-03 @ master（W1a/W1b/W2/W3a-1 已合并，896 passed；W3a-2（`06`）已派工，W3b/W3c（`07`/`08`）待 W3a-2 合并后并行）

# Spec Group · MCP + Codemode 两个内置插件（2026-10-02）

> Group: `specs/2026-10-02-mcp-codemode/`
> Baseline (master @ W0): **700 passed, 0 failed, 0 skipped, ~25s, exit 0**（2026-10-02 lead 实测
> `uv run pytest -q; echo $?` → `700 passed, 4 warnings`，退出码 0；4 条 warning 是既有的
> `PytestUnraisableExceptionWarning: I/O operation on closed pipe`（Windows asyncio 子进程
> pipe 析构噪声），**不是** 门禁失败，本组不得以"修 warning"为名扩大改动）。
> worker 不改 spec、不写状态；完成信号 = 最终报告 + 分支 commit。git 是唯一进度权威。

---

## 1. 目标

把 pi 的两项能力做成 **mocode 的两个独立 builtin 插件**，只使用**当前**代码已有的扩展点，
**不改 core/**：

1. **`mcp`（MCP 客户端插件）**：读取 `mcp.json` / 配置，连接 MCP server（v1 只做 stdio），
   把其工具以 `mcp__<server>__<tool>` 注册进 `ToolRegistry`，按 exposure 映射
   `Tool.availability` / disabled。
2. **`codemode`（脚本编排插件）**：注册一个 `codemode` 工具，模型写 **Python 脚本**，
   脚本通过 `ToolDispatcher.run(..., origin="program")` 并行调用任意工具、过滤大结果，
   只有脚本输出回到模型。

**最高约束（放第一句）**：对外行为零变化 —— 现有 700 个测试全绿；不启用新插件时，
系统 prompt、工具列表、事件流、session 行为与今天逐字节一致。

## 2. 范围 / 非目标

**In scope**
- 两个 builtin 插件包 + 各自测试 + 集成测试。
- `builtin_plugins()` 注册（W2）、保留名、README/docs 更新（W2）。
- MCP stdio 传输 + **dual-era 协议**（modern 2026-07-28 的 `server/discover`+`_meta`，
  legacy ≤2025-11-25 的 `initialize` 握手）、`tools/list`、`tools/call`、关停。
- exposure → availability 的完整映射（`direct`/`codemode`/`deferred`/`hidden` + per-tool `toolExposure`）。
- Python codemode DSL：`tools.x()`、`text/console/image/exit`、`store/load`、`ALL_TOOLS`、
  `search_tools`、`describe_tool`、输出截断、失败语义、递归保护。

**Out of scope（明确不做，记入遗留）**
- `mocode/core/**` 的任何改动。
- `tool_search` 插件（pi 的 `deferred` 机制）；v1 把 `deferred` 当 `codemode`（program）处理。
- `models`（classifier / image 非 chat 模型目录）——pi 有、mocode 无，codemode v1 不注入。
- `describe_namespace()`（需要跨插件 server 描述通道；v1 用 `ALL_TOOLS` + `describe_tool` 代替）。
- `mode="only"`（把已声明工具对模型隐藏）——v1 只实现 pi 的 `on` 形态。
- **MRTR / elicitation**（modern 的 `InputRequiredResult`）：mocode v1 无交互 UI，收到即报错。
- **modern `subscriptions/listen`**：W1 不做，W3 可选；modern server 的工具列表在连接时取一次。
- OAuth、MCP prompts、sampling、`!command` 展开；resources **变更**通知（tools 变更通知已做，见 W3b）；`tool_search` 插件。
- ~~legacy `type:"sse"` 传输~~ → **已入范围**（W3a-2，SDK `sse_client`）。
- streamable HTTP / SSE 传输 → **已入范围**（W3a-2，原 `04` 手写设计作废，见 §9）。
- MCP resources 只读工具 → **已入范围**（W3c）。

## 3. 已拍板决策（不再反复）

| # | 决策 | 理由 |
|---|---|---|
| D1 | **两个独立插件**，不是一个 | codemode 依赖 dispatcher/registry（通用、可编排任何工具）；MCP 不依赖 codemode，只标 availability。生命周期不同：MCP 有 I/O + 子进程 + `close()`，codemode 近乎无状态。可独立启停。对应 pi 的 `builtin:mcp` / `builtin:codemode`。 |
| D2 | MCP 默认 exposure = **`auto`**：codemode 插件启用则 `codemode`，否则 `direct` | 既不默认把几十个工具塞给模型，也不让默认配置下的 MCP 工具不可达。见 `01` 工单的 `plugins.mcp.default_exposure`。 |
| D3 | codemode 沙箱 = **进程内 `exec` + restricted `__builtins__`** | 模型本来就有 bash，安全边界不是目标；目标是稳定 DSL + 资源约束。**文档必须写明"不是安全沙箱"**。子进程隔离记为遗留。 |
| D4 | `tools.x()` 非 ok **抛异常** | pi 语义；`asyncio.gather(..., return_exceptions=True)` 天然可用。异常带 `DispatchResult`。 |
| D5 | codemode **只做 `on` 形态** | `only` 需要改别的插件的 `availability`，侵犯"一个插件一个关注点"；记为遗留。 |
| D6 | ~~**不新增任何 runtime 依赖**~~ **SUPERSEDED 2026-10-03**：用户拍板改用官方 `mcp` SDK（v2，`mcp==2.2.0`，lead 落 master `fda301f`）；stdlib 手写方案作废 | SDK 覆盖双 era 协商、三种传输、resources、订阅；自研成本与一致性风险高于依赖重量 |
| D7 | MCP server 名/工具名规范：非 `[A-Za-z0-9_]` → `_`；仅 `-`/`_` 不同的 server 名视为同名；`mcp__<server>__<tool>` 冲突加 6 位 hash 后缀 | 对齐 pi 的命名规则；名字可预测、可去重。 |
| D8 | 每个 MCP 工具用 `ToolError` 表达 `isError`；成功时 `ToolResult.details` 带 `structured_content` / `content` / `server` / `tool` | 走 dispatcher 标准错误管线；details 不进 messages。 |
| D9 | MCP 在 `prepare()` 里连接；**只对 `direct` server 有界等待**（默认 10s），其余后台连接 | 首 turn 不被慢 server 卡住；program-only 工具不改模型 payload，迟到注册无 cache 代价。 |
| D10 | codemode 的输出截断保留 head+tail，全文落临时文件并在结果里给路径 | 对齐 pi 的 `max_output_tokens` 行为。 |
| D11 | `store` 只在脚本成功（正常结束或 `exit()`）时提交 | 对齐 pi；失败脚本不留半截状态。 |
| D12 | codemode 描述静态；MCP 用 `mcp_servers` prompt section 暴露 namespace | codemode 的 tool description 在 `build()` 定型；MCP 工具在 `prepare()` 才发现，动态信息只能进 section / registry。 |
| D13 | ~~MCP 客户端是 **dual-era**：手写 `server/discover` 探测~~ **SUPERSEDED 2026-10-03**：改由 SDK `Client(mode="auto")` 承载（discover 探测 + initialize 回退，实测真实 legacy server 走通） | 语义等价且是官方实现；mocode 只做 exposure/注册/错误码映射 |
| D14 | 插件目录 `mcp.json` **严格按 Agent Plugins 1.0.0 解析**；项目/用户级 mocode 配置才允许 `exposure`/`${VAR}` 等扩展 | 标准路径是别家客户端也会读的可移植数据；mocode 自有配置才是 mocode 的语法。 |
| D15 | ~~legacy `type:"sse"` 传输 **跳过并 report**~~ **SUPERSEDED 2026-10-03**：`sse` 一并支持（W3a-2，SDK `sse_client`） | 用户拍板；SDK 原生支持 2024-11-05 HTTP+SSE，成本近零 |

## 4. 波次表

| 波 | 分支 | 工单 | 内容 | 前置 | 写范围（文件级不相交） |
|---|---|---|---|---|---|
| W1a | `feat/mcp-stdio` | `01-w1-mcp.md` | MCP 插件（stdio） | W0 | `mocode/host/plugin/builtin/mcp/**` + `tests/test_builtin_mcp.py` |
| W1b | `feat/codemode` | `02-w1-codemode.md` | codemode 插件 | W0 | `mocode/host/plugin/builtin/codemode/**` + `tests/test_builtin_codemode.py` |
| W2 | `feat/mcp-codemode-integration` | `03-w2-integration.md` | 注册进 `builtin_plugins()`、保留名、README/docs、端到端测试 | W1a+W1b 合并 | `mocode/host/plugin/host.py`、`README.md`、`docs/plugins.md`、`docs/ARCHITECTURE.md`、`tests/test_builtin_mcp_codemode.py` |
| ~~W3~~ | ~~`feat/mcp-http`~~ | `04-w3-mcp-http.md` | ❌ 作废（手写传输设计被 SDK 方案取代，见 §9） | — | — |
| dep | master（lead） | — | 官方 SDK 依赖 + AGENTS.md 清单 | — | `pyproject.toml`、`uv.lock`、`AGENTS.md` |
| W3a-1 | `feat/mcp-sdk-stdio` | `05-w3a1-sdk-stdio.md` | stdio 客户端改用官方 SDK（删 `session.py`/`rpc.py`） | dep | `builtin/mcp/**`、`tests/test_builtin_mcp.py` |
| W3a-2 | `feat/mcp-sdk-http` | `06-w3a2-http-sse.md` | streamable HTTP + SSE 传输 | W3a-1 | `builtin/mcp/**`、`tests/test_builtin_mcp.py`、`tests/test_builtin_mcp_http.py` |
| W3b | `feat/mcp-sdk-subs` | `07-w3b-subscriptions.md` | modern subscriptions/listen → `sync_tools` | W3a-2 | `builtin/mcp/client.py`、新增 `subscriptions.py`、新测试文件 |
| W3c | `feat/mcp-sdk-resources` | `08-w3c-resources.md` | resources 只读工具 | W3a-2 | `builtin/mcp/tools.py`、`runtime.py`、新测试文件 |
| docs | master（lead） | — | `docs/plugins.md` mcp 节收口 | W3b+W3c | `docs/plugins.md` |

- W1a ∥ W1b 并行（各自包 + 各自测试文件，零共享写入）。
- W2 是 **唯一** 允许改 `host.py` / 文档的波；W1a、W1b 禁碰这些文件。
- W1a/W1b 自测时**不需要** `builtin_plugins()` 注册：用 `plugin_host(plugins=[PLUGIN])`
  或直接实例化插件即可（见 `ref/mocode-api.md` 的测试 fixture 事实）。
- W3a-1 → W3a-2 →（W3b ∥ W3c）串并行；**W3b 与 W3c 写范围文件级不相交**，一个 agent 一波，从同一合并点各切分支。
- dep/docs 两行是 lead 直落 master 的跨模块小改（依赖与文档收口），不进 worker 写范围。

合并顺序 = 表内顺序；W1a、W1b 谁先合都行（文件不相交），但 W2 必须在两者都合之后。

## 5. 全局不变量（每份工单都引用，违反即打回）

1. **`uv run pytest -q` 全绿，退出码独立确认（`uv run pytest -q; echo $?`，禁止管道吞）。**
   基线 700 passed；只增不减。每个 commit 独立过门禁。
2. **`mocode/core/**` 零 diff。** 需要的能力当前已全部存在（见 `ref/mocode-api.md`）。
   若发现确需 core 改动：**停下、写进报告**，不越界。
3. **host 层改动仅限 `host.py` 的 `builtin_plugins()` 一行（W2）**；其余 host/ 文件零 diff。
4. **对外行为零变化**：不启用 `mcp`/`codemode` 时，prompt、工具列表、事件流、session 字节不变。
5. **AGENTS.md 十条硬不变量有效**，尤其：能写成插件的概念不进 core；唯一执行路径
   `AgentLoop.start()`；观察走事件流；插件贡献只经 `Plugin.build(ctx)`；插件实例无状态
   （per-conversation 状态放 `build()` 创建的对象 / 闭包 / registry 句柄，**不放 `self`**）。
6. **program-origin 契约**（`mocode/core/dispatch.py` 模块 docstring）：`origin="program"` 的调用
   事件进 channel、**不进 messages**、**不折叠进 turn 的 `tool_calls_made`**。codemode 的脚本调用
   一律走它，禁止绕过 dispatcher 直接 `Tool.run`。
7. **写入范围外零 diff**；禁触清单逐文件写明；遇阻塞报告后停止，不越界自救。
8. 代码/注释/docstring/docs/commit 一律**英文**；spec 中文。commit 小写祈使句，
   `feat:` / `test:` / `docs:` 前缀；标题一行 + 正文动机。
9. ~~无新 runtime 依赖~~ **SUPERSEDED 2026-10-03**：例外是官方 SDK `mcp==2.2.0`（用户拍板，`fda301f`）；除此之外 `dependencies` 不变，`uv.lock` 只允许这一处依赖树扩张。
10. 不 push、不打 tag、不发版；agent 不 merge、不碰主检出与其他 worktree。

## 6. Git / worktree / 环境协议

- **worktree 根**：`C:\Users\shifu\.worktrees\mocode\<分支名中 / 换成 ->`（仓库外）。lead 创建/销毁。
- worker 在专属 worktree 内工作：**绝不操作主检出 `C:\Users\shifu\Desktop\mocode`，
  绝不 merge / push / tag / checkout master。**
- 分支从**合并时刻的最新 master** 创建（lead 负责）；开工第一步 `uv sync`。
- lead `git merge --no-ff`；合并前后各跑一次全量门禁并独立确认退出码。
- 环境事实：Windows 10 + Git Bash；uv + Python 3.13；路径全 ASCII；无浏览器；
  POSIX 专属行为（进程组、SIGTERM 树）在测试里按 `sys.platform` 分支，Windows 只断言直接子进程被终止。

## 7. 权威参考（worker 只信组内 `ref/`，不信仓外链接）

| 文件 | 内容 |
|---|---|
| `ref/pi-codemode.md` | pi codemode 官方文档全文（DSL 语义来源） |
| `ref/pi-mcp.md` | pi MCP 官方文档相关小节（exposure 语义、配置规则来源） |
| `ref/mcp-protocol.md` | MCP 协议要点：**modern 2026-07-28（`server/discover`/`_meta`/`resultType`/MRTR）+ legacy 握手**，stdio/HTTP framing，错误码 |
| `ref/agent-plugins-mcp-json.md` | Agent Plugins 1.0.0 `mcp.json` 规范（§7.2/§9）+ 路径包含 + 与 mocode 自有配置的分层 |
| `ref/mocode-api.md` | **当前代码**的插件/工具/上下文 API 事实，带 `file:line` 证据 |
| `ref/codemode-dsl.md` | 我们冻结的 Python codemode DSL 规范（目标形态） |

行号可能随基线轻微漂移；**冲突时以当前代码为准**，各工单重述了已核实的关键事实。

> **协议与标准基线（2026-10-02 核实）**：MCP 当前版本 = **2026-07-28**（modern：无握手、
> 每请求 `_meta`、`server/discover`、`resultType`、MRTR）；旧版 ≤2025-11-25 = **legacy**
> （`initialize` 握手）。实现必须 **dual-era**（D13）。Agent Plugins = **1.0.0**（`mcp.json`
> 固定位置、严格 schema；见 `ref/agent-plugins-mcp-json.md`）。

## 8. 收尾

终验（全量门禁 + `import mocode` <1ms + 两个插件手动 smoke）→ 汇总报告
（工单状态 / 取舍登记 / 遗留清单）→ 清理 worktree、保留分支。收尾时 lead 在每份工单头部补一行状态。

## 9. 波次 W3 重启：改用官方 `mcp` SDK（2026-10-03）

2026-10-03 用户拍板：MCP 客户端整体改用官方 `mcp` Python SDK（v2）重写，取代
`04` 的手写传输方案。群组决策修订：D6/D13/D15 作废（见 §3），不变量 9 加例外。

**Step-0 验证记录（lead 实测，2026-10-03）**：

1. `uv add mcp` → `mcp==2.2.0`，23 包（含 starlette/uvicorn/sse-starlette/python-multipart
   服务端栈、pywin32[Windows]）；`idna` 3.13→3.20；**全量门禁 891 passed exit 0**；
   `import mocode` 0.74ms 且 `-X importtime` 无 `mcp`（SDK 在导入图外）。
2. `Client` 是 dataclass：`Client(server, *, mode="auto", read_timeout_seconds=None,
   input_required_max_rounds=10, ...)`；`async with` 即生命周期；`mode="auto"` 先
   `server/discover` 探测、失败回退 `initialize`。
3. 传输签名：`streamable_http_client(url, *, http_client=None, terminate_on_close=True,
   max_sse_event_size=1MiB)`（**headers 必须走 `httpx2.AsyncClient`**）；
   `sse_client(url, *, headers=None, timeout=5.0, sse_read_timeout=300.0, ...)`
   （**sse 直接收 headers**）；`stdio_client(StdioServerParameters, errlog=sys.stderr)`。
4. `StdioServerParameters.env` 是"叠加在裁剪过的平台默认环境上"——mocode 必须显式
   传全量 env 才能保持 W1a 行为。
5. input_required：默认 10 轮驱动，耗尽抛 `InputRequiredRoundsExceededError`；
   手工驱动 `session.<method>(..., allow_input_required=True)`。mocode 无交互 UI →
   映射 `mcp_input_required`。
6. 进程内 `MCPServer` + `Client(server)` 可用（官方测试用法，实测 2026-07-28）——
   各波测试的主 seam。
7. **真机冒烟（仓库外临时脚本，不入库，符合用户版权要求）**：对一个托管
   streamable-HTTP MCP 搜索服务（key 可选，有无 key 结果一致）连接，
   `mode="auto"` 协商出 **2025-11-25（legacy 回退路径真实可用）**；
   server 为 `anysearch-mcp-server 1.0.0`，tools-only capabilities，4 个工具。
   只记录元数据，不调用工具、不落任何结果内容。
