# Spec 05 · P2 host：上下文阶段分裂 + API 面收敛 + 内置插件范例化 + SDK 收口（波次 W3）

> 分支 `spec/p2-host`；worktree `C:\Users\shifu\.worktrees\mocode\spec-p2-host`；
> 前置：W2 全部合并（schema= / with_context / ToolPolicy / RetryPolicy / 包插件已落地）；开工前先通读 `mocode/host/plugin/` 与 `mocode/plugins/__init__.py` 校准现状；
> 深读：总纲 + `ref/kernel-plugin-api.md` 的 R3-R7、K10、K11 与"K13 per-model 覆盖"。
> 你只读本 worktree 内的文件；绝不操作主检出，绝不 merge/push/tag。

## 现状事实（W2 后的期望形态）

- `HostContext`（`host/plugin/context.py`）单类双阶段：build 期 `agent=None`（emit/subscribe 抛 RuntimeError），assemble 后带 agent；成员含 tools/commands/hooks/prompt_sections/plugin_states/plugin_config()/plugin_state()/emit/subscribe/register(*commands)。
- `Plugin` 协议（`host/plugin/base.py`）：`build(ctx)` 同步 / `async prepare(ctx)` / `close(ctx)`。
- `host/command.py`：模块级 `dispatch(text, *, conversation, commands)`（:108-127）+ `CommandRegistry.register(*commands)`；`ctx.register(*commands)` 是第二注册入口。
- `host/plugin/host.py` `PluginHost`：`reinstate()` public 但唯一调用者是 materialize；`run()` = build_all+assemble 第二装配入口；`_install` 唯一写入点靠命名维持。
- `host/prompt.py:22` TYPE_CHECKING 块 `from .core.prompt import Section`（错误路径，应为 `..core.prompt`）。
- `builtin/filesystem.py` 仍是"模块级常量 + Tool 子类"形态；shell/skills 已在 W2 换 schema API 但保留类形态。
- `Section`（`core/prompt.py`）：`Section(name, content, priority=0, enabled=True)`；`Content = str | list[Section] | Callable[[dict], str]`。
- `providers/openai.py` 有 `retry_policy` 属性；`host/config.py` 的 `ModelEntry` 有 `context_window/max_output/extra_body`。
- `mocode/plugins/__init__.py` 导出 46 名（W1 加了 dispatcher 系列）；W2 新增的 `ToolPolicy/RetryPolicy/IterationLimit/RequestContext/ResponseContext/BuildContext` **尚未导出**。
- `docs/plugins.md` 已有 W2-C 加的"单文件 vs 包"一节，其余节仍是旧 API（params=、build(cli) 等）。

## 目标

host 插件 API 收敛为窄而稳的一面：阶段分裂进类型（R3）、命令单入口（R4）、PluginHost 面收窄（R5）、杂项修正（R6）、内置插件无子类化成为第一范例（R7）、Section 血缘与钉住（K10）、spawn 便利方法（K11）、retry 的 per-model 配置（K13 尾巴）、SDK 导出面与插件文档一次收口。

## 工单（每项一个 commit）

### T1 BuildContext / HostContext 阶段分裂（R3）
- `host/plugin/context.py`：
  ```python
  @dataclass
  class BuildContext:      # build() 收到：贡献目标 + 配置 + 路径，无 agent
      home, cwd, config, model, plugin_sources, register_provider_type
      tools, commands, hooks, prompt_sections, plugin_states
      plugin_config(name), plugin_state(name)

  @dataclass
  class HostContext(BuildContext):   # prepare()/close()/call time 收到
      agent: AgentLoop               # 非空；emit/subscribe/dispatcher 直接用
      def spawn(self, *, system_prompt, tools=None, model=None, visible=True) -> AgentLoop
  ```
- `Plugin.build(ctx: BuildContext)` / `prepare(ctx: HostContext)` / `close(ctx: HostContext)`；`PluginHost.build_all` 传 BuildContext，`prepare_all`/`close` 传 HostContext（runtime 装配序不变：build_all → assemble → materialize 内 prepare_all）。
- emit/subscribe 的 RuntimeError 分支删除（类型即约束）；HostContext.emit 的 run_id 盖章语义（W1 K9）保持。
- `spawn()`（K11）：`agent.derive(system_prompt=..., tools=tools 或 agent.tool_registry.select(), model=..., channel=agent.channel if visible else None)`——默认事件可见、默认不继承 hooks；docstring 注明 call time 使用（build() 时 agent 尚未装配）。
- **迁移全部调用方**：7 个内置插件、`examples/plugins/git-status`、`examples/plugins/multi-file`、tests 全量。插件需要 call-time 能力的模式统一为：build 里创建并保存自己的对象，工具经 `with_context=True` 拿 `ToolCallContext.emit`。
- **审计**：`ctx.emit/subscribe` 在 call time 的真实使用者；若迁移后无人需要（工具侧有 ToolCallContext.emit、hook 侧是对象），在报告登记——接口保留（prepare/close 里合法），不删。

### T2 命令单入口（R4）
- `CommandRegistry.dispatch(text, *, conversation) -> CommandResult` 方法（逻辑自模块级函数迁入）；删除模块级 `dispatch()`；删除 `BuildContext.register(*commands)`（统一 `ctx.commands.register(...)`）。
- 更新调用方：`mocode/cli/app.py`、`mocode/host/conversation.py`（如有）、`docs/embedding.md` 示例、tests。

### T3 PluginHost 面收窄（R5）
- `reinstate` → `_reinstate`（materialize 内部用）；删 `run()`（runtime 是唯一装配者）；`_install` docstring 写明"request surface 唯一写入点，改动必须过这里"。
- 公开面 = `build_all / prepare_all / adopt_session / materialize / rebuild / assemble / close`，与 Conversation 暴露同宽；tests 里用 `run()` 的地方改 build_all+assemble。

### T4 杂项（R6）
- `host/prompt.py:22` 改 `from ..core.prompt import Section`。
- `AGENTS.md` 新增"Adding a hook point"清单小节：加一个拦截点要同步改 `AgentHook` 方法、`HookRunner` 包装、`mocode/plugins` 导出、docs——四处无强制，清单供自查。
- `docs/embedding.md` 首段注明：`start()` 内部 `asyncio.create_task`，只能在 running event loop 中调用（同步上下文用 `asyncio.run` 包裹）。

### T5 内置插件无子类化（R7）
- `builtin/filesystem.py`（及其他仍以"常量 + Tool 子类"注册的工具）改为 `Tool(...)` 直接实例化，schema/元数据内联在构造调用里；需要 per-conversation 状态的（shell 的 BashSession、skills）保留会话类，但 Tool 构造不再子类化。目标形态 = README git-status 示例的样子。

### T6 Section 血缘与钉住（K10）
- `core/prompt.py`：`Section(name, content=None, *, priority=0, enabled=True, render: Callable[[dict], str] | None = None, pinned: bool = False, derived_from: str | None = None)`——`render` 存在时由 Prompt.build 调用产文（`Content` 的 Callable 形态并入 Section.render，顶层 Callable content 保留兼容现状用法）；`pinned=True` 的 section 渲染一次并缓存（rebuild 前不变），防"活注册表每次渲染"与钉住的前缀冲突。
- `host/plugin/builtin/cache_protect.py`：diff 工具 schema 之外，同时 diff `derived_from="tools"` 的 section 渲染结果；变化随既有 `[context update]` 通知路径一并告知。
- 测试：pinned section 在注册表变化后保持字节不变；derived section 变化触发通知。

### T7 K13 per-model 覆盖
- `host/config.py` `ModelEntry` 加可选 `retry: dict` 子段（键对齐 RetryPolicy 字段，未知键忽略）；from_dict/to_dict 往返。
- `host/runtime.py` `provider_for`：模型段有 `retry` 时构造 `RetryPolicy(**known)` 传给 provider（`OpenAIProvider(..., retry_policy=...)`——`providers/openai.py` 构造函数加可选参数，默认保持自带 policy）。
- `AGENTS.md` config 表同步一行；`docs/providers.md` 补 per-model 覆盖示例。

### T8 SDK 导出面 + plugins.md 收口
- `mocode/plugins/__init__.py`：导出面盘点至与 core/host 公开 API 一致——新增 `ToolPolicy`、`RetryPolicy`、`IterationLimit`、`RequestContext`、`ResponseContext`、`BuildContext`、`HostContext`（已在）、`PluginMessage` 若已存在（W4 才加，此处不导）、移除已删符号；`__all__` 排序保持字母序。
- `docs/plugins.md` 全文改写到新 API：`Tool(schema=..., with_context=, availability=, policy=, returns=)`、`BuildContext/HostContext` 两阶段、`ctx.tools` 盖章 source、包插件一节保留并入、`ctx.spawn`、drawer/emit_message 留位不写（W4/W5 补）。**W2-C 加的"单文件 vs 包"一节保留**（措辞可微调）。
- 全库 grep 旧 API 残留（`params=`、`_accepts_context`、`AgentSettings`、`dispatch(text, *, conversation, commands)` 模块级调用、`cli.commands` 旧例）清零。

## 验收

1. `uv run pytest -q` 全绿，独立确认退出码 0（基线 = W2 全合后数量，只增不减）。
2. `uv run python -c "from mocode.plugins import ToolPolicy, RetryPolicy, BuildContext, IterationLimit; print('ok')"` 真实输出。
3. `import mocode` 计时 <1ms（真实输出）。
4. `grep -rn "AgentSettings\|_accepts_context" mocode/ tests/ examples/ docs/` 零命中。

## 禁触清单

- `mocode/core/dispatch.py`、`mocode/core/provider.py`、`mocode/core/events.py`、`mocode/core/state.py`、`mocode/core/channel.py`、`mocode/core/turn.py`（前波已定型；`core/prompt.py` 与 `core/tool.py` 仅按 T6 明确授权的最小改动）。
- `mocode/providers/openai.py` 仅 T7 授权的构造参数改动。
- `mocode/cli/**` 中除 `mocode/cli/app.py`（T2 dispatch 调用点）外的一切。
- `mocode/host/plugin/loader.py`、`mocode/host/plugin/env.py`、`mocode/host/plugin/install.py`、`tests/test_plugins.py` 的 loader 用例区（W2-C 成果，勿回退；你的迁移测试可加新用例）。
- spec 文件（归 lead）。

## 最终报告格式

工单状态表｜commit 清单（hash+message）｜自测真实结论（命令+退出码）｜偏差与取舍（尤其 T1 的 emit/subscribe 审计结论）｜未决问题。遇阻塞：报告后停止，不越界自救。
