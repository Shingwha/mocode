# Spec 02 · S2 TUI 联动：运行中命令转后台（波次 A）
> ✅ 2026-10-02 @ merge（`spec/bash-promote`）—— 合并后 698 passed。

> 分支 `spec/bash-promote`；worktree `C:\Users\shifu\.worktrees\mocode\spec-bash-promote`；
> 前置：W0；深读：总纲 + `ref/kernel-plugin-api.md` S2 表格"TUI 联动"行 + `ref/cli-tui-api.md` §4.1。
> 只读本 worktree；绝不 merge/push/tag。

## 现状事实（已核实）

- `mocode/host/plugin/builtin/shell.py`：W4-A 已实现后台三件套——`bash(command, restart, timeout, run_in_background)` / `bash_output(shell_id, filter, wait, timeout)` / `kill_shell(shell_id)`；`_Job`（ring 2000 行/256KB、consumed 游标、done event）；并发上限 `plugins.shell.max_background`（默认 16）；POSIX `start_new_session` + `killpg`；restart/close 杀全部 job。
- **前台执行**仍是一次性 `session.execute(...)`：`await` 到完成，期间无句柄、不可转后台——S2 的"前台命令超时转后台/Ctrl+B 当场转后台"（Claude Code / pi-patty 同款）缺失。
- 前台输出经 `ctx.emit(ToolOutput(call_id, text, stream))` 逐行进 open 块。
- 消费者（04 号工单）将注册运行期键位 Ctrl+B 调用本 API——**API 形态必须对键位处理器友好**（无需知道 call_id 也能用）。

## 目标

把一个**正在前台运行**的 bash 调用提升为后台 job：立即返回句柄，前台调用以"已转后台"结论落定，后续输出与状态走既有 `bash_output`/`kill_shell`。前台零提升时行为零变化。

## 工单（每项一个 commit）

### T1 提升机制
- `BashSession` 新方法 `async def promote(self, call_id: str | None = None) -> dict`：
  - `call_id=None`：恰好一个前台调用在跑 → 提升它；零个或多个 → `ToolError("no_running_call" / "ambiguous_call", ...)`（消息区分）。
  - 提升 = 把运行中的 proc + 读泵并入 `_Job`（ring 从提升时刻起收集——此前输出已进 open 块/模型；consumed=0），占用一个后台并发名额（满则 ToolError `limit`），登记 id（复用 `shell_<n>` 单调序列）。
  - 前台 `execute` 协作：被提升的调用从 `await proc.wait()` 中解出，工具返回
    `ToolResult(content="moved to background as shell_N (bash_output to poll)", details={"shell_id": ..., "status": "running", "promoted": True})`——status 走 OK（不是错误；模型据此改用 bash_output）。
- 实现要点：execute 与 promote 之间用 `asyncio.Future`/`Event` 握手（promote 设 future，execute 的等待循环感知后走提升路径）；超时 watchdog 与提升互斥（先到先得，另一方退出）；killpg 语义不变（提升后的 job 沿用后台终止路径）。
- 提升后的 job 完成通知走既有聚批 notifier（W4-A 机制），零改动。

### T2 测试
- `tests/test_shell_bg.py` 新类：提升后立即返回句柄；`bash_output` 能读到提升后增量；提升占用并发名额；promote 空前台/多前台报错；提升与超时竞争（注入时序，先到先得）；restart/close 清理提升 job；前台无提升路径回归（现有测试原样过）。Windows 可跑全跑，POSIX 分支沿用既有 mock 手法。

### T3 文档
- `docs/plugins.md` shell 插件段（host 行为描述）补 promote 语义一小段——**只加不改其他节**（04/07 号工单拥有本文档其他波次的所有权，波 A 期间无人动它）。

## 验收

1. `uv run pytest -q` 全绿，独立退出码。
2. 冒烟（Windows 真跑）：起前台 `sleep` 长命令 → promote → bash_output 轮询到完成 → 报告真实输出。
3. `git diff master --stat` ⊆ 写入范围。

## 禁触清单

- `mocode/cli/**`（键位接线归 04）、`mocode/core/**`、`mocode/host/**` 除 `builtin/shell.py`、`mocode/testing/**`、`examples/**`。
- tests：只动 `tests/test_shell_bg.py`。
- `docs/ARCHITECTURE.md`（01 号工单领地）、spec 文件。

## 最终报告格式

工单状态表｜commit 清单｜自测真实结论（pytest + 冒烟输出）｜偏差与取舍｜未决问题。阻塞即停。
