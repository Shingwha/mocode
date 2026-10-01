# Spec 组 · MoCode 内核与插件重构（2026-10-01）

> 状态：进行中。spec 是定义、不是看板——分支被合并 = 工单完成，git 是唯一进度权威。
> worker agent 不改任何 spec、不写状态；完成信号 = 最终报告 + 分支 commit。

## 目标与范围

依据两份设计文档（`ref/kernel-plugin-api.md` = K/R/S 系列；`ref/cli-tui-api.md` = T1）完成：

- **完整内核与插件线**：K1-K13、K15、K16、K18（K14/K17 已在文档中否决，不做）、R1-R7、S 系列（shell 后台任务）。
- **CLI 线的 T1 骨架**：Transcript + Painter + CLIContext，视觉零变化。T2/T3 不在本组。

无向后兼容约束（文档明确）：破坏现有 API 不需要迁移层。**但对外行为（无 TTY 输出、session resume 字节级复原、事件流契约）有测试锁住，不得回退。**

## 权威参考与事实口径

- 设计意图：`ref/` 下两份文档（worker 只信组内副本）。
- **文档行号已漂移**：文档写作时的裸 `core/`、`host/`、`cli/` 路径现为 `mocode/core/`、`mocode/host/`、`mocode/cli/`；类 `Agent` 已更名 `AgentLoop`。各工单开头重述了已核实的现状事实（当前 file:line）；**冲突时以工单事实与代码为准**。
- 设计文档一处错误假设已修正：CLI 文档称"事件通道本来落盘"，实际 `Session.to_dict` 不存事件 → emit_message 不做跨重启回放（取舍 #1）。

## 波次表

| 波 | 分支 | 工单 | 内容 | 前置 |
|---|---|---|---|---|
| W0 | master（lead 直做） | — | spec 组落库、仓库卫生、基线门禁 | — |
| W1 | `spec/p0-dispatcher` | 01 | K1 dispatcher、K2 provenance、K4 availability、K9 emit 盖 run_id、K18 source | W0 |
| W2 | `spec/p1-core`（A） | 02 | K3+R2+K5+K6+K7+K8+R1 | W1 |
| W2 | `spec/p1-retry`（B） | 03 | K13（仅 core+provider 实现） | W1 |
| W2 | `spec/p1-loader`（C） | 04 | K16 多文件插件 | W1 |
| W3 | `spec/p2-host` | 05 | R3-R7+K10+K11+K13 配置覆盖+SDK 导出面收口 | W2 全合 |
| W4 | `spec/shell-bg`（A） | 06 | S 系列+PluginMessage/emit_message | W3 |
| W4 | `spec/testing-channel`（B） | 07 | K12 mocode.testing+K15 | W3 |
| W5 | `spec/cli-t1` | 08 | T1：Transcript+Painter+CLIContext 骨架 | W4 |

合并顺序 = 依赖序；W2 内 A→B→C，W4 内 A→B。

## 全局不变量（每份工单引用，违反即打回）

1. `AGENTS.md` 十条硬不变量全部有效，尤其：core 不含工具名/特性名/配置键；唯一执行路径 `AgentLoop.start()`；观察走事件流；插件贡献只经生命周期钩子；`import mocode` < 1ms（PEP 562）。
2. 分层 `core ← host ← cli` 不破：core 只 import stdlib + 包内；host 不 import cli、无终端词汇；cli 是 host 的普通消费者。
3. 内核文档 §0 "什么不要动"清单：事件流契约（`run_id + seq`、每 turn 恰一个终止事件、`to_dict()` 可跨进程）；十一类内核事件只扩展不重做；`ToolRegistry.freeze()` 归 host 拥有；`(priority, insertion order)` section 排序；build 同步 / prepare 异步两段式；messages 用 OpenAI dict 方言；hooks（请求/响应）与 events（单向通知）的分工；**重试编排留在内核**（K13 只是把策略参数化）。
4. 回归红线：`uv run pytest` 全绿。基线 466 passed（2026-10-01 实测 9.67s）。只增不减。
5. "能写成插件的概念不进 core"：所有新机制（dispatcher、schema 校验、retry policy、PluginMessage）保持通用，不带任何工具/插件名。

## Git / worktree / 环境协议（本组约定）

- **worktree 根**：`C:\Users\shifu\.worktrees\mocode\<分支名中 / 换成 ->`（仓库外）。lead 统一创建与销毁。
- worker 在专属 worktree 内工作：**绝不操作主检出 `C:\Users\shifu\Desktop\mocode`，绝不 merge / push / tag / checkout master。**
- 分支从**合并时刻的最新 master** 创建（lead 负责）；每 commit 独立过全量门禁；开工第一步 `uv sync`（worktree 内 .venv，已被 .gitignore 忽略）。
- lead `git merge --no-ff` 合并，**合并前后各跑一次 `uv run pytest` 并独立确认退出码**；红了整分支打回 worker 修复。
- 全程不 push、不打 tag、不发版。
- 环境事实：Windows 10 + Git Bash；uv 0.12.21 + Python 3.13.16；测试无终端依赖（`live=False` 路径）；路径全 ASCII；POSIX 专属行为（进程组等）在测试里按 `sys.platform` 分支。

## 语言与风格约定

- spec 中文（过程资产）；**代码、注释、仓库文档、commit message 一律英文**（与仓库现状一致）。
- commit 风格沿用仓库惯例：小写祈使句、`feat:` / `refactor:` / `chore:` / `test:` / `docs:` 前缀，标题一行 + 正文段落说明动机（参考 `git log` 既有条目）。
- 代码约定遵守 `AGENTS.md`：`from __future__ import annotations`、dataclass 而非 Pydantic、async-first、TYPE_CHECKING 守卫、无全局状态、stdlib 优先。

## 取舍清单（已拍板，不再反复）

1. **emit_message / PluginMessage 不做 session 持久化**——Session 不存事件，文档"事件通道本来落盘"不成立；记为已知限制，写入 plugins.md。
2. **S3 不新增 `teardown()`**——现状已有 `Plugin.close(ctx)`，后台任务清理挂 close()。
3. **K14 / K17 不做**（文档已否决：token 估算、惰性 schema）。
4. **`RunFinished.cancelled` 布尔直接删**，只留 `stop_reason`（无兼容负担）。
5. **K13 的 per-model 配置覆盖归 W3**（避免 W2 并行波在 host/config.py、host/runtime.py 上冲突）。
6. **T1 只立骨架、视觉零变化**：不引入 rich、不进 alt-screen、不自移光标（走 `print_formatted_text`）；CLIContext 只含 commands/drawers/ui 三成员；keys/status/header/middleware 留给 T2/T3。
7. **W2 的 SDK 导出面（`mocode/plugins/__init__.py`）与 docs/plugins.md 大改统一收口在 W3**——W2-A/B/C 均禁触这两个文件（W2-C 除外：plugins.md 允许**新增**"单文件 vs 包"一节，不改其他节）。
8. **墙钟预算（K8 `max_turn_seconds`）不深入重试退避 sleep 内部**——在迭代顶与工具批前检查；处于退避的 turn 最多超出一个退避间隔（≤60s）。K13×K8 的 deadline 交互留待后续版本。

## 状态记录（lead 收尾时一次写完）

| 工单 | 分支 | 状态 |
|---|---|---|
| 01-kernel-p0 | spec/p0-dispatcher | ✅ 2026-10-01 @ merge a4c2de0（507 passed） |
| 02-p1-core | spec/p1-core | ✅ 2026-10-01 @ merge 2428cab（570 passed） |
| 03-p1-retry | spec/p1-retry | ✅ 2026-10-01 @ merge efe49a4（582 passed；core/__init__ 导出面冲突由 lead 合并） |
| 04-p1-loader | spec/p1-loader | ✅ 2026-10-01 @ merge 94c53c3（595 passed） |
| 05-p2-host | spec/p2-host | ✅ 2026-10-01 @ merge 5014e35（607 passed） |
| 06-shell-bg | spec/shell-bg | ✅ 2026-10-01/02 @ merge 49b1efc（666 passed；一次打回：合并通知 jobs 顺序 flaky，实现侧按启动序排序后重合并） |
| 07-testing-channel | spec/testing-channel | ✅ 2026-10-01 @ merge 461d662（622 passed；tests/providers.py 整删无残留） |
| 08-cli-t1 | spec/cli-t1 | ✅ 2026-10-02 @ merge（686 passed；A/B 字节对比 4/4 一致，视觉零变化达成） |

收尾清扫：`272e7f0`（git-status 示例改新 CLIContext 签名；AGENTS.md 文档表补 testing.md 与 multi-file 范例行）。终验：`uv run pytest -q` → 686 passed、examples 全部可编译、`import mocode` 0.7-0.9ms。
