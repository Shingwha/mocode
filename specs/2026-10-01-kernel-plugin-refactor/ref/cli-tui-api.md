# MoCode CLI 重构：交互式 TUI 与 CLI 插件 API 设计

> 独立讨论文档。主题：把当前的追加式打印 CLI 重构为 pi / Claude Code 风格的交互式 TUI，
> 并在此过程中定义一套稳定、窄面、可扩展的 CLI 插件 API。
> 与《内核与插件 API 优化建议》相互独立，但共享同一批设计原则；文末说明交集。
>
> 所有现状引注均为 `Shingwha/mocode` master 的文件与行号。

---

## 0. 现状审计：CLI 现在长什么样

### 0.1 渲染模型——"打印完就忘"

```
事件流 → CLIRenderer.draw(event) → Display（直接写终端）
```

- **输出是追加式的**（`cli/display.py` 模块 docstring，10-16 行）：除了工具调用块之外，
  所有输出打印后即被遗忘，屏幕上没有任何"会话文档模型"。
- **唯一的可重写区域**是正在运行的工具调用块：`Display.place/rewrite`（display.py:96-98）
  申请一行 dim 占位符、结束时原地改写成结论。约束很硬：块内每行被夹紧为单行高
  （`clamp_visible`，display.py:57-86），任何其他输出出现就永久冻结该块。
- `CLIRenderer` 是纯消费者，不拦任何东西（render.py:1-13），事件→行的映射是
  `draw()` 里的一个**封闭 match**（render.py:53-98）；不认识的事件落到 `case _:` 用
  `event.summary()` 打印（render.py:94-98）——插件的自定义事件"能显示但显示得丑"。
- 跨 turn 的历史重放存在但只服务于 `/resume`：`L.conversation(messages, tools)`
  从 messages 重建行（render.py:78-86）。也就是说"从数据重建屏幕"的能力已经存在，
  只是没有被泛化成常驻模型。

### 0.2 输入层——prompt_toolkit 裸用

- `Input`（cli/input.py）= PromptSession + 粘贴存储 + 硬编码键位 + 斜杠补全。
- 键位在构造时写死，插件无法添加；补全只覆盖 `/` 命令名。
- Ctrl-C 由 app 层信号处理接管（app.py:142-153），空闲与运行中语义不同但不可定制。

### 0.3 插件面——一个 `build(cli)` 打天下

```python
class CLIPlugin:
    name: str = ""
    description: str = ""
    def build(self, cli: CLIApp) -> None: ...
```

- `cli/plugin.py:40-47`。插件拿到的是整个 `CLIApp`，能碰到什么取决于
  `CLIApp` 恰好暴露了哪些属性（`cli.commands / cli.display / cli.input /
  cli.conversation / cli.renderer`）——**偶然 API，不是设计**。
- 文档（cli/plugin.py:1-21）说插件"can reach cli.commands, cli.display, cli.input
  and cli.conversation"，这四个对象的内部实现细节（Display 的 block 机制、
  Input 的 prompt_toolkit 细节）就此全部变成事实上的兼容负担。
- 对话框存在（`dialogs.select`，返回 None when not a TTY）但只在 REPL 空闲时可用；
  **运行期间没有 UI 交互通道**——而审批流、确认对话框恰恰最需要运行期间可用。

### 0.4 结论

当前架构对"一次性打印"是合适的，而且其中有两个设计非常好、TUI 化必须保留：

1. **行即数据**（`lines.py`）——渲染逻辑不依赖终端即可测试；
2. **前端只是事件的读者**——CLI 不做任何拦截，与 web 前端同权。

TUI 化要做的不是推翻，而是把"渲染器直接写屏幕"中间插进一层**文档模型**，
并把插件从"整个 CLIApp"收窄到一个**明确的上下文对象**。

---

## 1. 设计原则

1. **模型与绘制分离**：事件先折叠进 Transcript（会话文档模型），再由 Painter
   把模型投影到终端。任何"重画"（resize、resume、主题切换、折叠展开）都是对同一模型
   的重投影，不产生新语义。
2. **前端仍是读者**：TUI 不拦截事件流、不改执行语义；审批这类"改变流程"的能力
   通过 host hook（策略层）+ CLI 对话框（表现层）的组合提供，而不是给 CLI 开私有通道。
3. **无 TTY 降级是永远有效的承诺**：管道模式（`isatty` 开关已存在于
   display.py:118）行为完全不变——Transcript 模型照常工作，Painter 换成纯追加输出。
4. **插件 API 窄而稳**：TUI 重构会搅动 CLIApp 内部，插件只依赖一个稳定的
   `CLIContext` 对象；内部怎么重写都与插件无关。
5. **不加能力到内核**：CLI 的一切扩展都是前端领土，host/core 一行不改。

---

## 2. 核心重构：Transcript——会话的文档模型

### 2.1 为什么必须加这一层

TUI 和追加式打印的本质区别：追加式只需要知道"下一行写什么"；TUI 需要随时回答
"屏幕上现在应该是什么"——工具块要展开收起、流式文本要重排、resize 后要重画、
resume 后要重建。没有模型，这些问题就只能用越来越多的"当前屏幕状态"补丁来解决，
而状态补丁正是 `Display._block`（display.py:120）已经在用的手法——它只够管一个块，
管不住一整屏。

### 2.2 形态

```python
# cli/transcript.py —— 事件流折叠成的会话文档，与 RunState 同构但面向展示
@dataclass
class Block:
    id: str                 # 稳定 id；工具块的 call_id、流式块用 run_id+序号
    kind: str               # "user" | "answer" | "reasoning" | "tool" | "notice" | "rule" | ...
    lines: list[L.Line]     # 行即数据：继续复用 lines.py 的词汇
    state: str = "done"     # "streaming" | "running" | "done" | "collapsed"
    meta: dict = field(default_factory=dict)   # call_id、usage、error、可展开内容

class Transcript:
    def __init__(self): self.blocks: list[Block] = ...

    def apply(self, event: Event) -> None:
        """事件 → 模型更新。与 RunState.apply 同一手法：
        TextDelta 追加进当前流式块；ToolCallStarted 开一个 running 块；
        ToolCallFinished 把它落定；RunFinished 关块加 rule。"""
```

- `apply` 是**纯折叠**：给定相同事件流必然得到相同 Transcript——无终端可测，
  测试断言从"输出了什么字节"升级为"模型里有什么块"。
- 现有的跨 turn 重放（`L.conversation`）变成特例：`Transcript.apply_history(messages)`。

### 2.3 现有代码的迁移量

`CLIRenderer.draw()` 的 match 分支几乎原样保留，只是每个分支从
"`self._d.stream/place/render`" 变成 "`self._t.block(...).append/close(...)`"。
`Display.place/rewrite/_block` 整套机制从 Display 删除，由 Painter 接管。
这是本次重构中最大的一块删除——值得做。

---

## 3. Painter：把模型画到终端

### 3.1 职责

```python
# cli/painter.py
class Painter:
    """Transcript → 终端。唯一懂 ANSI 光标移动的组件。"""

    def repaint(self, *, from_block: int | None = None) -> None:
        """把 from_block 起的块重画到终端尾部。None = 全量。"""
```

- 维护一个锚点：**最后一个"已提交"行**（用户输入行之下、当前脏区之上）。
- 脏区 = 锚点之后的所有行。重画 = 光标上移 N 行 → 清到行尾 → 逐块输出。
  这正是 `place/rewrite` 已有机制的一般化：从"一个夹紧单行的块"变成
  "任意高度、任意多个块"。
- 流式块：每个 delta 触发该块重画，**节流**（如 30ms 合并一次）避免高频抖动；
  块的行数可以增长——这解决了现状流式文本不能重排的根本限制。
- 工具块：多行渲染成为可能（名称+参数一行、输出可展开、结论高亮），
  `collapsed` 态就是那行熟悉的 `✓ bash find ... · exit=0 · 0.1s`。

### 3.2 与输入层的协调

prompt_toolkit 的 PromptSession 假定它拥有光标所在行。两个方案：

- **方案 A（推荐）**：升级为 prompt_toolkit `Application` + 自定义 Layout——
  上方一个只读 `FormattedTextControl`（渲染 Transcript 投影，自绘 ANSI），
  底部输入框 + 状态栏。Application 的 invalidate/redraw 与 Painter 的脏区重画
  由同一"模型变了"事件驱动。输入继续吃 prompt_toolkit 的补全、键位、多行编辑红利。
- **方案 B**：连输入也自绘（pi 的路线）。完全掌控但要自己实现补全、
  多行编辑、粘贴、终端兼容——工作量数倍，不建议第一版走这条。

### 3.3 无 TTY 路径

`Painter` 一分为二：TTY 用脏区重画；非 TTY 用纯追加（每个块落成时输出一次，
流式块按行输出）。选择逻辑就是现有的 `live` 开关。管道模式的行为承诺不变。

### 3.4 渲染栈选型：库与自己写的边界（调研结论）

**生态事实**（2026 年调研）：Python 没有 pi-tui / ink / Silvery 那样的
"内联会话 TUI"成品库——那些都在 TS/JS 世界。Python 侧成熟选项只有两类：
textual（完整 widget 框架，默认 alt-screen 接管视口）和 prompt_toolkit + rich。

**选型：prompt_toolkit Application + rich，拒绝 textual，不全自绘。** 依据：

- **路线之争已经分出胜负**。终端 TUI 只有两条路线：全屏视口（接管屏幕、
  自管滚动）和 CLI 式内联（追加为主、只回画活动区域）。pi 作者自己的总结：
  Claude Code、Codex、Droid 都走内联路线——保留终端原生滚动、搜索、选择。
- **textual 的代价与 agent CLI 不匹配**：alt-screen 意味着退出即失历史、
  终端的滚动/搜索/跨屏选择全部失效；widget 框架的重量对一个"99% 是文本流"
  的应用是过度投资。连全屏 TUI 项目都在为自管滚动缓冲开 RFC，这条路不 shallow。
- **aider 验证过目标组合**：prompt_toolkit + rich 跑在普通终端缓冲上，
  是生产级 agent CLI 的 proven 架构——输入、补全、键位、markdown、diff、
  语法高亮全部现成。
- **rich 与 prompt_toolkit 的已知不兼容有成熟解法**：rich 产 ANSI 字符串，
  prompt_toolkit 用自己的 formatted-text 体系；中间包一层
  `prompt_toolkit.formatted_text.ANSI()` 即可进 FormattedTextControl。
  rich 设为可选依赖，非 TTY 路径不装不用。

**手写量评估**——真正要自己写的只有一层，且被两条原则继续压缩：

| 层 | 归属 | 估计 |
|---|---|---|
| 输入编辑 / 补全 / 键位 / 状态栏 | prompt_toolkit（已有依赖） | 0 行手写 |
| markdown / diff / 语法高亮 / 行宽测量 | rich（可选依赖，TTY 时启用） | 0 行手写 |
| Transcript（纯数据文档模型） | 自写 | ~150 行，无终端可测 |
| 投影函数 + 本 turn 实时区刷新 | 自写 | ~200-300 行，复用 lines.py 词汇 |

第一条压缩原则来自 koda 的 TUI 重构："正常终端优先——输出是 println、
滚动缓冲归终端；**内联视口只装控件**（输入 + 状态栏 + 审批），不装输出"。
落到本架构：已提交历史（上一条用户消息之前的一切）继续纯 print 进原生
滚动缓冲，**可重画区域缩小到"本 turn"**——流式回答、工具块、spinner、对话框。
这恰好是 Claude Code 的模型，也是 `Display._block` 现有机制的自然泛化：
最难的"历史区光标数学"根本不需要存在，脏区永远是"当前轮的实时区"。

第二条原则来自 pi 的组件纪律：组件 = 给定宽度产出一组行的纯函数，
渲染请求合并（coalesce），昂贵布局按宽度缓存。mocode 已有的
"行即数据"（lines.py）就是同一思想的 Python 形态——Transcript.apply 是纯折叠，
投影是纯函数，Application 的 invalidate 只做合并调度。Claude Code 抛弃 ink
自研渲染器但保留 React 组件模型的先例说明：**组件化思维重要，渲染库不重要**。

**据此修正 3.1 的架构**：Painter 不维护"全量脏区锚点"，只维护
"本 turn 实时区"——一个 FormattedTextControl 的内容 = 投影(Transcript 尾部
的未完成块)，事件到达即 invalidate（合并），turn 结束（RunFinished）时
把成品块纯 print 提交进滚动缓冲并清空实时区。历史永不重画，
折叠/展开历史块之类的功能（需要重写历史）明确列为不做，
或作为 T3 之后用"已提交区也可重画"的增强单独评估。

### 3.5 块的生命周期：open → sealed → committed

"修改已渲染的消息"分两种情形，答案相反，靠三态生命周期把两者都管住：

| 态 | 位置 | 可否更新 | 转移条件 |
|---|---|---|---|
| open | 实时区 | **可**——每次状态变化 invalidate 重画 | `ToolCallFinished` / 插件 `seal()` / turn 结束统一 seal |
| sealed | 实时区（待提交） | 否 | RunFinished 时提交；T2 起 sealed 且静默超 N 秒可**提前提交** |
| committed | 原生滚动缓冲 | **永不**——终端缓冲视为不可变 | 终态 |

**open 块的寻址方式**：后续事件靠标识路由回已有块，而不是新建——
内置事件用 K2 的 `parent_call_id`（codemode 的 331 次子调用事件实时折叠进
父块，父块显示"已执行 87/331"，脚本无需自报进度）；插件消息用 `block_id`
参数（§5.5）。

**sealed 后同标识再来更新**（必须定死的策略）：默认**追加跟随块**
（`✓ 索引完成`，接在原文之后，append-only，管道模式天然兼容），
或按插件配置丢弃并记 debug 日志。不存在"回头改"的路径——
那是全视口 TUI（amp/opencode）的路线，本架构已否决。

这个机制的第一个真实用户是 shell 后台任务：任务启动的块在 turn 结束时
seal 提交（标记"仍在后台运行"），任务完成时追加
`✓ shell_3 completed, exit 0` 跟随块——跨 turn 的"消息修改"不需要
重画历史。多个任务同刻完成时合并成一条摘要（pi-patty 的 coalesce 做法）。
详见内核文档 S 系列。

"工具调用持续在更新"是这套机制的基本盘而非特例：tool 块从
`ToolCallStarted`（spinner + 参数摘要）到 `ToolCallFinished`（定稿）
全程是 open 块的状态迁移；流式 assistant 块逐 token 更新同理，
只是 `Display._block` 单行 spinner 的一般化。

---

## 4. 输入层升级与"改变执行流程"

### 4.1 键位注册 API

```python
# 插件向输入层注册键位
ctx.keys.add("c-g", handler, description="打开补丁选择器")
# handler 收到 InputContext：当前 buffer、transcript 只读视图、ui 对话框入口

# "运行期键位 + 改变工作执行流程"的第一个真实用户（内核文档 S 系列）：
# 命令跑着按 Ctrl+B 当场转后台——handler 调 BashSession.background(call_id)
ctx.keys.add("c-b", background_running_command, when="running",
             description="将正在运行的命令转入后台")
```

- 运行期间按下的键位路由到"运行期处理器"（如 Esc 中断 turn），
  空闲期路由到"编辑期处理器"（如 c-g 打开选择器）。输入层负责按状态分发。
- 中断语义升级：空闲 Ctrl-C = 清输入（现状是退出 REPL，改为 Claude Code 语义）；
  运行中 Ctrl-C / Esc = `turn.cancel()`。空输入再按一次 Ctrl-C 才退出。

### 4.2 输入中间件链

```python
ctx.input.use(expand_mentions)     # @path → 文件内容引用
ctx.input.use(expand_shorthands)   # !git → 运行 git status 并注入结果
```

`text → text` 的纯函数链，在 dispatch 之前运行，可测试、可组合。

### 4.3 运行期 UI 通道——审批流的最后一块拼图

现状 `dialogs.select` 只在 REPL 空闲时可用；运行期间没有任何向用户提问的通道。
TUI 的脏区重画天然解决了这个（对话框是一个临时块，画在脏区顶端）：

```python
class CLIContext:
    ui: UI    # 运行期同样可用

class UI:
    async def confirm(self, message: str, *, danger: bool = False) -> bool: ...
    async def select(self, title: str, options: list[Option]) -> Option | None: ...
    async def input(self, message: str, *, default: str = "") -> str | None: ...
```

**审批流配方**（这就是"改变工作执行流程"的标准做法）：

```python
# mocode/plugin.py —— 策略在 host hook（每个前端都生效）
class ApprovalHook(AgentHook):
    async def on_tool_start(self, ctx: ToolCallContext) -> None:
        if ctx.origin == "program" or ctx.tool_name in SAFE:   # K2 的 origin 字段
            return
        # K18：按通道定信任——builtin 免审，第三方插件逐个问
        tool = self._tools.get(ctx.tool_name)
        channel = (tool.source.split(":", 1)[0] if tool else "")
        if channel == "builtin":
            return
        if not policy.needs_approval(ctx.tool_name, ctx.tool_args):
            return
        ok = await self._ui.confirm(f"[{src or '?'}] 允许 {ctx.tool_name}？", danger=True)  # 前端特定
        if not ok:
            ctx.deny = "user declined"

# mocode.cli/plugin.py —— 表现层在 CLI 命名空间（只有终端能问）
class ApprovalUIPlugin(CLIPlugin):
    def build(self, ctx: CLIContext) -> None:
        approval_plugin.ui = ctx.ui      # 把 UI 通道递给 host 侧的 hook
```

分工的关键：**策略（要不要拦）是 host 领土，任何前端都生效；提问（怎么问）是
CLI 领土**。web 前端可以同样实现 `ui.confirm` 用页面弹窗——同一个 hook 不用改。
pi-codemode 的 `on/off/yolo` 分级、Claude Code 的批准模式，都是这个配方的变体。

---

## 5. CLI 插件 API：CLIContext

### 5.1 形态

```python
class CLIContext:
    """CLIPlugin.build 收到的唯一参数。窄面、稳定；
    内部对象（CLIApp/Display/Input）一概不可达。"""

    # 命令（收编现有能力）
    commands: CommandRegistry                  # register/unregister/dispatch

    # 键位与输入
    keys: KeyRegistry                          # add(key, handler, description=)
    input: InputMiddleware                     # use(fn)

    # 渲染扩展
    drawers: DrawerRegistry                    # register(event_type, fn)
    theme: Theme                               # 注册样式名 → style

    # UI 通道（运行期可用）
    ui: UI            # confirm/select/input（§4.3）+ notify/message/set_status/is_interactive

    # 状态栏与头部（贡献点，§5.6）
    status: StatusRegistry                     # use(fn: StatusState -> Segment | None)
    header: HeaderRegistry                    # use(fn) / set(lines) —— 输入框上方区域

    # 会话只读视图（插件要知道自己在哪）
    conversation: Conversation                 # 现有对象，只读使用

    # 生命周期
    def on_close(self, fn) -> None             # 插件资源释放
```

### 5.2 自定义事件渲染：drawers

现状未知事件只能打印 `summary()`。注册制后：

```python
# mocode.cli/plugin.py
def draw_frustration(event: FrustrationScored) -> list[L.Line]:
    return [L.tool_close_style(f"情绪判定: {event.choice} ({event.prob})")]

ctx.drawers.register(FrustrationScored, draw_frustration)
```

`CLIRenderer` 从封闭 match 变为查表分发，内置事件是预注册的那批——
同一个机制，没有第二条路。这也让 host 侧 K2/K9 的 `origin/parent_call_id`
字段在 TUI 上直接可用：codemode 的 331 次子调用折叠成父块下的
`... (331 earlier calls)` 就是 drawer + 块折叠的合奏。

### 5.3 CLIPlugin 签名变更

```python
class CLIPlugin:
    def build(self, ctx: CLIContext) -> None: ...   # 从 build(cli: CLIApp) 改过来
```

无向后兼容负担（见内核文档约定），旧的内置 `BuiltinCommands` 随之迁移。
`build_cli_plugins`（cli/plugin.py:91-99）的错误隔离语义不变。

### 5.4 插件的 UI 地位：从打印者到文档公民

重构前 CLI 插件实际只是"打印者"——往 stdout 塞字符。Transcript 落地后，
插件分三层公民身份：

- **观察者**：`drawers.register(event_type, fn)`，未知事件不再落到 `summary()`。
- **作者**：`ctx.emit_message(kind, data)`（host 层 API）产生一等 `PluginMessage`
  事件——进事件通道、随 session 持久化、重启可回放；CLI 插件用
  `drawers.register(kind, fn)` 渲染它。**数据归 host（每个前端都拿得到），
  渲染归 CLI**——与 §4.3 审批流同一个分工公式。pi 的
  `appendEntry + registerEntryRenderer` 就是这对组合；mocode 因事件通道
  本来落盘，持久化白拿。
- **居民**：住在界面里——底栏贡献段、输入框上方头部区、键位、运行期对话框、
  （T3 之后的）实时区组件挂载。

### 5.5 自定义消息 API

```python
# host 层（mocode/plugin.py，每个前端生效，session 可回放）
ctx.emit_message("codemode/summary", {"rounds": 3, "tools": 331})
# 可持续更新的块（§3.5 的 open 态）：同 block_id 的后续消息更新同一块
ctx.emit_message("rag/index", {"done": 12, "total": 40}, block_id="rag-1")
ctx.emit_message("rag/index", {"done": 40, "total": 40}, block_id="rag-1")
ctx.seal_message("rag-1")   # 定稿；晚到的同 block_id 更新走"追加跟随块"策略

# CLI 层（mocode.cli/plugin.py，只负责表现）
ctx.drawers.register("codemode/summary", draw_summary)
ctx.ui.message("纯文本快捷块")        # 等效 emit_message(kind="text") + 内置 drawer
```

`PluginMessage` 事件进 Transcript 时折叠成一个块（kind 前缀 `plugin/` 之外的
命名空间约定为 `<插件名>/<类型>`）。`block_id` 缺省时每次 emit 都是新块；
携带时路由到 open 块。drawer 允许 `override=True` **显式覆盖
内置 kind**——"换肤"类插件（重画 tool 块、markdown 加边框）与自定义块走
同一个机制，没有第二条路。非 TTY 路径：无实时区，每次更新直接 append
输出，行为承诺不变。

### 5.6 底栏与头部：贡献点模型

底栏不是占位符而是**合并器**：每次重画跑全部已注册 provider，
各产出 `Segment | None`，合并器负责分隔符、优先级、超宽截断。

```python
@dataclass
class StatusState:
    model: str; cwd: Path; running: bool
    usage: Usage | None; pending_approvals: int

ctx.status.use(lambda s: Segment(f"[{s.model}]", priority=10) if s.running else None)
ctx.ui.set_status("索引中…")           # 瞬时覆盖（pi 的 setStatus），None 清除
ctx.header.set([ascii_art_lines])      # 输入框上方区域（pi 的 setHeader）
```

插件的地位是"与其他段平级共存"，不是抢到整行。`header`/`set_status` 是
"会话级装饰"，常驻内容仍走 `status.use` 贡献点。

### 5.7 降级契约

`ui.is_interactive`（pi 的 `ctx.hasUI` 对应物）是插件写降级逻辑的唯一开关：

| 能力 | TTY | 非 TTY |
|---|---|---|
| `ui.notify` | 实时区 toast | stderr 一行 |
| `ui.message` / `emit_message` 的块 | drawer 渲染 | 纯 stdout 文本 |
| `status` / `header` / `set_status` | 正常 | 忽略 |
| `ui.confirm/select/input` | 对话框 | **不可用** |

最后一行的推论必须写进文档：**host 侧策略插件查 `is_interactive`，
管道模式下审批无处可弹，策略回退到配置默认值**（allow/deny 由插件配置决定）。
这个检查放在 host 层做，CLI 层只负责呈现——又一个"策略 host、表现 CLI"实例。

### 5.8 保留的逃生口

刻意**不**提供 `ctx.app` / `ctx.display` 这类直通内部的对象，也不在第一版
开放 `ctx.ui.custom()` 级别的全功能组件挂载（T3 之后以"实时区 +
FormattedTextControl 插槽"的形式评估，接口位置预留、不承诺）。插件真正需要
底层能力时，正确路径是把能力提进 CLIContext，而不是开洞——开洞一天，
TUI 重构的自由就没了。

---

## 6. TUI 特性路线图（向 pi / Claude Code 对齐的顺序）

| 阶段 | 内容 | 视觉变化 | 主要删除 |
|---|---|---|---|
| **T1** | Transcript + Painter 落地，逐块替换现有输出，**逐像素保持现状外观**（已提交区纯 print + 本 turn 实时区重画） | 无 | `Display._block/place/rewrite/clamp_visible` 整块 |
| **T2** | 脏区重画泛化：工具块多行、可展开（含 ToolOutput）、流式块重排、spinner（thinking 态）、底部状态栏（model / tokens / cwd，贡献点制） | 有 | 单行夹紧的消失 |
| **T3** | 插件 API 全量（§5）+ Markdown 流式渲染（代码块边框、标题弱化）+ 命令菜单（registry 驱动的选择器）+ diff 视图（drawer 范例） | 有 | —— |

**Markdown 流式渲染**单独说一句：不要上完整 markdown 解析器重排——流式重画的成本
与块高成正比，而回答块正是最高的块。pi 的做法是轻量渲染（代码块加边框、
reasoning 降权），建议照抄：流式期间只做围栏代码块的检测与着色，块落定后可选完整渲染。

**明确不做的**：

- **不进 alternate screen**——pi 和 Claude Code 都在普通屏幕内联重绘，
  保留终端原生滚动和复制体验；
- **不引入 textual 级重依赖**——prompt_toolkit 已在依赖里，Application + Layout
  足够；cli 层允许有依赖，但每个依赖都要对得起安装成本；
- **不接管终端滚动条**——脏区之上的一切是终端的，不是我们的。

---

## 7. 风险与对策

1. **prompt_toolkit Application 与 ANSI 自绘的冲突**：Application 的光标管理
   会和我们上移清行的逻辑打架。对策：T1 阶段 Painter 输出完全走
   `print_formatted_text`（prompt_toolkit 的 above-prompt 输出原语），
   不自移光标；T2 再切 Layout。T1 的"零视觉变化"承诺把风险锁在第一阶段。
2. **性能**：长会话 Transcript 持续增长，全量重画不可接受。
   对策：repaint 永远带 `from_block`；流式重画只碰当前块；resize 才全量。
   超大会话（数千块）的虚拟化是 T3 之后的优化点，先不设计。
3. **测试惯例**：Transcript 是纯数据——`tests/test_display.py` 的
   "无终端整 turn 渲染"测试模式原样升级为 transcript 断言；
   Painter 的 ANSI 输出对 golden file 断言少量样例即可。
4. **插件先行陷阱**：CLIContext 应该在 T1 就定义（哪怕只有 commands/drawers/
   ui 三个成员），否则 T2/T3 会反复改插件签名——先定契约，再换引擎。

---

## 8. 与内核文档的关系

- **无硬依赖**：本文全部改动在 `cli/` 层，core/host 一行不动。
- **软依赖（可选）**：K2 的 `origin/parent_call_id`、K9 的 run_id 归属
  让嵌套调用折叠和审批流的"程序内调用默认放行"判断更干净——有了更好，
  没有也能做（按 call_id 前缀约定过渡）。
- **排期建议**：挂在内核文档 v0.4 之后作为独立的 cli 重构线推进；
  T1（Transcript+Painter+CLIContext 骨架）是整个线的地基，单独一个版本，
  视觉零变化是它的合入保证。
