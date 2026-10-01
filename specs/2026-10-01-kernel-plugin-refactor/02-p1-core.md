# Spec 02 · P1 核心：JSON Schema + 显式 context + 策略覆盖 + 请求拦截 + 终止语义 + 预算 + 策略类型合一（波次 W2-A）

> 分支 `spec/p1-core`；worktree `C:\Users\shifu\.worktrees\mocode\spec-p1-core`；
> 前置：W1 已合并（`core/dispatch.py` 存在；`Tool` 已有 `availability/source`；事件已带 `origin/parent_call_id`）；
> 深读：总纲 + `ref/kernel-plugin-api.md` 的 K3/K5/K6/K7/K8/R1/R2 与附录 B/C。
> 你只读本 worktree 内的文件；绝不操作主检出，绝不 merge/push/tag。

## 现状事实（W1 合并后的期望状态；开工前先通读代码校准）

- `mocode/core/tool.py`：`Tool(name, description, params: dict[str, dict], func, *, tags, summary_key, result_key, availability, source)`；`params` 是 `{name: {type, description, enum, default, optional}}` 扁平 mini-DSL；`_validate_args` 只补 default + 缺 required 抛 `ToolError(missing_param)`；`to_schema()` 把 params 映射成 JSON Schema；`_accepts_context` 用 `inspect.signature` 探测第二参数（`tool.py:53-64` 原逻辑，W1 未动）。
- `mocode/core/dispatch.py`：`ToolDispatcher.run(...)` 完整管线；`AgentConfig(tool_result_limit=50000, tool_timeout=240, max_iterations=0)`（`core/agent.py:64-74`）。
- `RunFinished(content, usage, iterations, tool_calls_made, cancelled)`——**无 stop_reason**；`chat()` 撞 `max_iterations` 返回空串（`agent.py:248-263` + `:414-418` 的 break）。
- `host/config.py`：`AgentSettings(tool_timeout, max_iterations)`（:156-161）与 core 的 `AgentConfig` 手工双轨；`runtime._agent_config()`（:260-265）只映射两个字段，`tool_result_limit` 在 config 里不可配。
- `AgentHook` 现有 `before_iteration / on_tool_start / on_tool_complete / on_event`（`core/hook.py:105-122`），无请求拦截点（TODO.md 第 3 条自认）。
- 内置工具的 params 定义在 `host/plugin/builtin/filesystem.py`（`_READ_PARAMS` 等模块级常量 + `ReadTool/WriteTool/EditTool` 子类）、`shell.py`（command/restart/timeout，timeout 参数自管）、`skills.py`。
- examples/plugins/git-status 与 json-validate 的 `mocode/plugin.py` 用旧 `params=` 注册工具。

## 目标

工具参数升级为官方 JSON Schema（K3）+ 隐式签名探测改显式声明（R2）+ 工具/调用级策略覆盖（K5）+ provider 请求拦截 hook（K6）+ 可区分的终止语义（K7）+ 调用数与墙钟预算（K8）+ 删掉双轨策略类型（R1）。全部允许破坏旧 API，**但每个 commit 过全量门禁，行为语义按本工单精确定义**。

## 工单（按序执行，每项一个 commit）

### T1 JSON Schema 参数（K3）
- `Tool.__init__`：`params` 参数删除，改为
  ```python
  Tool(name, description, schema: dict, func, *, tags=..., summary_key="",
       result_key="", returns: dict | None = None, availability=..., source=...)
  ```
  `schema` 是 JSON Schema 的 object 节点（`{"type":"object","properties":{...},"required":[...]}`），`to_schema()` 直接透传为 `parameters`；`returns` 存为属性（结构化输出元数据，SDK 渲染用），不出现在请求 schema 里。
- **零依赖迷你校验器**（`core/tool.py` 内或同包新函数）：覆盖 `type`（string/integer/number/boolean/array/object/null）、`required`、`properties`、`items`、`enum`、`anyOf/oneOf`、`default`；递归校验嵌套；未知关键字**放行**并用 stdlib `logging.getLogger(__name__).debug(...)` 记录（core 无其他日志，不引入新依赖）。integer 与 number：bool 不算任何 type 的合法值（Python bool 是 int 子类，显式排除）。
- `_validate_args` 升级：补 default（顶层与到达的嵌套层）→ 缺 required 抛 `ToolError("missing_param")`（保持）→ 类型不符抛 `ToolError("invalid_type")`。校验失败即 ToolError，走 `error:` 前缀管线，不炸 turn。
- `summary_key` 语义改为"展示摘要取哪个参数"，默认取 `required[0]`，无 required 取首个 property。
- 同步改写全部使用方：`host/plugin/builtin/filesystem.py`、`shell.py`、`skills.py` 的全部工具；`examples/plugins/git-status/mocode/plugin.py`、`examples/plugins/json-validate/mocode/plugin.py`（后者是自带 venv 的独立环境，只改文件不跑它的 uv）；`mocode/core/__init__.py` 若有示例 docstring 一并更新。
- 测试：嵌套 object/array schema 注册后 `to_schema()` 透传、校验器各关键字正反例、`enum` 拒绝、未知关键字放行。

### T2 显式 with_context（R2）
- 删 `_accepts_context`；`Tool(..., with_context: bool = False)`——True 时构造期校验 `func` 可按 `(args, ctx)` 调用（参数个数/形态不符立即 `TypeError`），`wants_context` 由该开关决定。
- 全部带 ctx 的工具（内置 shell/filesystem/skills、examples、tests 里的 fixture 工具）迁移为显式声明。
- 测试：声明不符构造即炸；两类错误（漏声明/错声明）都在 import/build 期暴露而非运行时。

### T3 ToolPolicy（K5）
- `core/tool.py`：
  ```python
  @dataclass(frozen=True)
  class ToolPolicy:
      timeout: int | None = None          # 秒；None = 落到 AgentConfig.tool_timeout
      result_limit: int | None = None
  ```
  `Tool(..., policy: ToolPolicy | Callable[[dict], ToolPolicy] | None = None)`——callable 接收已校验 args，支持按参数定策略。
- dispatcher 解析优先级：**调用级 `run(timeout=...)` > 工具级 policy > config 级**；生效值写进 `ToolCallContext.tool_timeout`（超时时已有此行为）与截断 limit。
- bash 迁移：`shell.py` 的 `timeout` 参数保留（模型面不变），语义转交 dispatcher——工具以 `policy=lambda args: ToolPolicy(timeout=args.get("timeout"))`（None → config），删除 shell 内部对参数 timeout 的自管逻辑（前台等待仍需它驱动读泵，见 T6 一起改）。

### T4 请求拦截 hook（K6）
- `core/hook.py`：
  ```python
  @dataclass
  class RequestContext:
      messages: list[dict]      # 可 in-place 改写或整体 rebind（ctx.messages = [...]）
      system_prompt: str        # 本 run 范围内有效（与 before_iteration 同规则）
      tools: list[dict]         # 本次请求实际发送的 schema 快照
      model: ModelSpec
      emit: EmitFn
  @dataclass
  class ResponseContext:
      usage: Usage | None       # 可改写（token 记账修正）
      finish_reason: str | None
      iteration: int
  ```
  `AgentHook` 加 `async def before_request(self, ctx) -> None` / `async def after_response(self, ctx) -> None`（默认空实现，`HookRunner` 补对应包装）。**重试不走这两个 hook**（重试窗口在首 chunk 前，属 K13 编排）——写进 docstring。
- `AgentLoop._iterate`：每次 provider 调用前组装 `RequestContext`（messages/system_prompt/tools 快照）→ 跑 `before_request` → 回读 rebind → `with_retry_stream` → 响应后 `after_response`。`system_prompt` 的回读与 before_iteration 同样"仅本 run 有效"（`_drive` 的 finally 恢复已覆盖，确认即可）。
- `TODO.md` 第 3 条（Interception thin）划掉/更新为已实现。
- 测试：hook 改写 messages/usage 真实生效、tools 快照正确、hook 抛错不炸 turn（HookRunner 既有隔离）。

### T5 终止语义（K7）+ 预算（K8）
- `RunFinished` 删 `cancelled: bool`，加
  ```python
  stop_reason: Literal["completed","max_iterations","max_tool_calls","time_budget","cancelled"] = "completed"
  ```
  （`to_dict` 往返一致；取消路径 `stop_reason="cancelled"`。）
- `core/agent.py` 新异常 `class IterationLimit(Exception)`：`chat()` 撞 `max_iterations` 时抛出（不再返回空串）；`stream()` 消费者看 `RunFinished(stop_reason="max_iterations")`。`run_with_messages` 的 had_error 分支兼容它。
- `AgentConfig` 加 `max_tool_calls: int = 0`（0=不限；本 turn 累计）、`max_turn_seconds: int = 0`（0=不限；到期按终止处理）。
- 检查点（写在 `_iterate` 顶部，每次迭代 provider 调用前）：迭代超限→`max_iterations`；累计工具调用 ≥ 上限→`max_tool_calls`；墙钟超→`time_budget`。三个预算触发走与 max_iterations 相同的终止路径（历史保持可回放：已发出的工具调用保持已应答）。**墙钟不深入重试退避内部**（总纲取舍 #8）。
- 测试：三个 stop_reason 各自触发与区分；`chat()` 抛 `IterationLimit`；取消路径 `stop_reason="cancelled"`；`config` 新字段经 T6 可配。

### T6 删 AgentSettings（R1）
- `host/config.py`：删 `AgentSettings`；`Config.agent: AgentConfig`（`from_dict`/`to_dict` 做嵌套序列化：`dataclasses.asdict` 出、按 `AgentConfig` 已知字段过滤入，未知子键忽略不炸）；`host/runtime.py` 删 `_agent_config()`，装配处直传 `config.agent`。
- config.json 的 `agent` 段自动获得 `tool_result_limit`/`max_tool_calls`/`max_turn_seconds` 可配性；`tests/test_config.py`、`tests/test_runtime.py` 更新。
- `AGENTS.md` "Where config values belong" 表同步（`agent` 块一行补全新字段）。

### T7 文档 + 收尾
- `docs/ARCHITECTURE.md`：schema 方言、policy 优先级、请求拦截、stop_reason 语义各一段。
- 全库 grep `params=` 残留（`Tool(` 构造与文档示例）清零；`uv run python -c "import mocode"` 计时仍 <1ms。

## 验收

1. `uv run pytest -q` 全绿，独立确认退出码（基线 = W1 合并后的数量，只增不减）。
2. 新测试覆盖：schema 校验器、with_context 构造期校验、policy 三级优先、before/after hook 改写、五个 stop_reason、Config.agent 嵌套序列化。
3. `git diff <merge-base> --stat` ⊆ 写入范围。
4. `grep -rn "params=" mocode/ examples/` 对 `Tool(` 构造零命中（docstring 示例除外——也应更新）。

## 禁触清单

- **`mocode/plugins/__init__.py` 与 `docs/plugins.md`**（总纲取舍 #7，W3 收口；新 API 的插件作者文档 W3 统一写）。
- `mocode/core/provider.py`、`mocode/providers/**`、`tests/test_retry.py`、`tests/test_provider.py`、`docs/providers.md`（W2-B 领地）。
- `mocode/host/plugin/loader.py`、`mocode/cli/plugin.py`、`tests/test_plugins.py`、`tests/test_cli_plugin.py`、`tests/test_plugin_install.py`、`examples/plugins/` 下**新增**目录（W2-C 领地；你只改 examples 既有文件）。
- `mocode/cli/**`、`mocode/core/turn.py`、`mocode/core/channel.py`、`mocode/core/state.py`。
- spec 文件（归 lead）。

## 最终报告格式

工单状态表｜commit 清单（hash+message）｜自测真实结论（命令+退出码）｜偏差与取舍｜未决问题。遇阻塞：报告后停止，不越界自救。
