# ref · mocode 当前 API 事实（2026-10-02 基线）

> 本文件是 **当前代码** 的权威摘录，供 W1/W2/W3 工单直接引用。所有行号以 master
> `2026-10-02`、`uv run pytest -q` = 700 passed 为基线。若行号漂移，以代码为准。
> 只列与本组相关的 API；未列出的不要假设存在。

---

## 1. 分层与约束

- `mocode/core/` 内核：`core ← host ← cli` 单向依赖；core 不含工具名/特性名/配置键。
- `mocode/host/plugin/` 插件运行时；`mocode/plugins/__init__.py` 是插件 SDK 导出面。
- 本组**不改 core**。需要的一切扩展点已存在。

## 2. `Tool`（`mocode/core/tool.py`）

构造签名（`:282`）：

```python
Tool(
    name: str,
    description: str,
    schema: dict,                       # JSON Schema object node
    func: Callable,
    *,
    tags: frozenset[str] = frozenset(),
    summary_key: str = "",
    result_key: str = "",
    with_context: bool = False,
    availability: Literal["model", "program", "both"] = "both",
    policy: ToolPolicy | Callable[[dict], ToolPolicy] | None = None,
    source: str = "",                   # 由注册路径盖章，不要自己填
)
```

- `schema` 必须是 dict；`to_schema()`（`:374`）原样透传为 provider 的 `parameters`。
- `func` 同步或 async 均可；`is_async` 自动检测（`inspect.iscoroutinefunction`）。
- `with_context=True` ⇒ 调用形态 `func(args, ctx)`，`ctx` 是 `ToolCallContext`；
  构造时校验签名（`_positional_shape`），不满足在 import/build 期就抛 `TypeError`。
- `availability`：`"model"` 只给模型、`"program"` 只给程序代码、`"both"` 两边。
  **不可见 = 不 offered 也不可运行**（dispatcher 会 `denied:`）。
- `policy`：静态 `ToolPolicy` 或 `callable(args) -> ToolPolicy`；`None` 字段落回 config。
- `run()` / `run_async()` 直接调用工具；`run_async` 是 async/sync 统一入口。
- 规范化返回：`ToolResult(content: str = "", details: dict = {})`（`:51`）；
  `split_result(result) -> (content, details)` 把裸 str 归一。
- `ToolPolicy(timeout: int|None = None, result_limit: int|None = None)`（`:220`）。
- `ToolError(message, code="execution_error")`（`:20`）。
- `ToolConflictError`：不同 source 同名注册会炸。

`Tool` 实例上可以挂自定义属性（shell 就这么做：`tool.session = session`，
见 `builtin/shell/tool.py` 末尾），用于 `close()` 通过 registry 找回 per-conversation 资源。

## 3. `ToolRegistry`（`mocode/core/tool.py:399`）

| 方法 | 行 | 语义 |
|---|---|---|
| `register(tool, *, replace=False)` | `:430` | 不同 source 同名 → `ToolConflictError`；同 source 覆盖 |
| `unregister(name)` | — | 移除并重新启用 |
| `get(name) -> Tool \| None` | — | 取出 Tool（含 disabled / 不可见） |
| `all() -> list[Tool]` | — | 管理视图（全部） |
| `names(*, audience="model") -> list[str]` | `:475` | **可见投影**：enabled 且 availability 匹配 |
| `enable(name)` / `disable(name)` | — | disable 对两个 audience 都不可见且拒跑 |
| `freeze(schemas=None)` | `:504` | host 的 cache 钉住；本组不直接用 |
| `all_schemas(*, audience="model")` | `:524` | 可见 schema 列表 |
| `select(*, audience, include_tags, exclude_tags, include_names, exclude_names)` | `:542` | 过滤视图，共享 Tool 实例 |

`_AUDIENCES`：`{"model": {"model","both"}, "program": {"program","both"}}`。
`Audience = Literal["model","program"]`。

**盖章**：`host/plugin/context.py:56` 的 `_StampingToolRegistry.register` 把
`tool.source = ctx._current_source or "host"`；插件 build 期间 `_current_source` 是
`builtin:<name>` / `plugin:<name>`。**插件不要自报 source。**

## 4. `ToolDispatcher`（`mocode/core/dispatch.py:91`）

```python
async def run(self, name, args, *, call_id="", parse_error=None,
              origin: Literal["model","program"] = "model",
              parent_call_id: str | None = None,
              timeout: int | None = None) -> DispatchResult
```

- `DispatchResult`：`status: ToolStatus`、`content: str`、`details: dict`、
  `error_code: str|None`、`duration: float`、`call_id: str`。
- `ToolStatus = Literal["ok","error","timeout","denied","not_found"]`（`core/events.py`）。
- 管线：`on_tool_start` hook → 可见性检查（audience 由 origin 决定）→ policy 解析
  （call-level > tool-level > config）→ `asyncio.wait_for`（sync 工具走 `to_thread`）→
  `ToolError`→status 映射 → `on_tool_complete` → 截断 → `ToolCallStarted/Finished` 事件。
- **program-origin 契约**（模块 docstring `:16`）：`origin="program"` 的事件进 channel、
  **不进 messages**、**不折叠进父 turn 的 `tool_calls_made`**；`call_id` 分配为
  `<parent_call_id>:<n>`（嵌套）或 `pcall_<n>`（独立）。
- 调用层级：**唯一入口**就是这里；禁止绕过 dispatcher 直接 `Tool.run`。
- 参考：ARCHITECTURE.md 明确点名 "a codemode-style orchestrator" 应走这个入口。

## 5. `HookContext`（`mocode/core/hook.py`）

`ToolCallContext`（`:62`）：`tool_name, tool_args, tool_call_id, origin, parent_call_id,
deny, status, error_code, tool_result, tool_details, tool_timeout, tool_result_limit,
emit, cancel_event`；`cancelled` 属性读 `cancel_event`。
`emit` 是 async 回调（默认 no-op）。

`IterationContext` / `RequestContext` 同文件，与本组无直接交互。

## 6. 插件生命周期（`host/plugin/base.py` + `context.py` + `host.py`）

```python
class Plugin:
    name: str = ""
    description: str = ""
    def build(self, ctx: BuildContext) -> None: ...          # 同步、只注册
    async def prepare(self, ctx: HostContext) -> None: ...   # 异步、I/O
    def close(self, ctx: HostContext) -> None: ...           # 释放
```

- `build()` 每 conversation 一次；**插件实例无状态**，per-conversation 状态放 build 里
  创建的对象 / 闭包 / registry 句柄。
- `BuildContext`（`context.py:61`）字段：
  `home: Path`、`cwd: Path`、`config: Config`、`model: ModelSpec|None`、
  `plugin_sources: list[Path]`（`:89`）、`register_provider_type`、
  `tools: ToolRegistry`、`commands: CommandRegistry`、`hooks: list[AgentHook]`、
  `prompt_sections: list[Section]`、`plugin_states: dict[str, dict]`（`:113`）。
  方法：`plugin_config(name) -> dict`（`:125`）、`plugin_state(name) -> dict`（`:129`）。
- `HostContext(BuildContext)`（`:160`）：多一个 `agent: AgentLoop`（`:169`）与方法
  `emit(event)`（`:171`）、`emit_message(...)`（`:192`）、`subscribe(...)`（`:217`）、
  `spawn(...)`（`:229`）。装配时同一对象从 BuildContext "长成" HostContext
  （`_with_agent`，`:143`）——build 里存下的 ctx 引用在 call time 就有 `.agent`。
- `PluginHost`（`host.py:125`）：`build_all()`（`:165`）→ `assemble()`（`:264`）→
  首 turn 时 `materialize()`（`:197`）内 `prepare_all()`（`:177`）；`close()`（`:283`）逐个
  `plugin.close()`。单个插件异常被隔离并记 `failures`。
- `builtin_plugins()`（`host.py:58`）返回固定顺序的 8 个 `PLUGIN`。
- `load_plugins(...)`（`host.py:88`）先加载 builtins（受 `config.plugins[name].enabled` 控制），
  再加载发现的三方插件；`reserved` = builtins 名集合 ∪ 传入集合。

## 7. `Config`（`host/config.py`）

- `Config.plugins: dict[str, dict]` 直接来自 config.json 的 `plugins` 块；
  `ctx.plugin_config(name)` = `config.plugins.get(name, {})`。
- `Config.providers` / `agent`(=`AgentConfig`) / `provider` / `model` 等。
- `plugins` 任意键原样保留；`Config.save()` 会写回。

## 8. `Section`（`core/prompt.py`）

```python
Section(name, content=None, *, priority=0, enabled=True, attrs={},
        render: Callable[[dict], str] | None = None,
        pinned=False, derived_from=None)
```

- 渲染顺序 `(priority, insertion order)`，包成 `<name>...</name>`。
- `render` 存在时优先于 `content`；`pinned=True` 只渲染一次直到 `refresh()`。
- `derived_from="tools"` 让 `cache-protect` diff 该 section 的 live 渲染。

## 9. 事件（`core/events.py`）

- `ToolCallStarted(call_id, name, args, origin, parent_call_id)`。
- `ToolCallFinished(call_id, name, status, result, error_code, duration, details, origin, parent_call_id)`。
- `ToolOutput(call_id, text, stream)`。
- `Notice(message, level)`：`ctx.emit(Notice(...))`。
- 每个 Event 有 `to_dict()` 与 `summary()`。

## 10. `Session` 持久化（`host/session.py`）

- `Session.plugin_state: dict[str, dict]`：随 session 存取（`Conversation._as_session` 写
  `self.ctx.plugin_states`，`load_session` 读回）。`ctx.plugin_state("codemode")` 就是其中一格。
- 相关也可看 `Conversation.load_session` / `_as_session`（`host/conversation.py`）。

## 11. `MoCode` runtime（`host/runtime.py`）

- `MoCode(config=..., home=..., plugin_dirs=[...], freeze_interface=True)`。
- `new_conversation(cwd=...) -> Conversation`；`resume(id)`；`plugins_for(cwd)`。
- 测试里 `make_mc()` 的 home 落在 `tmp_path`，`plugin_dirs=[]` 默认不加载三方插件
  （`tests/conftest.py`）。
- 默认会加载 8 个 builtins（除非 `config.plugins[name].enabled=False`）。

## 12. 测试设施（`tests/conftest.py` + `mocode/testing/providers.py`）

- Fixture `make_mc` / `mc`：runtime（home 在 tmp）。
- Fixture `wired`：`(conversation, MockProvider)`，`wired("answer", cwd=...)`。
- Fixture `plugin_host`：`plugin_host(plugins=[PLUGIN], tools=..., responses=..., build=..., assemble=...)`
  —— 不加载 builtins，直接用给定插件装配（W1 自测用这个）。
- `make_agent(*tools, provider=..., config=..., ...)`：裸 `AgentLoop`。
- `mocode.testing`：`MockProvider`、`say(text)`、`call_tool(name, args, call_id=)`、
  `tool_call_response(...)`。MockProvider 的**最后一个 response 永远重复**，脚本必须以 `say(...)` 收尾。
- `write_plugin(root, name, code=..., skills=..., package=...)`：造 Agent Plugins 目录。

## 13. 命名/路径事实

- `host/plugin/loader.py`：`HOST_NAMESPACE = "mocode"`；`report(msg)` 打印 `[plugin] msg` 到 stderr；
  `MANIFEST = "plugin.json"`；`mcp.json` 只在 docstring 被提及，**loader 从不解析**。
- builtin 插件 import 深度：`mocode/host/plugin/builtin/<pkg>/x.py` 用 **5 个点**
  到 core，例如 `from .....core.tool import Tool`（见 `builtin/shell/tool.py`）。
