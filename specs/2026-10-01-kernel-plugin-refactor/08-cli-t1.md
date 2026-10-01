# Spec 08 · T1：Transcript + Painter + CLIContext 骨架（波次 W5）

> 分支 `spec/cli-t1`；worktree `C:\Users\shifu\.worktrees\mocode\spec-cli-t1`；
> 前置：W4 全部合并（PluginMessage 事件已存在）；开工前通读 `mocode/cli/` 全部 11 个文件与 `tests/test_display.py` 校准；
> 深读：总纲 + `ref/cli-tui-api.md` 的 §0（现状审计）、§1（设计原则）、§2（Transcript）、§3（Painter，**以 §3.4 修正后的架构为准**：只维护"本 turn 实时区"，历史纯 print）、§3.5（块生命周期）、§5.1/5.2/5.3（CLIContext 骨架）、§7（风险，尤其第 1 条对策）。
> 你只读本 worktree 内的文件；绝不操作主检出，绝不 merge/push/tag。

## 现状事实

- `mocode/cli/` 11 文件：`display.py`（304 行，追加式 + `place/rewrite/_block` 单块重写 + `clamp_visible` 单行夹紧 + `live = isatty` 开关）、`render.py`（132 行，`CLIRenderer.draw()` 封闭 match；未知事件落 `event.summary()`；`ConversationChanged` 走 `L.conversation` 重放）、`lines.py`（347 行，`Line` 词汇 + `L.user/prompt/divider/tokens/answer/reasoning/notice/tool_summary/tool_pending/tool_close/conversation`）、`input.py`、`plugin.py`（`CLIPlugin.build(self, cli: CLIApp)`、`load_cli_plugins` 只认 `mocode.cli/plugin.py`、`build_cli_plugins` 错误隔离）、`app.py`、`commands.py`、`dialogs.py`、`text.py`、`theme.py`、`__init__.py`。
- 无 `transcript.py` / `painter.py`。
- `tests/test_display.py`：`_make_display(live=False)` + `TestRenderer._run`（真 AgentLoop + MockProvider + 真 ToolRegistry，整 turn 渲染后对输出逐行断言，如 `["let me check", "✓ echo  x", "all done"]`、`"↑3 ↓3 tokens"`）；live 路径用 `REWRITE` 正则断言转义序列。
- `PluginMessage` 事件已上事件流（W4）；CLI 尚无渲染路径。

## 目标

**T1 = 视觉零变化的架构替换**。事件先折叠进 Transcript（纯数据文档模型），Painter 把"本 turn 实时区"投影到终端（已提交历史纯 print 进原生滚动缓冲）；CLIPlugin 从"整个 CLIApp"收窄到 `CLIContext`（骨架三成员）。删掉 `Display` 的单块重写机制。**现有 test_display 的输出断言就是视觉基线，必须原样通过**（live 转义序列断言按等价输出适配，语义不变）。

## 工单（每项一个 commit）

### T1 `cli/transcript.py` — 会话文档模型
- 纯数据、无终端依赖：
  ```python
  @dataclass
  class Block:
      id: str                 # 工具块 call_id；流式块 run 内序号；插件消息块 block_id
      kind: str               # "user"|"answer"|"reasoning"|"tool"|"notice"|"rule"|"plugin"
      lines: list[Line]       # 复用 lines.py 词汇
      state: str = "done"     # "streaming"|"running"|"done"
      meta: dict = field(default_factory=dict)   # call_id/usage/error/可展开内容
  class Transcript:
      def apply(self, event: Event) -> None            # 纯折叠：相同事件流 → 相同 Transcript
      def apply_history(self, messages, tools) -> None # 现状 L.conversation 的泛化（/resume 重建）
  ```
- 折叠规则照 `render.py` 现有 match 语义逐一迁移：TextDelta/ReasoningDelta 追加当前流式块；ToolCallStarted 开 running 块（`L.tool_pending`）；ToolOutput 进块 meta；ToolCallFinished 落定（`L.tool_close`）；Notice → notice 块；RunFinished/RunFailed → rule/tokens 收尾；ConversationChanged → apply_history；**PluginMessage → kind="plugin" 的块**（同 block_id 更新同块，sealed 落定；无 drawer 时用内置一行渲染 `event.summary()`——与未知事件同策略）。`TurnEnded` 边界（RunFinished）= 实时区提交点。
- 测试（无终端）：给定事件序列断言块结构；`apply_history` 与旧 `L.conversation` 对同一 messages 产出相同行（A/B 对比测试）。

### T2 `cli/painter.py` — 本 turn 实时区投影
- 职责单一：Transcript → 终端。**不维护全量脏区锚点**（§3.4 修正）：已提交块纯 print 进原生滚动缓冲；实时区 = Transcript 尾部未提交块。
- TTY 路径：实时区重画经 prompt_toolkit 的 `print_formatted_text`（above-prompt 输出原语）或等价的"清行重画"——**T1 不自研光标数学、不上 Application/Layout**（§7 风险 1 对策）；流式 delta 合并节流（~30ms）。
- 块生命周期 open（实时区，可更新）→ sealed（ToolCallFinished / turn 结束）→ committed（RunFinished 后纯 print，**永不重画**）。多工具并发批 = 多个 open 块并排重画（现状 place/rewrite 只有一个块的泛化）；单行高度约束（`clamp_visible` 的语义）在 T1 保持——视觉零变化。
- 非 TTY 路径：纯追加，块落定时输出一次，流式按行输出——与现状 `live=False` 行为一致（管道模式承诺不变）。
- `Display` 的 `place/rewrite/_block/_invalidate/clamp_visible` 机制整体删除，Display 收窄为追加式写入器（stream/render/fix_console/高度检测保留所需部分）；`render.py` 的 `CLIRenderer` 变为"事件 → Transcript.apply → Painter"的薄驱动。

### T3 CLIContext 骨架 + CLIPlugin 签名切换
- `mocode/cli/plugin.py`：
  ```python
  @dataclass
  class CLIContext:
      commands: CommandRegistry
      drawers: DrawerRegistry       # register(event_type_or_kind, fn: Event -> list[Line])
      ui: UI                        # 骨架：is_interactive: bool；message(text)（= emit_message 的 CLI 侧内建 drawer）
  class CLIPlugin:
      def build(self, ctx: CLIContext) -> None: ...
  ```
- `CLIRenderer` 从封闭 match 改为查表分发：内置事件是**预注册**的 drawer（同一机制，无第二条路）；未知事件/无 drawer 落 `event.summary()`（现状兼容）。`drawer.register(PluginMessage 的 kind, fn)` 可覆盖插件消息渲染；`override=True` 显式覆盖内置 kind 的能力预留接口位但不实现查找优先级之外的逻辑。
- `BuiltinCommands` 迁移到新签名；`build_cli_plugins` 错误隔离语义不变；`load_cli_plugins` 行为不变（包支持 W2-C 已做）。
- `cli/app.py`：`CLIApp` 内部组装 Transcript/Painter/CLIContext；对外属性面（`commands/display/input/conversation/...`）保持——嵌入式调用方（tests）不破。

### T4 测试升级 + 视觉基线锁定
- `tests/test_display.py`：现有输出断言**原样保留通过**（这是视觉零变化的证据）；新增 transcript 结构断言与 painter golden（少量样例锁 ANSI 输出）。
- `tests/test_cli_plugin.py`：CLIContext 注册 drawer 后自定义事件渲染的用例。
- `docs/ARCHITECTURE.md`（本波归你）：cli 模块地图补全（input/dialogs/text/theme 节点缺失的文档滞后一并修）、Transcript/Painter 小节、"前端只是事件的读者"原则重申。

## 明确不做（T2/T3 的领土，本轮不实现）

- 工具块多行展开、ToolOutput 展开查看、流式块重排为多行、spinner 态、底部状态栏、命令菜单、diff/markdown 渲染、rich 依赖、prompt_toolkit Application/Layout、键位注册、输入中间件、`ui.confirm/select/input` 对话框、status/header 贡献点。

## 验收

1. `uv run pytest -q` 全绿（含**未修改的既有 test_display 输出断言**），独立确认退出码 0。
2. A/B 证据：任选 3 个既有整 turn 渲染用例，报告"改动前后输出逐字节一致"的对比结论（在改动前先跑基线存档，改后 diff）。
3. `git diff <merge-base> --stat` ⊆ `mocode/cli/**` + `tests/test_display.py` + `tests/test_cli_plugin.py` + `tests/test_lines.py`（如需）+ `docs/ARCHITECTURE.md`。

## 禁触清单

- `mocode/core/**`、`mocode/host/**`、`mocode/plugins/**`、`mocode/providers/**`、`mocode/testing/**`、`examples/**`。
- `mocode/cli/input.py`、`mocode/cli/dialogs.py`（T1 不动输入层与对话框）。
- 除上述外的 tests 文件、`TODO.md`、`AGENTS.md`、`docs/plugins.md`、spec 文件。

## 最终报告格式

工单状态表｜commit 清单（hash+message）｜自测真实结论（命令+退出码 + A/B 对比结论）｜偏差与取舍｜未决问题。遇阻塞：报告后停止，不越界自救。
