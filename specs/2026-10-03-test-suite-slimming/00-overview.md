# 00 — 总纲：测试套件精简、提速、去脆弱

> 组：`specs/2026-10-03-test-suite-slimming/` · 日期 2026-10-03 · 状态：进行中
> Git / worktree 协议见 spec-team SKILL.md「Git / worktree 协议」一节，本组不重复。

## 目标与范围

**目标**（一句话）：全量测试从 ~1006 个 / 约 70s 精简到 ~450 个 / < 15s，同时消灭
"改一句文案就红"的脆弱断言和 sleep 当同步的时序脆弱性，行为保护只减量不减级。

**范围**：只动 `tests/`、`pyproject.toml`（pytest 段）、`docs/testing.md`、`AGENTS.md`。
**产品代码 `mocode/**` 零 diff** —— 发现疑似产品 bug 上报 lead，不越界修改。

**决策记录**（已与用户拍板，不再反复）：

| 岔路 | 决策 |
|---|---|
| 规模 | 均衡精简：1006 → 450±50，全量 <15s |
| 慢子进程测试 | 进程内 fake 优先；真子进程仅保留 spawn/kill/exit/stdio 进程契约 6–8 个 |
| 文案断言 | 结构化重断言；`test_display.py`/`test_lines.py` 的 escape golden 集豁免 |
| 空洞与门禁 | 补 transcript/prompt/io/text 小测试；dev 加 pytest-timeout，单测 ≤30s |

## 基线（ref/baseline.md，lead 采集）

- 31 个文件、1006 个测试、约 1.6 万行；`pyproject.toml` 的 pytest 配置仅 `asyncio_mode = "auto"`。
- 耗时大头：`test_builtin_mcp.py`（117 测试，真子进程，含 3s sleep）> `test_shell_bg.py`
  （33 测试，真 bash sleep 5/2）> `test_builtin_codemode.py`（158 测试）> MCP 附属 4 文件。
- 脆弱点分布：提示词/渲染文案断言 ~130 行；sleep 当同步 6 处
  （test_builtin_mcp.py:1763,1767,2061,2094、test_builtin_mcp_codemode.py:253、
  test_builtin_codemode.py:1459）；`test_retry.py` 13 行钉死退避秒数字面量；MCP 测试 ~35 行
  私有属性断言（`_watch_task/_ctx.tools/_codemode_warned/_registered`）；`test_cache_protect.py`
  逐字 diff/工具清单断言。
- 覆盖空洞：`core/transcript.py`、`host/prompt.py`、`host/io.py`、`host/text.py` 零直接测试。

## 全局不变量（每个 agent 逐条遵守，写完自检）

1. **零裸睡当同步**：禁止 `time.sleep` / `asyncio.sleep` 做等待（守卫会失败该测试）。等待一律
   `wait_until(predicate, bound=…)` 条件轮询或 `asyncio.Event` 事件门控；确需真实等待（子进程
   fake 内部的 sleep、shell 看门狗时限这类"等待即被测行为"）才用 `settle()` / `real_time()`。
2. **零墙钟断言**：禁止 `assert elapsed < X` 类 stopwatch 断言。用事件（unset event 即事实）、
   死锁边界（`asyncio.wait_for(..., bound)` 把挂起变成失败）或 FakeClock 证明。
3. **零提示词/文案字符串断言**：system prompt 测**结构**——section 名集合、工具名集合与顺序、
   pin/冻结行为；用户可见文案（错误话术、notice 文本、mcp_status 渲染）测存在性与
   可解析性，不测逐字。唯一豁免：`test_display.py` / `test_lines.py` 的 escape 序列
   golden——那是 offset 机制契约，不是文案。
4. **零私有属性断言**：`_watch_task`、`_ctx.tools`、`_codemode_warned`、`_registered`、
   `_max_value_chars` 等一律改为可观测行为（事件、Notice、请求面 `provider.calls`、
   工具注册结果、命令回包）。
5. **零算法字面量钉死**：退避秒数、调用序号索引（`provider.calls[N]`）改为性质/边界断言
   （区间、单调、首/末请求语义）。例外：秒数来自测试显式传入的 `RetryPolicy(base_delay=…)`
   参数时可精确相等。
6. **每个 commit 独立过门禁**，不留"改一半"的中间态；红即修，不带病进下一 commit。

## 保留底线（删除后不少于，低于即补回或上报）

| 文件 | 底线 | | 文件 | 底线 |
|---|---|---|---|---|
| test_agent_loop.py | 45 | | test_plugins.py | 25 |
| test_dispatch.py | 30 | | test_runtime.py | 12 |
| test_core.py | 30 | | test_session.py | 12 |
| test_events.py（含 test_state） | 20 | | test_retry.py | 15 |
| test_channel.py | 12 | | test_provider.py | 12 |
| test_conversations.py | 35 | | 其余文件合计 | ~130 |

核心 11 个文件合计 ≥230，全量 450±50。删除登记写进各组最终报告（测试名 + 理由），
lead 在 W2 汇总核对"没有悄悄删掉核心"。

## 波次表

| 波次 | 模式 | 范围（文件级独占） | 量化出口 |
|---|---|---|---|
| W0 | 串行前置 | tests/conftest.py、pyproject.toml | 不改测试语义，1006 全绿 |
| W1-A | 并行 | 内核 7 文件 | ≥140 测试，组耗时 <3s |
| W1-B | 并行 | 宿主+CLI 14 文件 | ≥150 测试，组耗时 <4s |
| W1-C | 并行 | 内置插件 11 文件 | ≥120 测试，组耗时 <8s |
| W2 | 串行 | 合并+终验+文档 | 全量 <15s；守卫硬失败；450±50 |

**写入范围不相交约定**：W0 拥有 `tests/conftest.py` 与 `pyproject.toml` 的 pytest 段，且对
conftest **只新增不改名**（W1 三组文件里的既有 import 名称必须继续可用）。W1-A 拥有
`test_agent_loop/dispatch/core/channel/events/retry.py` 与 `test_state.py`（后者并入
test_events.py 后删除原文件）。W1-B 拥有 `test_conversations/runtime/session/config/
plugins/plugin_message/commands/cli_plugin/provider/display/lines/plugin_env/
plugin_install/cache_protect.py`。W1-C 拥有 5 个 MCP 文件 + `test_builtin_codemode/
shell_bg/tools/builtin_plugins/test_testing.py` + `tests/_mcp_fake.py`，其中 MCP 5 文件
允许合并为 2 个（拆分/合并在组内自定，但在工单里写死最终清单）。W2 拥有
`docs/testing.md`、`AGENTS.md`、新增 `tests/test_transcript.py`、`tests/test_host_tools.py`。

## 门禁命令（所有波次通用）

```bash
uv run pytest -q; echo "EXIT=$?"     # 全量，退出码独立确认，禁止管道吞
uv run pytest tests/<组文件> -q; echo "EXIT=$?"   # 组的自测
uv run pytest --durations=15 -q      # 找 >1s 的单项
```

## 环境事实

- Windows 10 + Git Bash；`uv` 管理 Python；测试不与真实 `~/.mocode` 交互。
- 提交信息用英文 conventional commits（`test(scope): …` 风格），注释与文档中文。
- agent 遇阻塞：报告阻塞点后停止，不越界自救；允许的降级路径在各工单「诚实出口」写明。
