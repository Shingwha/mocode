# Spec 组 · CLI T2/T3 + 内核收尾（2026-10-02）

> 状态：进行中。spec 是定义、不是看板——分支被合并 = 工单完成，git 是唯一进度权威。
> worker agent 不改任何 spec、不写状态；完成信号 = 最终报告 + 分支 commit。
> 上一组：`specs/2026-10-01-kernel-plugin-refactor/`（内核线 + T1 已全部合并，终态 686 passed）。

## 目标与范围

完成两份设计文档的**全部剩余内容**：

- **CLI 文档 T2**：实时区重画泛化——流式块部分行重写、工具块多行（运行期输出尾部）与落定收拢、spinner（thinking/running 态）、单行夹紧消失。**视觉允许变化**（T1 的"零变化"承诺只属于 T1）。
- **CLI 文档 T3**：§5 插件 API 全量（keys / input 中间件 / status / header / theme / ui.confirm-select-input / on_close / conversation 只读视图 / is_interactive 降级契约）、§4.1 中断语义升级（Ctrl-C/Esc）、Markdown 流式轻量渲染 + 落定完整渲染（rich 可选）、命令菜单、diff drawer 范例、审批流配方（§4.3）。
- **内核文档收尾**：K8×K13 交互——墙钟预算约束重试退避 sleep（文档 K8 明文要求）；S2 TUI 联动——`BashSession` 运行中前台命令转后台 + Ctrl+B 运行期键位（上一组遗漏）。
- **翻案实现**：PluginMessage 随 session 持久化与 resume 回放（CLI 文档 §5.4 明文承诺；上一组取舍 #1 以"Session 不存事件"降级为限制，本组按用户指示实现，`docs/plugins.md` 的限制段落改写）。

**不做**（文档明示）：K14 / K17（文档已移除/否决）；已提交块的折叠展开（§3.4"committed 永不重画"）；alternate screen；接管终端滚动条；textual。

## 权威参考与事实口径

- 设计意图：`ref/` 两份文档（worker 只信组内副本）。
- **事实基线（2026-10-02 探索核实，master c8b30eb，686 passed）**——各工单开头重述与本工单相关的部分；冲突时以工单与代码为准。要点：流式文本现状"追加即提交"（不在可重写区），`clamp_visible` 只作用于 painter 实时区的工具行；`DrawerRegistry.register(key, fn, *, override=False)` 已实现（T1 超前）；`UI` 仅有 `is_interactive` + `message()`；Session 无任何事件字段；`with_retry_stream(provider, *args, policy=None)` 的 sleep 在 `provider.py:325`（裸 asyncio.sleep，不感知 deadline）；`_iterate` 的预算检查在每次迭代顶（`agent.py:392-416`），注释自认"回退中可能超预算一个间隔"。

## 波次表

| 波 | 分支 | 工单 | 内容 | 前置 |
|---|---|---|---|---|
| A | `spec/t2-render` | 01 | T2 渲染泛化（实时区管理器） | W0 |
| A | `spec/bash-promote` | 02 | S2 运行中命令转后台 | W0 |
| A | `spec/k8-deadline` | 03 | K8×K13 墙钟×退避 | W0 |
| B | `spec/t3-input-status` | 04 | 键位/中断语义/中间件/状态栏/CLIContext 全量 | A 全合 |
| B | `spec/t3-content` | 05 | Markdown 流式 + diff drawer 范例 + rich 可选 | A 全合 |
| B | `spec/session-replay` | 06 | PluginMessage 持久化与回放 | A 全合 |
| C | `spec/t3-ui` | 07 | 运行期对话框/菜单/审批配方/降级契约/header | B 全合 |

合并顺序 = 依赖序；波内并行（写入范围已核对文件级不相交，见下）。02 先于 04 合并（04 的 Ctrl+B 依赖 02 的 API）。

## 全局不变量（每份工单引用，违反即打回）

1. `AGENTS.md` 硬不变量全部有效（core 零特性名、唯一执行路径、观察走事件流、`import mocode` <1ms、分层 `core←host←cli` 不破）。
2. 回归红线：`uv run pytest -q` 全绿。**基线 686 passed**（2026-10-02 实测 30.86s）。只增不减（T2 允许改写 T1 golden 的**期望字节**——那是视觉变化的一部分，断言结构不得弱化）。
3. 无 TTY 承诺不变：所有新特性在非 TTY 下降级（§5.7 契约），管道模式输出仍为纯追加。
4. 事件流契约不变：`run_id + seq`、每 turn 恰一个终止事件、`to_dict()` 往返。
5. "能写成插件的概念不进 core/host"：keys/status/header/ui 全是 cli 层领土；本轮 core 改动仅 03 号工单（deadline）与既有事件的零扩展。

## 本组技术裁决（已拍板，不再反复）

1. **不迁移 prompt_toolkit Application+Layout**。文档 §7"T2 再切 Layout"的动机（光标协调）已被 T1 的等价清行重画 + epoch 冻结实证消解；状态栏走 PromptSession 原生 `bottom_toolbar`，运行期键位走 `prompt_toolkit.input.create_input` 的 raw 读取循环（编辑期继续由 PromptSession 拥有——§3.2 反对全自绘的本意）。§3.4 的内联哲学与"明确不做"清单同向。
2. **流式重排的边界**：已完成的行随追加即提交（不可重写）；可重写部分 = 当前未完成的行（多行回绕按可视行数计，`count_visual_lines`）。围栏代码块着色在行完成时判定（开启围栏之后的行可着色，开启行本身已提交为素色）——文档 §3.4 committed 不可变原则的推论，记为 T2 语义。
3. **工具块展开的边界**：运行期（open 态）显示输出尾部；落定收拢为单行结论（文档 §3.1 "collapsed 态就是那行熟悉的 ✓…"）。verbose 开关（painter 布尔，01 实现，04 接线 Ctrl+O）让"落定未提交"的块保留输出尾部提交；**已提交块不可展开**（§3.4）。
4. **header = 打印式装饰**（set 时 print 到提示符上方，进滚动缓冲），非实时区组件——无 Application 下的诚实实现；status = bottom_toolbar 贡献点。
5. **Markdown 渲染**：流式期间手写轻量围栏检测（零新依赖）；落定后完整渲染用 rich——新增可选组 `[dependency-groups] tui = ["rich"]`，未安装则回退素文本；非 TTY 不用。
6. **PluginMessage 持久化**：Session 增加有界 `plugin_messages`（deque 200 条，序列化存储）；resume 在 `changed()` 之后经 channel 重放（seq 重新盖章是既有语义）；回放期间暂停捕获防重复。
7. **运行期 UI 双路径**：turn 运行中 → 实时区内联对话框（raw 键位读取）；空闲 → questionary（dialogs.py 既有）；非 TTY → confirm=False / select=None / input=None 并文档化。

## Git / worktree / 环境协议（沿用上一组）

- worktree 根 `C:\Users\shifu\.worktrees\mocode\<分支名 / 换 ->`；lead 统一创建销毁；worker 绝不碰主检出、绝不 merge/push/tag。
- 分支从合并时刻最新 master 创建；每 commit 过全量门禁（独立确认退出码）；lead 合并前后各跑一次全量门禁。
- 开工第一步 `export PATH="/c/Users/shifu/.local/bin:$PATH" && uv sync`（PATH 缺 uv 是本机现状）。
- 环境：Windows 10 + Git Bash；Python 3.13.16 via uv 0.12.21；POSIX 专属行为按 `sys.platform` 分支测试。基线 686 passed。

## 语言与风格

spec 中文；代码/注释/仓库文档/commit 英文（仓库惯例：小写祈使句 + feat:/refactor:/fix:/test:/docs: 前缀 + em-dash 长标题）。

## 文档所有权矩阵（防并行冲突，逐波唯一所有者）

| 文件 | 波 A | 波 B | 波 C |
|---|---|---|---|
| `docs/ARCHITECTURE.md` | 01 | 06 | — |
| `docs/plugins.md` | — | 04 | 07 |
| `pyproject.toml` / `uv.lock` | — | 05 | — |
| `AGENTS.md` | — | — | lead 收尾 |

## 状态记录（lead 收尾时一次写完）

| 工单 | 分支 | 状态 |
|---|---|---|
| 01-t2-render | spec/t2-render | ⬜ |
| 02-bash-promote | spec/bash-promote | ⬜ |
| 03-k8-deadline | spec/k8-deadline | ⬜ |
| 04-t3-input-status | spec/t3-input-status | ⬜ |
| 05-t3-content | spec/t3-content | ⬜ |
| 06-session-replay | spec/session-replay | ⬜ |
| 07-t3-ui | spec/t3-ui | ⬜ |
