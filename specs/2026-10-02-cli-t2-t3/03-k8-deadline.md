# Spec 03 · K8×K13：墙钟预算约束重试退避（波次 A）

> 分支 `spec/k8-deadline`；worktree `C:\Users\shifu\.worktrees\mocode\spec-k8-deadline`；
> 前置：W0；深读：总纲 + `ref/kernel-plugin-api.md` K8（"墙钟预算同时约束重试退避 sleep"）与 K13。
> 只读本 worktree；绝不 merge/push/tag。

## 现状事实（已核实）

- `mocode/core/provider.py`：`with_retry_stream(provider, *args, policy=None)`（:283-287）；退避循环 `for attempt in range(p.max_attempts)`，`_compute_delay`（:243，指数+jitter、cap max_delay），Retry-After 命中则覆盖 delay（:318-320）；**sleep 是裸 `await asyncio.sleep(delay)`（:325）**，可取消但不感知 deadline；首块后窗口关闭（:328-334）。
- `mocode/core/agent.py`：`AgentConfig.max_turn_seconds`（0=不限）；`_iterate` 里 `started = time.monotonic()`（:386），预算检查在每次迭代顶（:392-416），超限 break → `RunFinished(stop_reason="time_budget")`；**注释自认（:397-398）"回退中的 turn 可能超预算一个间隔"**——本工单消灭这句话。
- with_retry_stream 调用点（:446-452）不传 policy。
- 测试惯例：`tests/test_retry.py` autouse fixture 把 `asyncio.sleep` monkeypatch 成 AsyncMock（:54-58）；时间源 monkeypatch 替换（`tests/test_display.py:467` 先例）。

## 目标

退避 sleep 前检查剩余墙钟预算：预算耗尽时不再退避重试，turn 以 `stop_reason="time_budget"` 优雅终止（不是 provider 错误、不是取消）。未配预算时行为零变化。

## 工单（每项一个 commit）

### T1 deadline 进入编排器
- `core/provider.py`：`with_retry_stream(provider, *args, *, policy=None, deadline: float | None = None)`（monotonic 时刻，None=不限）。
  - **每次退避 sleep 前**：`deadline` 已过 → 不 sleep，抛新异常 `RetryDeadlineExceeded(Exception)`（携带 provider 名与最后异常，docstring 说明语义：调用方应把它当作"预算终局"而非错误）。
  - **每轮 attempt 顶**：同样检查（覆盖"sleep 后首 chunk 前预算已过"的窗口）。
  - Retry-After 与 deadline 同时存在：deadline 优先（预算是硬边界）。
- `core/agent.py`：`max_turn_seconds > 0` 时传 `deadline=started + max_turn_seconds`；`_iterate` 捕获 `RetryDeadlineExceeded` → 与迭代顶预算检查同路径 break（`stop_reason="time_budget"`；此时 response 未成形、无 assistant 消息入史，历史一致性天然成立——写明理由）。
- 删除/改写 `agent.py:397-398` 的"可能超一个间隔"注释；`docs/providers.md` retry 一节补 deadline 参数两行。

### T2 测试
- `tests/test_retry.py`（monkeypatch `time.monotonic` + 既有 sleep fixture）：sleep 前 deadline 已过 → 抛 `RetryDeadlineExceeded` 且不再 sleep；attempt 顶检查生效；Retry-After 被 deadline 压制；`deadline=None` 行为与现状逐项一致。
- `tests/test_agent_loop.py`：`max_turn_seconds` 小 + provider 前两次抛 retriable 异常第三次才成功 → turn 以 `time_budget` 终止、`RunFailed` 不出现、messages 可回放。

## 验收

1. `uv run pytest -q` 全绿，独立退出码。
2. `grep -n "one interval\|一个间隔" mocode/core/agent.py` 零命中（旧注释已消灭）。
3. `git diff master --stat` ⊆ 写入范围。

## 禁触清单

- `mocode/core/` 除 `provider.py`、`agent.py` 外一切；`mocode/host/**`、`mocode/cli/**`、`providers/`、`mocode/plugins/__init__.py`（`RetryDeadlineExceeded` 的 SDK 导出与 ARCHITECTURE 更新归 lead 收尾，或下波工单顺带——本波不碰）。
- tests：只动 `tests/test_retry.py`、`tests/test_agent_loop.py`。
- `docs/ARCHITECTURE.md`、`docs/plugins.md`、`TODO.md`、`AGENTS.md`、spec 文件。

## 最终报告格式

工单状态表｜commit 清单｜自测真实结论（pytest + grep 证据）｜偏差与取舍｜未决问题。阻塞即停。
