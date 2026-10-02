# Spec 01 · T2 渲染泛化：实时区管理器（波次 A）

> 分支 `spec/t2-render`；worktree `C:\Users\shifu\.worktrees\mocode\spec-t2-render`；
> 前置：W0；深读：总纲 + `ref/cli-tui-api.md` §2/§3（以 §3.4 修正架构为准）/§3.5/§6 T2 行/§7。
> 只读本 worktree；绝不 merge/push/tag。
>
> **进行中状态（2026-10-02 快照，续作者必读）**：分支 `spec/t2-render` @ `3a554ee`（已 rebase 到
> master `e45b44d`），**未合并**；worktree 保留于 `C:\Users\shifu\.worktrees\mocode\spec-t2-render`，续作直接在该
> worktree 继续（无需重建）。已提交 T1 `ba2bb1c`（区域管理器）、T2 `d68ef1a`（流式未完成行进区域）、
> T3 `3a554ee`（工具输出尾部 + 收拢 + `painter.verbose`），**提交态门禁实测 714 passed**（47.12s，exit 0）。
> **T4 半成品未提交**：工作区 WIP（`lines.py` 的 `thinking()` + `painter.py` 的 thinking 行与 `_spinning`
> 标志）是续作点，勿丢弃勿手改；带 WIP 门禁红：7 failed `TestPainterGolden`（thinking 行改期望字节、
> golden 未更新）。`.smoke/`（未跟踪）是冒烟 driving 脚本，可参考可删。T5 文档未动。
> T1-T3 的实现契约八条见总纲"跨会话交接快照"，勿推翻已定型机制；非 TTY 路径字节级保持是回归红线。
> 剩余：T4 spinner 接线（接续 WIP + golden 回绿）｜T5 `docs/ARCHITECTURE.md` 终端章节改写。
> **动工前先读总纲「待决事项」**——本工单是否继续执行取决于新对话的范围裁决（TUI 重设计意图）。

## 现状事实（已核实）

- `mocode/cli/painter.py`（264 行）：实时区只含工具行——`_place`（:202）打印占位行并占 row，`_rewrite`（:214）用 `\033[{n}A\033[K…\033[{n}B\r` 单行原地重写；epoch 冻结（`_region_epoch` vs `display.epoch`）；`THROTTLE=0.030` 节流合并流式 flush；`clamp_visible`（:50-79）单行夹紧（保 ANSI、wcwidth 计宽）；非 TTY `_place` 返回 None 纯追加。
- `mocode/cli/transcript.py`（359 行）：`Block(id, kind, lines, state, meta)`；流式块"每 kind 单块"，文本存 `meta["text"]`，**行只在 seal 时物化**（`_seal_stream` :184）；ToolOutput 累进 `block.meta["output"]` 不出线（:116）；PluginMessage 折叠含 sealed 后跟随块（:232）；`apply_history` 走 `L.grouped_messages`。
- `mocode/cli/display.py`（212 行）：薄追加式写入器，`epoch` 每次追加 +1，`stream/end_stream`、`render_all`、`live`。
- 流式文本**不在可重写区**：`_flush_stream` → `display.stream` 按 delta 追加（自然换行、不 clamp、即提交）。
- `mocode/cli/text.py` 有宽度助手（`count_visual_lines` 等，input.py 在用）；`lines.py` 有 `Line` 词汇（`tool_pending/tool_close/tool_summary` 等）。
- golden 测试：`tests/test_display.py::TestPainterGolden`（:380-476）锁精确 ANSI 字节；`TestTranscript`（:480-683）纯折叠断言（含 sealed 跟随块）。

## 目标

把实时区从"若干单行工具占位"升级为**实时区管理器**：区域内是任意高度、可增减的 open 块集合（流式部分行 + 运行期工具块 + spinner），任何结构变化整体重画区域。单行夹紧消失。这是 T2，视觉允许变化；**非 TTY 路径行为不变**。

## 工单（按序执行，每项一个 commit）

### T1 区域管理器重构
- Painter 维护：锚点行（区域顶部）、open 块的有序列表（各带当前可视行数）、区域总可视行数。任何事件引起的结构变化（新 open 块、块落定收拢、块提交、流式行追加）→ **整区重画**：光标上移总行数 → 逐行清写 → 回到区底。行数计算必须用可视行数（`count_visual_lines`，处理回绕与 wcwidth）——这是正确性核心，宽行回绕算错会撕坏屏幕。
- 区域成员：a) 流式块的未完成行（见 T2）；b) RUNNING 工具块（见 T3）；c) spinner 行（见 T4）；d) sealed 未提交块（收拢后的单行结论，等待 turn 结束统一提交）。
- 提交点不变：`RunFinished/RunFailed` 的 rule 块 append 时重置锚点（committed 进滚动缓冲）。epoch 冻结机制保留（任何区外追加即失效重置）。
- `clamp_visible` 删除；宽度截断保留为 `_fit`（单行宽度 fit，用于结论行——高度不再夹紧）。
- **T1 golden 字节会变——这是本波授权的视觉变化**：改写 `TestPainterGolden` 的期望字节（结构断言不弱化：仍逐字节精确），`TestLiveBlock`/`TestClamping` 相应演进（夹紧类断言改为区域重画断言）。`TestTranscript` 的纯折叠断言**不得变**（模型不动）。
- 非 TTY：区域机制整体关闭（现状行为：纯追加、verdict 追加），现有测试原样通过。

### T2 流式块进实时区
- 流式块的**未完成行**进区域参与重画：已完成的行仍走 `display.stream` 追加即提交（不可重写，总纲裁决 #2）；未完成行（最后一个 `\n` 之后的部分）作为区域成员，每次 flush 原地重写（多行回绕按可视行数）。`_seal_stream` 的行物化逻辑相应调整（已完成行已物化，seal 只补最后部分行）。
- reasoning 与 answer 两个流式块可同时 open（各自区域成员）。
- 节流 30ms 语义不变（合并、不丢、他输出先行 flush）。

### T3 工具块多行 + 收拢
- RUNNING 工具块区域形态：参数摘要行（含 spinner 后缀，T4）+ **输出尾部**（`meta["output"]` 的最后 N 行，N=6 起步、可调常量；每行进区域，新输出替换尾部——区域内重画）。ToolOutput 高频到达时同样吃 30ms 节流。
- `ToolCallFinished` → 收拢为单行结论（`L.tool_close` 词汇），区域内重画（输出尾部消失）；verbose 标志开启时（`painter.verbose = True`，公开属性；**键位接线归 04 号工单**）落定块保留输出尾部行提交。
- 提交形态：收拢单行（默认）或 verbose 多行——都是 committed 终态。

### T4 spinner
- thinking 态（turn 运行中、当前无任何流式内容与工具块）：区域顶部一行 spinner（braille 帧 `⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏`，~80ms 步进）+ "thinking" 字样。
- 工具运行中：参数摘要行尾附同款 spinner 帧（替代静态 `…`）。
- spinner 动画需要时间驱动：用 `asyncio` 后台任务或渲染时钟（选择实现对测试最友好的——golden 测试能钉帧）。非 TTY 无 spinner。
- turn 结束 spinner 行消失。

### T5 文档 + 收尾
- `docs/ARCHITECTURE.md` 终端章节改写（本波归你）：实时区管理器、区域成员、可视行数、verbose 边界、T2 视觉语义（追加即提交 + 部分行重写）。
- 全量门禁 + `git diff master --stat` 自查 ⊆ 写入范围。

## 验收

1. `uv run pytest -q` 全绿（基线 686，只增不减），独立退出码。
2. golden 断言逐字节精确（列出改写的 golden 用例与新字节要点）；`TestTranscript` 零改动证明（`git diff master -- tests/test_display.py` 里无 TestTranscript 区改动）。
3. 非 TTY 回归：现有 non-live 测试原样通过（列出）。
4. 手动冒烟（真实终端或 pytdump 模拟）：报告多行回绕块重画、工具输出尾部、收拢、spinner 的真实行为描述。

## 禁触清单

- `mocode/cli/input.py`、`mocode/cli/dialogs.py`、`mocode/cli/plugin.py`、`mocode/cli/app.py`、`mocode/cli/commands.py`（04/07 号工单领地——本波不改任何交互面）。
- `mocode/core/**`、`mocode/host/**`、`mocode/providers/**`、`mocode/testing/**`、`examples/**`、`docs/plugins.md`、`pyproject.toml`。
- tests：只动 `tests/test_display.py`（如需新文件 `tests/test_painter.py` 可建）。
- spec 文件（归 lead）。

## 最终报告格式

工单状态表（T1-T5）｜commit 清单（hash+message）｜自测真实结论（pytest 尾行+退出码；golden 改写要点；非 TTY 回归清单）｜偏差与取舍｜未决问题。阻塞即停，不越界自救。
