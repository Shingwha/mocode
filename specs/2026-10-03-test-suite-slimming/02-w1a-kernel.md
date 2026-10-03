# Spec KERNEL · 内核测试重构（波次 W1-A，并行组 A）

> 分支 `test/slim-w1a-kernel`；前置：W0 已合并进 main，本分支从该时刻 main 切出
> 深读：`specs/2026-10-03-test-suite-slimming/00-overview.md`、`01-w0-time-control.md`、
> `docs/ARCHITECTURE.md`（Hard Invariants）、`tests/test_retry.py`

## 目标

最高约束放在第一句：**这 7 个文件守护的是内核不变量，重构后每个不变量仍有一条可执行断言；
测试净减少来自去重和去白盒，不来自砍契约**。目标 ≥140 测试、组耗时 <3s、组内裸睡清零、
私有属性与算法字面量断言清零。

## 你的文件（独占，组外零 diff）

`tests/test_agent_loop.py`、`tests/test_dispatch.py`、`tests/test_core.py`、
`tests/test_channel.py`、`tests/test_events.py`、`tests/test_retry.py`、
`tests/test_state.py`。

`test_state.py` 的 8 个测试（RunState 折叠）并入 `test_events.py` 的 RunState 组，
随后删除 `test_state.py`（git rm，commit message 说明去向）。

## 工单（按序执行，每项一个 commit，每 commit 独立过门禁）

### T1 删重复 + 参数化机械模式

- `test_retry.py`：秒数字面量（0.3/0.7/2.0 的 `call_args.args ==`）改性质断言——Retry-After
  被 honoring 时断言"sleep 值 ∈ [0.25, 0.35]（来自 header 0.3）且 ≠ 默认退避区间 [1.0,1.5]"；
  `TestBackoffBounds` 的步长断言保留（那本来就是边界契约）。显式传入的
  `RetryPolicy(base_delay=2.0, max_delay=3.0)` 的精确值可保留。
- `test_agent_loop.py` `TestErrorPrefixes`：3 个同形测试参数化，error_code 断言集中一处。
- `test_dispatch.py` / `test_core.py`：同输入矩阵的小测试合并 parametrize。
- `provider.calls[N]` 按序号精确比较（test_agent_loop.py:443,469,492,512,695,708,722,737 等）
  改为**语义断言**：首请求 / 末请求 / 满足谓词的唯一请求；钉整个 messages 列表字面量的
  （:695）改为关键不变量（条数、首条 role、末条 role、包含某 call_id 的 tool 消息数）。

### T2 时序清零

- 本组唯一的真实 sleep：`test_agent_loop.py` 一处 `time.sleep(0.3)`（tool_timeout 0.05 的
  测试）——改用 FakeClock 或"取消事件已置位"的事实断言；参考文件内已有的 FastClock 模式
  （:552-630，那是正面教材，保留其精神）。
- `test_agent_loop.py` 的 `asyncio.sleep(30)` 挂起 provider 经 `SlowProvider` 走法保留
  （可取消、`wait_for` 有界，不算裸睡）。
- `test_retry.py` 的 `_patch_sleep` autouse 改为基于 conftest 守卫的等价写法或保留
  （它 patch 的是被测编排的 sleep，属 FakeClock 类别；若保留需在报告说明为何不属于"裸睡"）。
  优先：迁到 conftest 的 `FakeClock` + `advance()`，删掉本地 `_Clock`。
- 门禁：`grep -n "sleep(" tests/test_*.py`（你的 7 个文件）零命中或每处都有 `settle(` /
  `real_time(` / FakeClock 注释说明。

### T3 去白盒

- `test_agent_loop.py` monkeypatch `mocode.core.agent.time` / `mocode.core.provider.time`
  的模块级 `time` 替换：改用 conftest `FakeClock` 注入路径；若产品只接受 time 模块，
  保留 patch 但在测试 docstring 声明"接缝在 time 模块，重构改名属预期内"。
- 私有计数器（`tool_call_count`）断言改事件侧可观测事实（ToolCallFinished 条数）。
- `test_retry.py` 的 `provider_module.time` patch 同上处理。

### T4 保留与精炼并发/取消测试

- Event 门控并发测试（agent loop 的并发 turn、cancel）全部保留——这是全仓稳健并发的范本，
  只做去重不改机制。
- 确保每个测试有界：测试内 `asyncio.wait_for(..., 5.0)` 或 pytest-timeout 兜底，二者其一。

## 验收（完成前自测，报告给真实结论）

1. `uv run pytest tests/test_agent_loop.py tests/test_dispatch.py tests/test_core.py tests/test_channel.py tests/test_events.py tests/test_retry.py -q; echo "EXIT=$?"` → 全绿
2. `uv run pytest tests/test_events.py tests/test_agent_loop.py -q --durations=10` → 组内无 >0.5s 单项
3. 测试数：`uv run pytest <你的6个最终文件> --collect-only -q | tail -1` ≥ 140
4. `grep -rn "sleep(" <你的7个文件>` → 仅 settle/real_time/FakeClock/SlowProvider 语境
5. `git status --short` → 只有你的 7 个文件（test_state.py 删除）

## 禁触清单

- 你的 7 个文件之外的一切 tests/ 文件、conftest.py、mocode/**、docs/**、pyproject.toml、
  specs/**

## 诚实出口

若 FakeClock 迁移发现产品代码无法注入时钟（`mocode.core.agent` 直接用 `time.monotonic()`）
：保留 monkeypatch 模块级 time 的写法，逐个测试 docstring 标注接缝，报告"FakeClock 未
完全收敛，清单附后"。不得为此改产品代码。

## 最终报告格式

工单状态表 / commit 清单（hash+message）/ 自测真实结论（命令+输出+退出码）/ 测试数前后
对比 / 删除登记（测试名+理由，RunState 折叠去向必写）/ 偏差与取舍 / 未决问题
