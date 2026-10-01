# Spec 01 · P0 内核：公共分发器 + provenance + 可见性 + 归属 + 来源（波次 W1）

> 分支 `spec/p0-dispatcher`；worktree `C:\Users\shifu\.worktrees\mocode\spec-p0-dispatcher`；
> 前置：W0（specs 已在 master）；深读：`specs/2026-10-01-kernel-plugin-refactor/00-overview.md`（下称"总纲"）+ `ref/kernel-plugin-api.md` 的 K1/K2/K4/K9/K18 与 §0"什么不要动"。
> 你只读本 worktree 内的文件；绝不操作主检出，绝不 merge/push/tag。

## 现状事实（2026-10-01 已核实，file:line 以当前代码为准）

- 工具分发管线锁在 `mocode/core/agent.py` 私有方法：`_one`(:465，解析 JSON 参数)、`_run_tool`(:475，hook 拦截→ToolCallStarted→denied/parse 分支→`_execute_tool`→`on_tool_complete`→`_truncate`→ToolCallFinished→返回 tool message dict)、`_execute_tool`(:533，not found/switched off 检查→`asyncio.wait_for` 超时→`cancel_event` 协作取消→ToolError 映射→`split_result`)。批量入口 `_run_tool_batch`(:435，create_task+gather)。`_publish`(:602) 盖 `run_id` + 折入 `turn.state` + 发 channel。`_truncate`(:590)、前缀常量 `ERROR_PREFIX/TIMEOUT_PREFIX/DENIED_PREFIX`（`core/tool.py:14-16`）。
- `ToolCallContext`（`core/hook.py:79-102`）：`tool_name, tool_args, tool_call_id, deny, status, error_code, tool_result, tool_details, tool_timeout, emit, cancel_event` + `cancelled` property。**无 origin / parent_call_id**。
- `ToolCallStarted(call_id, name, args)`（`core/events.py:182`）、`ToolCallFinished(call_id, name, status, result, error_code, duration, details)`（:211）。**无 origin / parent_call_id**。
- `ToolRegistry`（`core/tool.py:182-311`）：`register`(:206) **重名静默覆盖**；`names()`(:225) 唯一可见投影；`all_schemas()`(:262) frozen 优先；`select()`(:276) 过滤视图；`freeze(schemas=None)`(:245)。**无 audience 参数、无 ToolConflictError**。
- `HostContext.emit`（`host/plugin/context.py:107-120`）直接 `channel.publish(event)`，**不盖 run_id**（run_id 只由 `_publish` 盖）；build 期 `agent is None` 抛 RuntimeError。
- `RunState.apply`（`core/state.py:123-172`）把 `ToolCallStarted` 折进 `tool_calls_made` 计数；`Turn.subscribe` 的 `_is_mine`（`core/turn.py:68-69`）按 `run_id` 过滤。
- 测试惯例：`tests/providers.py` 的 `MockProvider`（末条 response 永远重复、tool-call 参数 8 字符分片）；`tests/test_agent_loop.py` 为 pytest 原生 class + 显式 `@pytest.mark.asyncio`。

## 目标

把"执行一个工具调用"从 `AgentLoop` 私有管线提取为 core 的公共组件（K1），加上来源与归属的结构化字段（K2/K9/K18）与按受众的可见性（K4）。循环与插件（未来的 codemode、sub-agent 工具）共用同一执行入口，行为不可能漂移。**模型直连路径的对外行为零变化**（现有测试不改断言全部通过）。

## 工单（按序执行，每项一个 commit，每 commit 过全量门禁）

### T1 `core/dispatch.py` — ToolDispatcher（K1）
- 新建 `mocode/core/dispatch.py`：
  ```python
  @dataclass
  class DispatchResult:
      status: ToolStatus
      content: str            # 模型可读文本（已加前缀、已截断）
      details: dict[str, Any] # 结构化数据，永不进 messages
      error_code: str | None = None
      duration: float = 0.0
      call_id: str = ""

  class ToolDispatcher:
      """完整策略管线：hooks 拦截 → 可见性检查 → 超时/取消 → 状态映射 →
      前缀 → 截断 → 事件发布。AgentLoop 与插件共用，行为不可能漂移。"""
      def __init__(self, registry, hooks, config, publish): ...
      async def run(self, name, args, *, call_id="", parse_error=None,
                    origin="model", parent_call_id=None, timeout=None) -> DispatchResult
  ```
- `publish` 是注入的 async 回调，由 `AgentLoop` 提供：model 起源走 `_publish`（盖 run_id + 折 RunState + channel）；**program 起源走"盖章不折叠"路径**（盖父 run_id、发 channel、**不折入 `turn.state`**）。两条路径都在 dispatcher 内选择，循环不再各自拼装。
- 管线语义逐条等价迁移自 `_run_tool`/`_execute_tool`：on_tool_start 在 ToolCallStarted **之前**（消费者只见最终参数）；parse_error→`error:` invalid JSON；deny→`denied:`；not found→`error: unknown tool`；switched off→`denied: tool '...' is switched off`（该检查改为 audience 感知，见 T3）；`asyncio.wait_for` 超时→`cancel_event.set()`+`TOOL_TIMEOUT`+`timeout: Ns`；CancelledError→set 后 re-raise；ToolError→`error_code`；成功→`split_result`；on_tool_complete 之后 `_truncate`；ToolCallFinished 带全部结构化字段。
- **嵌套 call_id 分配**（K2）：program 起源给 `parent_call_id` 时分配 `<parent_call_id>:<n>`（dispatcher 内自增计数）；无 parent 时自分配 `pcall_<n>`；model 起沿用 provider 的 call id（缺席时 `call_<n>`，即现 `_next_call_id` 逻辑迁入）。
- `AgentLoop.__init__` 创建 `self.dispatcher = ToolDispatcher(...)`（公开属性）；删除 `_run_tool/_execute_tool/_one`；`_run_tool_batch` 与 `_iterate` 改调 `self.dispatcher.run(...)`，tool message dict 由循环从 `DispatchResult` 组装（`{"role":"tool","tool_call_id":provider_id,"content":result.content}`）。
- **program 起源契约**（写进 `core/dispatch.py` 与 `core/hook.py` docstring，并落 ARCHITECTURE.md）：`origin="program"` 的调用——事件进 channel（可观测可审计）、携带父 run_id 与 `parent_call_id`；**不进 messages；不折入父 turn 的 `tool_calls_made` 计数**。
- 新测试 `tests/test_dispatch.py`：program 起源（事件可见/不进 messages/计数不变/嵌套 call_id 形如 `parent:1`）、deny/timeout/error 语义与 model 起源逐项一致、dispatcher 可脱离 AgentLoop 单独构造使用（裸 core 用法）。

### T2 provenance 字段（K2）
- `ToolCallContext`、`ToolCallStarted`、`ToolCallFinished` 各加：
  ```python
  origin: Literal["model", "program"] = "model"
  parent_call_id: str | None = None
  ```
  （events 注意保持 `to_dict()` 往返；默认值保证旧事件 dict 可读。）
- `RunStarted` 的 `tools` 投影不变；`Turn.subscribe` 的过滤行为不变（program 事件带父 run_id，turn 订阅者自然可见——写进契约测试）。

### T3 可见性按受众（K4）
- `Tool.__init__` 加 `availability: Literal["model", "program", "both"] = "both"`；存为属性。
- `ToolRegistry`：`names(*, audience="model")`、`all_schemas(*, audience="model")`、`select(*, audience="model", ...)`。`audience="model"` 只含 `availability in ("model","both")` 且 enabled 的工具；`"program"` 只含 `("program","both")` 且 enabled。**`freeze()` 钉住的仍是 model 投影**（语义不变）；switched-off 检查在 dispatcher 内按调用的 origin 选 audience。
- 测试：三种部署形态（全 both / 折叠式标 program / 混合）各自拿到正确的 schema 面与执行许可。

### T4 `ctx.emit` 盖 run_id（K9）
- `HostContext.emit`：`agent is None` 仍抛 RuntimeError（本波不改阶段模型，R3 在 W3）；否则
  ```python
  turn = self.agent.turn
  if turn is not None and not turn.done and not event.run_id:
      event.run_id = turn.id
  await self.agent.channel.publish(event)
  ```
- 契约（写 ARCHITECTURE.md 的"事件归属"小表）：哪些事件进 turn 视图（按 run_id）、哪些只进会话流；`ctx.emit` 发布的事件不折 RunState（现状保持）。
- 测试：运行中插件 emit 的事件被 `turn.subscribe()` 订阅者看到、run_id == turn.id；空闲期 emit 的事件 run_id 为空（进会话流不进 turn 视图）。

### T5 `Tool.source` + 注册路径盖章（K18）
- `core/tool.py`：`Tool(..., source: str = "")`；`register(tool, *, replace: bool = False)`——重名且**两者 source 均非空且不同**时抛 `ToolConflictError`（新异常，core/tool.py）；同 source 覆盖允许（插件热更新自己的工具）；任一方 source 为 `""`（裸 core 用法）保持旧的覆盖语义；`replace=True` 强抢。
- 盖章点在 host 注册路径，**不许插件自报**（插件传了 source 也以盖章为准——覆盖为通道前缀名）：
  - `host/plugin/loader.py` 的 `load_plugins` / `LoadedPlugins` 增加与 plugins 对齐的 `sources: list[str]`（内置插件 `"builtin:<发现名>"`，目录发现 `"plugin:<清单名>"`）。
  - `PluginHost.build_all` / `prepare_all` 逐插件把"当前来源"放进 ctx（如 `ctx._current_source`），`HostContext` 在 `__post_init__` 里给 `self.tools` 换成盖 chapter 的 registry 子类（`register` 时若 `tool.source == ""` 或与通道不符则改写为当前来源）。
  - 宿主代码在无插件上下文处直接注册 → `"host"`；裸 core / 测试直建 registry → 保持 `""`。
- 测试：内置工具盖到 `builtin:*`；第三方插件注册盖 `plugin:<name>`；假名抢不到 builtin 身份；异 source 重名抛 `ToolConflictError`；`replace=True` 通过；同 source 覆盖不抛。

### T6 SDK 导出 + 文档 + 收尾
- `mocode/plugins/__init__.py` 导出 `ToolDispatcher`、`DispatchResult`、`ToolConflictError`（本波允许改此文件；W2 起禁触，W3 统一收口）。
- `docs/ARCHITECTURE.md`：dispatcher 小节（循环变薄封装）、provenance 两轴表（`source`=静态归属/注册时盖章 vs `origin`=本次调用发起方/每次调用）、事件归属表（T4）。`docs/plugins.md`：`Tool` 新参数（availability/source）与 dispatcher 用法各一段。
- 检查 `import mocode` 耗时仍 <1ms：`uv run python -c "import time;s=time.perf_counter();import mocode;print(f'{(time.perf_counter()-s)*1000:.1f}ms')"`。

## 验收（完成前自测，报告真实结论）

1. `uv run pytest -q` 全绿（基线 466 + 新增），独立 `echo EXIT=$?` 确认退出码 0。
2. `tests/test_dispatch.py` 覆盖 T1-T5 全部契约点（列出测试名）。
3. `git diff master --stat` 的文件清单 ⊆ 写入范围（见禁触清单）。
4. import mocode 计时命令的真实输出。

## 禁触清单

- `mocode/cli/**`、`mocode/providers/**`、`examples/**`、`mocode/core/provider.py`、`mocode/core/turn.py`、`mocode/core/channel.py`、`mocode/core/state.py`（如需 RunState 适配 program 事件跳过折叠，**在 dispatcher 的 publish 回调里解决**，不改 state.py）。
- `tests/test_retry.py`、`tests/test_provider.py`、`tests/test_plugins.py`、`tests/test_cli_plugin.py`、`tests/test_plugin_install.py`（W2 并行波的领地——本波的新测试全部进 `tests/test_dispatch.py` 或既有 `tests/test_agent_loop.py`/`tests/test_tools.py`）。
- `TODO.md`、`docs/providers.md`、`docs/embedding.md`。
- spec 文件本身（归 lead）。

## 最终报告格式

工单状态表（T1-T6 各 ✓/✗/偏差）｜commit 清单（hash + message）｜自测真实结论（命令 + 退出码）｜偏差与取舍｜未决问题。遇阻塞：报告阻塞点后停止，不越界自救。
