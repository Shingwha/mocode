# MoCode 内核与插件 API 优化建议

> 基于对 `Shingwha/mocode`（master，约 6900 行）的完整代码评审。
> 动机：在 mocode 上构建 pi 风格 codemode 插件过程中暴露的缺口。
> 每条建议附现状证据（文件:行号）、问题分析、建议 API 草样，可直接核对。
>
> 优先级：P0 = 不改则插件只能复刻内核私有逻辑；P1 = 很快会咬人；P2 = 锦上添花。
> K 系列（16 条）补能力；R 系列（7 条）只做重构——删重复入口、双类型漂移和运行时才能发现的误用，不加能力。
>
> **无向后兼容约束**：本文所有改动允许破坏现有行为，不需要迁移层、不需要双轨。
> 破坏性触点集中清单见文末附录 B。

---

## 0. 总原则：什么不要动

以下设计经得住重量级插件（codemode 这类编排型插件）的检验，**不建议改动**：

- 三层依赖 `core ← host ← cli`，内核零应用依赖（新增 JSON Schema 校验器也保持零依赖，见 K3）。
- "能写成插件的概念不进内核"——codemode 本身应是一个插件，本文所有改动都是**拓宽插件 API**，零个新应用特性。
- 事件流契约：`run_id + seq`、每个 turn 恰好一个终止事件、`Event.to_dict()` 可跨进程。
- 十一种内核事件**只扩展不重做**：新事件继承 `Event` 声明 `type` ClassVar、override `summary()` 即可。
- `ToolRegistry.freeze()` 留在 registry、host 拥有钉住权；`cache-protect` 负责 diff 通知。
- `(priority, insertion order)` 的 section 排序；build() 同步注册 / prepare() 做 I/O 两段式。
- messages 用 OpenAI dict 方言（`core/provider.py:171-197` 明确声明了理由：内核只维护一种消息形态）。
- hooks 与 events 的分工（单向通知 vs 请求/响应）。
- **重试编排留在内核**（详见 K13）："重试窗口在首个 chunk 前关闭"这条安全边界只有流的消费者知道；provider 内部自行重试会让编排逻辑在每个 provider 里复制、内核失去统一的取消语义与可观测性。

**一句话总结现状**：mocode 的 API 让插件"被循环调用"很舒服，但让插件"反过来驱动循环的机制"（调工具、派生 agent、拦请求）没有公共路径。以下所有条目都在补这个方向。

---

## P0：不改就绕不过去

### K1. 工具分发管线锁死在 `AgentLoop` 私有方法里

**现状**：`AgentLoop._run_tool` / `_execute_tool`（`core/agent.py:475-577`）集中了全部策略语义：

- `ToolCallContext` 构造（含 `emit` 注入）；
- `on_tool_start` / `on_tool_complete` 两段 hook 拦截；
- 已关闭工具的拒跑检查（`agent.py:540-544`，还要正确处理 freeze 语义下"冻结界面仍提供但已关闭"的边界）；
- `asyncio.wait_for` 超时 + `cancel_event` 协作取消；
- `ToolError` → `status` / `error_code` 映射；
- `error:` / `timeout:` / `denied:` 前缀格式化；
- `_truncate` 结果截断（`agent.py:590-594`）；
- `ToolCallStarted/Finished` 事件发布。

**问题**：任何想在循环外"以与直连完全相同的策略语义"执行工具的插件（codemode 的调度桥、sub-agent 工具、未来的工作流节点）唯一的选择是复刻这约 100 行逻辑。复刻必然漂移：忘了 disabled 检查，嵌套调用就绕过 freeze 语义；忘了截断，一次嵌套调用就能把 50KB 结果灌进程序上下文；忘了 `cancel_event`，协作取消就失效。

**建议**：提取为 core 的公共组件，循环自身变薄封装：

```python
# core/dispatch.py —— 循环与插件共用的唯一执行入口
@dataclass
class DispatchResult:
    status: ToolStatus
    content: str                    # 模型可读文本（已加前缀、已截断）
    details: dict[str, Any]         # 结构化数据，永不进 messages
    error_code: str | None = None
    duration: float = 0.0

class ToolDispatcher:
    """完整策略管线：hooks 拦截 → 可见性检查 → 超时/取消 →
    状态映射 → 截断 → 事件发布。AgentLoop 与插件共用，行为不可能漂移。"""

    def __init__(self, registry: ToolRegistry, hooks: HookRunner,
                 config: AgentConfig, channel: EventChannel): ...

    async def run(self, name: str, args: dict, *,
                  origin: Literal["model", "program"] = "model",
                  parent_call_id: str | None = None,
                  timeout: int | None = None) -> DispatchResult:
        """origin="program" 时：事件照常进 channel，但不进 messages、
        不折叠进父 turn 的计数（见 K2 的契约）。"""
```

`AgentLoop._one` 变为 `self._dispatcher.run(...)` 的薄封装；`_iterate` 只保留编排职责。这也符合 mocode 自己的哲学——观察走公共流（events），执行也应该有公共入口。

**破坏点**：`AgentLoop` 的 `_run_tool` / `_execute_tool` / `_one` 三个私有方法删除，逻辑迁移；`_publish` 保留（编排职责）。

### K2. `ToolCallContext` 与工具事件缺 provenance 和父子关系

**现状**：`ToolCallContext`（`core/hook.py:58-92`）只有 `tool_call_id`，没有"谁发起的"和"父调用是谁"；`ToolCallStarted/Finished`（`core/events.py:181-228`）同样只有 `call_id`。

**问题**：

1. hook 无法区分模型直连与程序内嵌套调用，只能靠解析 `tool_call_id` 字符串约定（脆）；
2. UI 想把数百次嵌套调用折叠进父调用块（pi codemode 的 `... (331 earlier calls)`），没有结构化依据；
3. 嵌套事件**现在碰巧**不污染 `RunState`：插件经 `ctx.emit` 发布的事件不经过循环的 `_publish`（`agent.py:602-612`），所以不被折叠——这是实现细节的侥幸，不是承诺，一次重构就可能 silently 破掉。

**建议**（无兼容负担，直接加字段）：

```python
# ToolCallContext、ToolCallStarted、ToolCallFinished 三处各加：
origin: Literal["model", "program"] = "model"
parent_call_id: str | None = None
```

并写下显式契约（建议进 `core/hook.py` docstring 与 ARCHITECTURE.md）：

- `origin="program"` 的调用：事件进 channel（消费者可观测、可审计），但**不进 messages**、**不折叠进父 turn 的 `tool_calls` 计数**；
- 嵌套 `call_id` 由 dispatcher 分配（如 `<parent>:<n>`），`parent_call_id` 是结构化归属；
- turn 订阅（`Turn.subscribe` 的 `keep=self._is_mine`，`core/turn.py:68-69`）对 program 起源事件的取舍显式化：program 事件携带所属 run_id（见 K9），订阅者按需过滤。

### K3. 工具参数直接采用 JSON Schema（替换 mini-DSL）

**现状**：`Tool.__init__`（`core/tool.py:93-121`）的 `params` 是 `{name: {type: string|number|boolean, description, enum, default, optional}}`——只有扁平标量；`_validate_args`（`tool.py:142-153`）只检查"缺必填参数"一种错误。

**问题**：

1. 嵌套参数（object/array 形态的 filter、options）表达不了——MCP 生态的基本形态在 mocode 里无法注册；
2. codemode 的 Python SDK 渲染想生成 `list[dict]` 类型签名没有原料；
3. schema token 效率损失：模型需要靠 description 文字理解结构。

**建议**（无兼容负担，`params` 参数直接替换）：

```python
Tool(
    name="list_issues",
    schema={                                # 官方 JSON Schema 方言，全模型兼容
        "type": "object",
        "properties": {
            "filter": {"type": "object", "properties": {...}, "required": [...]},
            "limit":  {"type": "integer", "default": 50, "description": "..."},
            "state":  {"type": "string", "enum": ["open", "closed"]},
        },
        "required": [],
    },
    func=...,
    returns={"type": "object", "properties": {...}},   # 结构化输出 schema，可选
)
```

配套两个内核决定：

- **零依赖的迷你校验器**：core 不引第三方包。内置一个只覆盖常见关键字的校验器——`type`（string/integer/number/boolean/array/object/null）、`required`、`properties`、`items`、`enum`、`anyOf/oneOf`、`default`；遇到未知关键字**放行**（forward-compat，debug 日志记录）。`to_schema()` 直接透传，`summary_key` 语义改为"展示摘要取哪个参数"（默认取 `required` 第一个或首个 property）。
- 现有内置工具（read/write/edit/bash/skill）的 params 按同一格式改写，顺带补上嵌套能力（如 `read` 的 offset/limit 本就有，无结构变化）。

### K4. 工具可见性只有"开/关"，没有"给谁看"

**现状**：`ToolRegistry` 只有 `names()` 一种可见投影（`core/tool.py:225-238`）+ `disable()`。

**问题**：pi 路线（codemode 加成式）和 DSH 路线（PTC 折叠式）在 mocode 里都只能用别扭手段模拟。折叠式要 `freeze(自定义 schema 列表)` + veto hook 兜底——而 freeze 是 host 的缓存保护杠杆，插件去抢这个杠杆会跟 `cache-protect` 打架（diff 的是 host 钉住的界面，插件钉的是另一份）。pi 官方为 codemode 升级工具元数据正是因为这个缺口：需要能声明"这个工具给 LLM 直连，还是只给 codemode 部分"。

**建议**：

```python
Tool(..., availability: Literal["model", "program", "both"] = "both")

# ToolRegistry（破坏点：签名变化）：
def names(self, *, audience: Literal["model", "program"] = "model") -> list[str]: ...
def all_schemas(self, *, audience: str = "model") -> list[dict]: ...
def select(self, *, audience: str = "model", ...) -> ToolRegistry: ...   # K1 dispatcher 用 program 面
```

freeze 语义不变——钉住的仍是 model 可见投影。三种部署形态用同一机制表达：

- pi 加成式：全部 `both`（默认）；
- DSH 折叠式：工具标 `program` + 一个 SDK prompt section；
- 混合式：标注 `both`，模型自选直连还是编排。

---

## P1：不阻塞，但很快咬人

### K5. 执行策略全局唯一，工具级/调用级没有覆盖口

**现状**：`AgentConfig`（`core/agent.py:64-74`）的 `tool_timeout` / `tool_result_limit` / `max_iterations` 是整个会话唯一策略。真实需求已出现过一次：bash 工具自己加了 `timeout` 参数做软覆盖（`host/plugin/builtin/shell.py:28-32`）——插件在用参数冒充策略。

**建议**：

```python
@dataclass(frozen=True)
class ToolPolicy:
    timeout: int | None = None          # 秒；None = 用 AgentConfig.tool_timeout
    result_limit: int | None = None

Tool(..., policy=ToolPolicy(timeout=600))
# dispatcher.run(..., timeout=...) 允许调用级临时覆盖
```

覆盖优先级：调用级 > 工具级 > config 级。顺手把 bash 的手写 timeout 参数迁到这个正式机制（参数保留，语义转交 dispatcher）。

### K6. Provider 请求没有拦截点（TODO.md §3 已识别，给出具体形状）

**现状**：TODO.md 第 3 条自己承认："Nothing sits between the loop and `provider.stream()`. To inspect or replace the payload — headers, an extra field, a rewritten message list — you must wrap the provider object."

**建议**：按 TODO 给出的形状实现 `before_request` / `after_response` hook 对：

```python
@dataclass
class RequestContext:
    messages: list[dict]        # 可改写（in place 或 rebind）
    system_prompt: str          # 本 run 范围内有效，与 before_iteration 同规则
    tools: list[dict]           # 本次请求实际发送的 schema 快照
    model: ModelSpec
    emit: EmitFn

@dataclass
class ResponseContext:
    usage: Usage | None         # 可改写：token 记账修正
    finish_reason: str | None
    iteration: int

class AgentHook:
    async def before_request(self, ctx: RequestContext) -> None: ...
    async def after_response(self, ctx: ResponseContext) -> None: ...
```

用途：usage 上报/成本拦截、PII 过滤、A/B 消息改写、token 预算强制。这是 compaction、可观测性插件的基础设施。**注意**：重试**不**走这个 hook——重试要包的是"建流 + 等首 chunk"窗口，见 K13。

### K7. `RunFinished` 缺 `stop_reason`，迭代上限与正常完成无法区分

**现状**：`RunFinished`（`core/events.py:92-113`）有 `cancelled` 但没有 `stop_reason`。`agent.py:414-418` 命中 `max_iterations` 时直接 `break`，此时 `answer=""`，发出一个 `content=""` 的 `RunFinished`——消费者无法区分"模型答了空串"、"撞了迭代上限"、"被取消"（cancelled 有标记）。`chat()` 同样返回 `""`。

**建议**：`RunFinished` 加 `stop_reason: Literal["completed", "max_iterations"] = "completed"`；`chat()` 在撞上限时抛 `IterationLimit`（新异常）而不是返回空串。成本跑飞时的排障依赖这个信号。

### K8. 运行预算只有迭代数，缺调用数与墙钟预算

**现状**：`AgentConfig.max_iterations` 是唯一节流阀（`agent.py:70`）。一次迭代可以并发发 10 个工具调用，迭代数限制不住总调用量；也没有墙钟预算——跑飞的循环（模型反复重试同一失败工具）会一直烧钱到手动取消。

**建议**：`AgentConfig` 增加：

```python
max_tool_calls: int = 0      # 0 = 不限；本 turn 累计工具调用上限
max_turn_seconds: int = 0    # 0 = 不限；墙钟预算，到期按取消处理（stop_reason 见下）
```

`RunFinished.stop_reason` 扩为 `"completed" | "max_iterations" | "max_tool_calls" | "time_budget" | "cancelled"`——`cancelled` 布尔保留一个版本后删除（无兼容负担，可以直接只留 `stop_reason`）。两个新预算触发时走与 `max_iterations` 相同的终止路径。墙钟预算同时约束重试退避 sleep（K13 的编排器每次 sleep 前检查剩余预算）。

### K9. `ctx.emit` 的事件不打 `run_id`：归属语义是隐式的

**现状**：循环事件经 `_publish` 盖 `run_id` 戳（`agent.py:609`）；插件经 `ctx.emit` 发布的事件直接进 channel（`host/plugin/context.py:107-120`），`run_id=""`。这造成两层隐式行为：turn 的订阅者（`Turn.subscribe` 的 `keep=self._is_mine`，`core/turn.py:68-69`）看不到运行期间插件发的事件；`RunState` 不折叠它们（K2 的侥幸）。

**建议**：明确并固化语义——`ctx.emit` 在 turn 运行期间自动盖上当前 `run_id`（从 `ctx.agent.turn` 读），让 turn 订阅者**能看到**插件说了什么（`Notice` 本来就是给人看的）；program 起源的嵌套工具事件是否折叠，由 K2 的契约按 `origin` 决定。把"哪些事件进 turn 视图、哪些只进会话流"写成表格进 ARCHITECTURE.md。

---

## P2：锦上添花

### K10. Section 的 callable 形态与 freeze 语义脱节

**现状**：`Content = str | list[Section] | Callable[[dict], str]`（`core/prompt.py:32`）。SDK section 做成 callable 每次从活注册表渲染，与"前缀钉住"直接冲突；做成 build() 时一次性字符串，工具中途变化时 SDK **静默过期**——`cache-protect` diff 的是 schema 和 prompt 文本，对这种"派生自工具的 section"没有感知。

**建议**：

```python
Section("tools-sdk", render=render_sdk, pinned=True)   # 渲染一次缓存
Section(..., derived_from="tools")                      # 声明血缘
```

`cache-protect` 在 diff schema 的同时 diff `derived_from="tools"` 的 section，变化随 `[context update]` 一起通知。codemode 的 SDK 块与工具界面从此不可能漂移。

### K11. `derive()` 的默认值有一个面向插件的坑：`channel=None`

**现状**：`AgentLoop.derive()` 默认 `channel=None`（`core/agent.py:175-183`），子 agent 拿私有流——事件不可见。对子代理是合理默认（display hook 不该继承），但对插件是陷阱：每次都得记得传 `channel=self.agent.channel` 才有可观测性。

**建议**：`HostContext` 加便利方法，固化插件侧常见形态：

```python
def spawn(self, *, system_prompt: str, tools: ToolRegistry | None = None,
          model: ModelSpec | None = None, visible: bool = True) -> AgentLoop:
    """derive + 默认事件可见（visible=True → channel=父 channel）
    + 默认不继承 hooks。call time 使用（build() 时 agent 尚未装配）。"""
```

### K12. 插件作者没有公共测试工具包

**现状**：`tests/providers.py` 的 `MockProvider`（剧本化分块回放）是仓库私有；插件作者想"对着剧本模型测插件"只能整段复制。

**建议**：发布 `mocode.testing` 子模块（`MockProvider` + 事件断言助手 + 常见剧本构造器），文档加"测试你的插件"一节，照 `tests/test_plugins.py` 的惯例给范例。成本极低，对生态质量回报极高。

### K13. 重试机制：编排留在内核，策略交给 Provider

**现状**：`with_retry_stream`（`core/provider.py:216-256`）是内核里的编排器，策略却是四个模块级硬编码常量：`_MAX_RETRIES=6 / _BASE_DELAY=1.0 / _MAX_DELAY=60 / _JITTER_MAX=0.5`（`provider.py:204-207`），对所有 provider 一刀切；`is_retriable()` 只回答"什么算可重试"，不回答"怎么重试"。

**分析**（两个关注点分家）：

- **编排（何时安全重试）必须在内核**：重试窗口在首个 chunk 到达时关闭（`provider.py:221-229`）——流一旦输出，重试会重复输出，这条边界只有流的消费者知道；退避期间的取消语义（turn cancel 必须立刻打断 sleep）、K8 墙钟预算的交互，也只有内核能统一。provider 内部自行重试（yield 首 chunk 前）虽然技术上可行，但编排代码会在每个 provider 实现里复制一遍，内核失去统一的可观测性与预算控制。
- **策略（重试什么、退多久、几次、尊不尊重 `Retry-After`）是 provider/model 的知识**：官方 API 和限流敏感的自建端点不该共用一套参数。

**建议**：

```python
# core/provider.py
@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 7        # 含首次，对齐现状
    base_delay: float = 1.0
    max_delay: float = 60.0
    jitter: float = 0.5
    honor_retry_after: bool = True   # 429 的 Retry-After 优先于指数退避

class Provider(Protocol):
    @property
    def retry_policy(self) -> RetryPolicy: ...      # provider 自带默认
    def is_retriable(self, exc: Exception) -> bool: ...

# with_retry_stream(provider, ..., policy: RetryPolicy) 显式收策略；
# per-model 覆盖挂在 config 的 model 段（与 extra_body 同位置）。
```

模块常量删除；退避 sleep 保持可取消（现状已对），并接入 K8 墙钟预算检查。`OpenAIProvider` 给出贴合官方 API 的默认 policy；`MockProvider` 用内核默认。

### K14.（已移除）compaction 的 token 估算助手

~~内核提供 `estimate_prompt_tokens()`。~~ 已否决：字符/token 启发式本质上是 provider 相关的（各家 tokenizer 不同），内核给出一个不准确的"官方数字"比不给更糟——违背内核应**简洁、通用、准确**的原则。

替代路径已存在且更准确：compaction 插件通过 K6 的 `after_response` / `IterationFinished.usage` 拿到 provider 回报的**真实** prompt_tokens，自行累加（增量消息自己估）即可。估算逻辑属于插件领土，内核不需要知道。

### K15. channel 的 REPLAY/BACKLOG 是全局常量；长 turn 的 TextDelta 内存无界

**现状**：`REPLAY = BACKLOG = 1000`（`core/channel.py:38-42`）。一个长 turn 几秒内就能发 1000 个 delta，重连读者必然 `dropped > 0` 靠 state 重同步（哲学上可接受，但不可调）。`RunState.content` 按 turn 全量累积（`core/state.py:131`），web 嵌入场景多会话并存时内存随 turn 长度线性增长。

**建议**：`EventChannel(replay=..., backlog=...)` 构造参数已存在，host 按模型/场景配置即可（文档化推荐值）；`RunState` 考虑对 `content` 设软上限（超出保留尾部 + 标记），快照语义不变。

### K16. 插件代码只支持单文件，且多文件尝试被静默忽略

**现状**：插件的代码入口在两个面上都硬编码为单个文件：

- 宿主侧：`_spec_from_entry`（`host/plugin/loader.py:217-218`）只认 `mocode/plugin.py`——作者若建成 `mocode/plugin/` 包目录，`code.is_file()` 为 False → `spec.module = None` → 插件被当成纯 skills 插件，**无任何提示**；
- CLI 侧：`load_cli_plugins`（`mocode/cli/plugin.py:73-74`）同样只认 `mocode.cli/plugin.py`，不存在就 `continue`，同样静默；
- 导入器：`import_module_file`（`loader.py:235-256`）用裸的 `spec_from_file_location(module_name, path)`，没有 `submodule_search_locations`——即使入口指向 `__init__.py`，包内相对导入也会失败。

第三方依赖其实已有出路（`PluginVenv.attach()`，`env.py`），缺的只是**插件自己的多文件组织**——稍微复杂的插件（codemode 就需要把 worker 代码与插件逻辑分开）目前只能写成一个文件或诉诸 sys.path 技巧。

**建议**：

1. **统一入口判定**——loader 加一个共用辅助函数，宿主与 CLI 两个 loader 调同一份逻辑：

```python
def code_entry(namespace_dir: Path) -> Path | None:
    """<ns>/plugin.py（单文件）优先，其次 <ns>/plugin/__init__.py（包）。"""
    single = namespace_dir / "plugin.py"
    if single.is_file():
        return single
    package_init = namespace_dir / "plugin" / "__init__.py"
    return package_init if package_init.is_file() else None
```

2. **导入器支持包**——`import_module_file` 检测到导入的是 `__init__.py` 时传 `submodule_search_locations=[包目录]`；现有语义全部保留（sys.modules 先注册再 exec、异常隔离、ModuleNotFoundError 给 `fix` 提示）。子模块自动获得 `mocode_plugin_<slug>.xxx` 名字，插件间无碰撞。

3. **禁止 sys.path 方案，写进文档**——不把 `<plugin>/mocode/` 加进 sys.path：两个插件都带 `helpers.py` 时会在 sys.modules 里互相覆盖，是隐性跨插件污染。包内相对导入是唯一推荐形态；第三方依赖继续走 `PluginVenv`。

4. **消灭静默失败**——发现 `mocode/plugin/` 目录存在但没有 `__init__.py`，或 `mocode/` 下有 `.py` 文件但入口缺失：`report()` 提示（如 "multi-file plugins need mocode/plugin/__init__.py"）而不是当作无代码插件跳过。

5. **文档与范例**——`docs/plugins.md` 加"单文件 vs 包"一节：几百行以内用 `plugin.py`；更大就建包，`__init__.py` 里暴露 `plugin = MyPlugin()`（`resolve_plugin` 的实例约定在包形态下最稳——`vars(package)` 只看见 `__init__` 导入的名字）。`examples/plugins/` 下加一个多文件插件范例（入口 + 两个子模块 + 子模块间相对导入）。

**收获**：插件规模不再受单文件约束；作者拿到的第一个多文件范例就是官方形态；加载失败从静默变成有指引的报错。

### K17.（已移除）惰性 schema / tool search

~~core 加 `brief`/`deferred` 字段与 `ToolRegistry.search()/schema_of()`。~~
已否决：用户裁决——内核保持**简洁、通用、准确**，tool search 是特定
场景（50+ 工具的 MCP 桥/数据库插件）的优化，不该变成内核概念；为不常见
场景预留 core 字段违背简洁原则。

**tool search 完全可以在插件层实现**（调研结论保留备查）：市面两大范式
为 provider-native（Anthropic/OpenAI 的 defer_loading + 托管搜索）与
client-side（Hermes 三桥）；mocode 的 `_validate_args` 对空 `params` 的
stub 全透传，所以插件用"空 params stub + side dict 存真 schema + 一个
`tool_search` 工具"即可零 core 改动落地，且 tools 数组全程恒定、不破
前缀缓存。未来若有插件作者需要，在 `docs/plugins.md` 写一节作者指南
即可，core 不提供任何专门支持。

### K18. `Tool.source`：来源由注册路径盖章，且重名冲突从静默覆盖改为报错

**现状**：Tool 没有任何来源字段（name/description/tags/summary_key/
result_key/params/func）；`ToolRegistry.register()` 重名时静默覆盖
（`tool.py:206-210`），谁注册的就永远丢失；插件 `build(ctx)` 期间宿主
不记录"当前是谁在注册"。

**建议**：

```python
# core/tool.py —— 一个字符串字段，无枚举、无类型层次
Tool(name=..., schema=..., func=..., source="")   # 默认 "" 仅用于裸 core 用法

# host/plugin/host.py —— 盖章点在注册路径，盖"通道前缀"的合格名，不许插件自报
# 插件 build() 执行期间，HostContext.tools.register 自动盖 source
```

**source 是带通道前缀的合格名**——盖章点知道通道（这是加载器的事实）：

| 值 | 谁 | 背书 |
|---|---|---|
| `builtin:shell` / `builtin:filesystem` | 仓库自带（包内 builtin 目录加载） | 加载路径 |
| `plugin:<name>` | 发现目录加载的第三方插件（loader 区分项目/用户目录时可细化 `project:`/`user:`） | 加载路径 |
| `host` | 宿主代码直接注册，无插件上下文 | 注册路径 |
| `""` | 裸 core 用法（测试、嵌入者直操作 registry） | — |

消费侧 `src.split(":", 1)[0]` 取通道、`split(":", 1)[1]` 取名。
**防欺骗**：第三方插件取名 `name="shell"` 也只会盖到 `plugin:<它的发现名>`
——类别信息由加载路径背书，抢不到 builtin 身份。一个字段装两段信息
是刻意的简洁取舍（显示/策略标签，非结构化数据），不做双字段。

**三条原则对照**：

- **简洁**：一个 `str` 字段，无并行注册结构；
- **通用**：消费者不定死——`/tools` 括注、审批配方按通道定信任
  （`startswith("builtin:")`）、cache-protect 通知分组、冲突报错、
  codemode 动态工具自标 `"codemode"`；
- **准确**：来源由注册路径盖章（注册点知道谁在注册、从哪加载；自报
  一定会说谎或抄错）；**重名冲突前移到注册时报错**——同 source 允许
  覆盖（插件重载自己的工具，codemode 热更新靠这个），异 source 抛
  `ToolConflictError`，显式 `register(tool, replace=True)` 可强抢。
  错误从运行时静默错位变成注册时爆炸。

**与 K2 的边界（两个正交 provenance 轴，同版本定义清楚）**：

| 轴 | 含义 | 何时定 | 在哪 |
|---|---|---|---|
| `source` | 工具**归谁**（静态归属，通道合格名） | 注册时盖章 | `Tool.source` |
| `origin` | 这次调用**谁发起的**（动态路径） | 每次调用 | K2 的 `ToolCallContext.origin` / 事件 |

`source` 不进事件：消费方要查就 `registry.get(name).source`——
事件保持瘦，注册表是状态，事件是事实流，各守本分。


---

## R 系列：重构项——API 面收敛与类型准确

这组与 K 系列不同：**不新增能力，只消除重复入口、双类型漂移和运行时才能发现的误用**。判断标准就是你定的那三条：简洁（一个概念一个入口）、通用（机制不绑死场景）、准确（错误尽量在类型/结构层面暴露，而不是运行时才炸）。

### R1. 两份执行策略类型手工同步：`AgentSettings` ↔ `AgentConfig`

**现状**：`AgentSettings`（`host/config.py:157-161`）和 `AgentConfig`（`core/agent.py:64-74`）是两个必须手动保持同步的 dataclass；`MoCode._agent_config()`（`host/runtime.py:260-265`）手工映射字段，而且只映射两个——`tool_result_limit` 在 config 里根本不可配。K8 再给 `AgentConfig` 加两个预算字段后，这份手工映射要继续人肉跟进，漏一个就是静默的默认值。

**建议**：删 `AgentSettings`。core 的 `AgentConfig` 是唯一策略类型，`Config.agent` 直接持有它，from_dict/to_dict 做嵌套（反）序列化。host 不再有自己的策略副本——`runtime.py` 的 `_agent_config()` 整个删掉。

**收获**：策略字段单一来源（准确）；config.json 的 `agent` 段自动获得 `tool_result_limit` 及 K8 新字段的可配性（简洁——零映射代码）。

### R2. `Tool.func` 的上下文注入靠签名探测，是隐式魔法

**现状**：工具要不要接收 `ToolCallContext` 由 `_accepts_context(func)`（`core/tool.py:53-64`）签名探测决定：数位置参数个数，或有没有 `*args`。

**问题**：探测规则有真实盲区——`(args, ctx=None)` 这种带默认值的第二参数**不**被识别（`POSITIONAL_OR_KEYWORD` 计数够但语义含糊时行为依赖细节），`(args, **kwargs)` 会被误判为想要 ctx。插件作者写错签名不会报错，只是静默拿到不同行为：该收到 ctx 的没收到，或函数被多传一个参数在运行时才炸。

**建议**：无兼容负担，改显式声明：

```python
Tool(..., func=fn)                        # 纯 (args) -> ...
Tool(..., func=fn, with_context=True)     # (args, ctx) -> ...，构造时校验签名
```

构造时校验（声明了 `with_context` 但签名不符 → 立即 `TypeError`），把两类错误都从运行时前移到 import/build 时。探测代码删除。

### R3. `HostContext` 的生命周期靠 RuntimeError 把守，应在类型上表达

**现状**：`ctx.emit()` / `ctx.subscribe()` 在 `build()` 期间调用会抛 `RuntimeError`（`host/plugin/context.py:107-140`）——用运行时错误约束一个编译期可知的事实：build 阶段 agent 不存在。插件作者最常犯的错就是在 build 里拿 ctx 做 call-time 的事。

**建议**：把阶段分裂写进类型（无兼容负担，`Plugin.build` 签名同步改）：

```python
@dataclass
class BuildContext:           # build() 收到：贡献目标 + 配置 + 路径，无 agent
    home, cwd, config, model, plugin_sources, register_provider_type
    tools, commands, hooks, prompt_sections, plugin_states, plugin_config(), plugin_state()

@dataclass
class HostContext(BuildContext):   # prepare()/close()/call time 收到
    agent: AgentLoop                  # 非空，emit/subscribe/derive 直接用
```

插件实例在 build 里保存 `HostContext` 做不到（拿到的只是 BuildContext）——保存一个"取上下文"的引用即可，或约定插件保存自己在 build 里创建的对象、call time 从工具参数拿（`ToolCallContext` 本就带 `emit`，多数场景已够）。配套审查：若 `emit/subscribe` 的真实 call-time 使用者很少，宁可删接口不留两个半空方法——一个入口原则。

### R4. 命令注册有两个等价入口，分发却是游离函数

**现状**：注册有 `ctx.register(*commands)`（`host/plugin/context.py:102-105`）和 `ctx.commands.register(...)` 两个入口做同一件事；分发却是模块级 `dispatch(text, *, conversation, commands)`（`host/command.py:108-127`），不挂在任何对象上——嵌入者要记住一个函数加两个参数。

**建议**：收敛为一个形态：

```python
CommandRegistry.dispatch(text, *, conversation) -> CommandResult   # 方法，取代游离函数
ctx.register(*commands) 删除，统一走 ctx.commands.register(...)   # 少一个等价入口
```

注册与分发同属一个 registry，API 面更小，心智模型一致。

### R5. `PluginHost` 的公开面比实际用途宽

**现状**：`reinstate()`（`host/plugin/host.py:185-194`）是 public 但唯一调用者是 `materialize()` 自己；`run()`（`host/plugin/host.py:236-239`）与 `build_all()+assemble()` 等价，是第二个装配入口；`_install` 作为"唯一写入点"的约束靠注释和命名维持。

**建议**：`reinstate` 私有化；删 `run()`（runtime 是唯一装配者，两个入口意味着两条测试路径）；`_install` 保持唯一写入点并在 docstring 写明"request surface 只有这一个写入点，改动必须过这里"。公开面 = `build_all / prepare_all / adopt_session / materialize / rebuild / assemble / close`，与 Conversation 暴露的刚好同宽。

### R6. 杂项修正（一次性扫掉）

- **`host/prompt.py:22`** TYPE_CHECKING 块里 `from .core.prompt import Section` 是错误路径（应为 `..core.prompt`），类型检查器会报；运行时因 `from __future__ import annotations` 不炸。修正。
- **`AGENTS.md` 加一节"新增 hook 点的清单"**：现在加一个拦截点要同步改 `AgentHook` 方法、`HookRunner` 包装方法、`mocode/plugins/__init__.py` 导出、文档——四个地方没有任何一处强制。一份清单让贡献者自查。
- **`Turn.__init__` 的 `asyncio.create_task`**（`core/turn.py:48`）意味着 `AgentLoop.start()` 只能在 running loop 中调用——合理，但写进 `Embedding` 文档的第一段，同步上下文的嵌入者少走弯路。

### R7. 内置插件改写成"无子类"形态，作为插件作者的第一范例

**现状**：`filesystem.py` 里每个工具是"三个模块级常量（`_READ_PARAMS`/`_READ_DESC`）+ 一个只为了实现 `func=self._execute` 的 `Tool` 子类"。能力没问题，但它是插件作者会照抄的第一个范例——照抄的代价是每写一个工具多一个类、元数据和逻辑分离在文件三处。

**建议**：内置插件统一改成 `Tool(...)` 直接实例化（README 的 git-status 示例已经是这个形态），元数据内联在构造调用里；需要 per-conversation 状态的工具（持有 `base` 路径）照旧用闭包或轻类，**不需要状态的绝不子类化**。同步做 R2 的 `with_context` 迁移。

**收获**：插件作者的第一段代码从 ~20 行缩到 ~8 行，且全仓库一个形态——范例即文档。

---

## S 系列：内置 shell 插件——后台任务设计

对标调研后的具体设计。不改内核，全部落在 `host/plugin/builtin/shell.py`
+ 一个插件生命周期小补丁。

### S0. 现状与缺口

现状 bash 工具（shell.py）：持久会话（cwd/env 逻辑持久、每条命令独立
subprocess）、超时真正杀进程、输出逐行走 `ToolOutput` 事件（call_id 寻址，
天然适配 TUI 的 open 块）。缺口：

1. **只有前台**：`execute()` 直接 `await proc.wait()`，模型在命令运行期间
   完全阻塞，做不了任何事；
2. **无句柄**：命令一旦在跑，模型永远失去它——不能查输出、不能等完成、
   不能终止；
3. **kill 漏进程组**：超时 `proc.kill()` 只杀直接子进程，
   `bash -c "npm run dev"` 的孙进程（node 等）泄漏。

### S1. 对标（2026）

- **Claude Code 三件套（事实标准）**：`Bash(run_in_background=true)` 立即
  返回 `bash_1` 式句柄；`BashOutput(bash_id, filter)` 只返回**自上次检查
  以来的增量**，filter 正则**消费**匹配行（读过即丢弃，防重复读、防
  context 爆炸）；`KillShell(bash_id)`；`/bashes` 交互管理；完成时向模型
  推送通知。源码级 trick 是常驻 shell 用 sentinel echo 判定命令结束——
  mocode 每条命令独立 subprocess 天然有结束边界，不需要 sentinel。
- **pi 生态（pi-patty-bg-tasks，更激进）**：前台命令超 120s **自动转后台**
  并问模型怎么办；`bash_bg` 跳过前台竞速；`jobs` 工具统一 list/search/
  attach；并发上限 16；多个任务同刻完成的通知**合并成一条摘要**；
  `monitor` 工具把 `tail -f` 式流源的每行变成一个通知。
- **核心 pi 本身**：bash 纯前台（后台是社区包补的）——后台任务是公认缺口，
  不是锦上添花。

### S2. mocode 设计：三件套 + 分级超时

```python
bash(command, restart, timeout, run_in_background=False)
    # 后台：立即返回 {"shell_id": "shell_3"}，无 exit_code detail
bash_output(shell_id, filter=None, wait=False, timeout=None)
    # 增量输出（消费式游标）+ status(running|completed|killed|timed_out) + exit_code
    # wait=True：asyncio.Event 阻塞到完成或 timeout —— "等待完成"的原语，禁止 sleep 轮询
kill_shell(shell_id)
```

**Job 结构**（挂在 BashTool 实例上，天然 per-conversation）：

```python
@dataclass
class _Job:
    id: str
    command: str
    proc: asyncio.subprocess.Process   # start_new_session=True（进程组）
    collector: asyncio.Task            # 行收集协程
    buf_out / buf_err: deque[str]      # 有界 ring：各 2000 行 / 256KB，
                                       # 溢出丢最旧 + 记 "…(N earlier lines discarded)"
    consumed: int                      # bash_output 游标，只返回增量
    done: asyncio.Event
    exit_code: int | None
```

**关键决策**：

| 决策 | 选择 | 理由 |
|---|---|---|
| 输出拉取 | 增量游标；filter 消费式（不匹配的行丢弃） | 对齐 Claude Code，防 context 爆炸 |
| 内存 | ring buffer 有界 | dev server 过夜不撑爆 |
| 前台超时 | 沿用 tool_timeout（240s），超时 **killpg 进程组** | 顺手修孙进程泄漏 |
| 后台超时 | 默认无（长任务是用途）；显式 timeout 可设；配置硬上限（默认 3600s） | 分级语义 |
| 并发 | 上限可配（默认 16，抄 pi-patty） | 防 fork 炸弹 |
| 生命周期 | restart / 会话结束 → 杀全部后台任务 | 需要 S3 的 teardown 钩子 |
| 完成通知 | 任务完成且 turn 已结束 → `emit_message` 追加跟随块（`✓ shell_3 completed, exit 0`）；同刻多任务合并一条 | 复用 CLI-TUI 文档 §3.5 的正式出路 |
| turn 内输出 | 启动后的早期输出走 `ToolOutput`（call_id 寻址）进 open 块；块提交后静默累积进 ring buffer | 块已 committed 不重画 |
| 会话语义 | 后台任务启动时快照 cwd/env；运行中的 cd/export 不影响它（与前台一致） | 逻辑持久模型不变 |
| 审批 | 三工具同 `tags={"shell"}`，现有配方自然覆盖 | 无特殊化 |

**TUI 联动（CLI-TUI 文档 §4.1 的用例落地）**：工具层暴露
`BashSession.background(call_id)` 把运行中命令转后台；运行期键位
**Ctrl+B** 注册为它的调用——Claude Code / pi-patty 同款交互。
这是"运行期键位 + 改变工作执行流程"插件 API 的第一个真实用户。

### S3. 配套：插件生命周期补 teardown

host 侧 `Plugin` 协议目前只有 `build()`，后台任务（以及未来 codemode 的
进程池）无处清理。补可选钩子：

```python
class Plugin:
    def build(self, ctx: HostContext) -> None: ...
    async def teardown(self) -> None: ...   # 新增，可选；会话结束/卸载时调用
```

`HostContext.on_close(fn)` 作为注册式等价物（与 CLI 文档 CLIContext
的 `on_close` 对称）。Conversation 关闭路径上依次调用，异常隔离
（一个插件的 teardown 失败不阻断其余）。

---

## 附录 A：汇总——目标插件 API 形态

以上落地后，codemode 插件的形态（对比现状要复刻 `_execute_tool`、解析 call_id 字符串、手写 wait_for、与 cache-protect 抢 freeze）：

```python
from mocode.plugins import Plugin, Tool, Section, ToolPolicy

class CodeModePlugin(Plugin):
    name = "codemode"

    def build(self, ctx):
        ctx.tools.register(Tool(
            name="codemode",
            schema={...},                          # K3：真 JSON Schema
            func=self._run(ctx),                   # 闭包持有 per-conversation ctx
            policy=ToolPolicy(timeout=600),        # K5
        ))
        ctx.prompt_sections.append(
            Section("tools-sdk", render=self._sdk, pinned=True))  # K10

    def _run(self, ctx):
        async def execute(args, call_ctx):
            bridge = ctx.dispatcher(               # K1：公共分发器
                origin="program",                  # K2：provenance
                parent=call_ctx.tool_call_id,
            )
            # 审批/超时/截断/事件与直连完全同源
            ...
        return execute
```

## 附录 B：破坏性触点清单（按文件）

| 文件 | 破坏点 | 对应条目 |
|---|---|---|
| `core/tool.py` | `Tool(params=...)` 删除，改 `schema=` + `returns=` + `availability` + `policy` + `source`；`names()/all_schemas()/select()` 加 audience；`register()` 重名异 source 抛 `ToolConflictError`，加 `replace=True` | K3, K4, K5, K18 |
| `core/hook.py` | `ToolCallContext` 加 `origin/parent_call_id`；`AgentHook` 加 `before_request/after_response` | K2, K6 |
| `core/events.py` | `ToolCallStarted/Finished` 加 `origin/parent_call_id`；`RunFinished` 加 `stop_reason`，删 `cancelled` | K2, K7, K8 |
| `core/agent.py` | 删 `_run_tool/_execute_tool/_one`；`AgentConfig` 加 `max_tool_calls/max_turn_seconds`；`chat()` 撞上限改抛 `IterationLimit` | K1, K8 |
| `core/provider.py` | `Provider` 协议加 `retry_policy`；`RetryPolicy` 取代模块常量；`with_retry_stream` 收 policy 参数 | K13 |
| `core/dispatch.py` | 新增：公共分发器 | K1, K2, K5 |
| `host/plugin/context.py` | `ctx.emit` 自动盖 run_id；加 `spawn()` | K9, K11 |
| `host/plugin/builtin/shell.py` | bash 的 timeout 参数语义迁到 ToolPolicy | K5 |
| `host/config.py` | 删 `AgentSettings`，`Config.agent` 直接持有 `AgentConfig` 并嵌套（反）序列化 | R1 |
| `host/runtime.py` | 删 `_agent_config()` 手工映射 | R1 |
| `host/command.py` | 模块级 `dispatch()` 收为 `CommandRegistry.dispatch` 方法 | R4 |
| `host/plugin/context.py` | 删 `ctx.register`；`BuildContext`/`HostContext` 阶段分裂（如采纳 R3） | R3, R4 |
| `host/plugin/host.py` | `reinstate` 私有化，删 `run()` | R5 |
| `host/prompt.py` | 修正 TYPE_CHECKING 错误 import | R6 |
| `core/tool.py` | 删 `_accepts_context` 签名探测，改 `with_context` 显式声明 | R2 |
| `core/turn.py` | 无代码改动：文档注明 start() 需要 running loop | R6 |
| `host/plugin/loader.py` | 加 `code_entry()` 统一入口判定；`import_module_file` 支持包（`submodule_search_locations`）；入口缺失/包无 `__init__.py` 时 report 提示 | K16 |
| `mocode/cli/plugin.py` | `load_cli_plugins` 改用共享的 `code_entry()` | K16 |
| `examples/plugins/` | 新增多文件插件范例 | K16 |
| `providers/openai.py` | 实现 `retry_policy` | K13 |
| `mocode/testing/` | 新增：MockProvider 等测试工具公共化 | K12 |
| `mocode/plugins/__init__.py` | 导出面同步（含 BuildContext、ToolPolicy、RetryPolicy） | 全部 |
| 全部内置插件 | params 改 schema 格式；无子类化改写；build 签名随 R3 | K3, R7 |

## 附录 C：落地顺序

| 版本 | 内容 | 理由 |
|---|---|---|
| v0.2（P0） | K1 dispatcher、K2 provenance、K4 availability、K9 归属语义、K18 source 字段 | codemode 硬前置；改动集中在 core/，测试惯例现成（照 `tests/test_agent_loop.py`）；K18 搭车——与 K2 同版本定义两个 provenance 轴，改动极小 |
| v0.3（P1） | K3 JSON Schema、K5 策略覆盖、K6 请求拦截、K7 stop_reason、K8 预算、K13 重试策略化、K16 多文件插件、R1 策略类型合并、R2 with_context | K8/R1 必须同版本（同一类型）；R2 随 K3 一起做（同一文件）；K16 独立，可先合（codemode 需要） |
| v0.4（P2） | K10-K12、K15、R3 上下文阶段分裂、R4 命令 API 收敛、R5 host 面收窄、R6 杂项、R7 内置插件范例化 | R3 影响所有内置插件的 build 签名，单独一个版本好审 |

R 系列原则备忘：**不加能力，只删重复入口、双类型和运行时才能发现的误用**——每条落地后 SDK 表面积只会变小。

每个改动同步更新：`docs/ARCHITECTURE.md` 对应章节、`AGENTS.md` 分层规则（如有新增）、`TODO.md`（K6 完成后从 TODO 划掉）、`docs/plugins.md` 插件作者文档。

---

*原 K16（插件清单 SDK 版本协商）已移除：无向后兼容约束则无需版本协商机制。*
