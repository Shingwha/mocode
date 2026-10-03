# Spec TIME-CTROL · 时序地基（波次 W0）

> 分支 `test/slim-w0-time-control`；前置：无（从合并时刻最新 main 切）
> 深读：`specs/2026-10-03-test-suite-slimming/00-overview.md`、`tests/conftest.py`、
> `mocode/testing/__init__.py`、`docs/testing.md`

## 目标

最高约束放在第一句：**不改任何测试文件的一个断言，现有 1006 个测试必须原样全绿**。
本波只交付所有后续波次要用的时序/断言基建，并把"零裸睡"纪律变成机制。

## 工单（按序执行，每项一个 commit）

### T1 conftest 新增时序 API（只新增，不改名、不改既有助手语义）

新增（名字写死，W1 三组将直接 import）：

- `settle(seconds=0.01)`：受批短睡，真子进程观察窗唯一合法入口。内部就是
  `asyncio.sleep`，但走 conftest 的赦免名单（守卫不拦它）。加中文 docstring 说明它只用于
  "等待是真被子测行为"的场景（子进程产出、看门狗时限），不当同步用。
- `wait_until(predicate, *, bound: float = 5.0, step: float = 0.01, what: str = "")`：
  条件轮询；超时抛 `AssertionError`，消息里带 *what* 和耗时。统一替换现有的 4 处本地
  轮询（`tests/test_builtin_mcp_subscriptions.py:73 wait_until`、
  `tests/test_builtin_mcp_codemode.py:70 _wait_for`、`test_builtin_mcp.py:1062 _read_pid/
  :1096 wait_gone`、`test_builtin_mcp_codemode.py:240` 的手写循环——它们 W1-C 会改调用点，
  你无需动那些文件）。
- `FakeClock` + `advance(clock, dt)`：公共假单调时钟，供 `test_retry.py` 的 `_Clock` 和
  `test_agent_loop.py` 的 `FastClock/Clock` 在 W1 收敛（W0 不碰那两个文件，只提供类）。
  接口对齐现有用法：`monotonic()` 方法、`now` 属性可推进。
- `real_time()`：上下文管理器，进入后裸睡赦免；退出恢复。docstring 写明这是逃生舱，
  普通测试不该见到它。

### T2 autouse 守卫，recording 模式上线

在 `tests/conftest.py` 加 autouse fixture（名字 `_sleep_guard`）：

- monkeypatch `time.sleep` 与 `asyncio.sleep` 为记录版：被调用时记一条
  `(test nodeid, caller 文件:行号, 秒数)` 到模块级清单；在会话结束时（pytest 的
  `pytest_terminal_summary` hook 或 fixture 汇总）输出到 terminal summary 一段
  "BARE SLEEPS" 清单。**recording 模式不失败任何测试**——现有测试有合法裸睡
  （如 `test_dispatch.py` 工具体 `time.sleep(1)`），W1 各组清零后 W2 翻成硬失败。
- 赦免：`settle()` 内部、`real_time()` 上下文内的调用不计。
- 注意 patch 的作用域：测试进程内有效；子进程（MCP fake server）不受影响，天然豁免。

### T3 pyproject.toml 加超时门禁

- dev 组加 `pytest-timeout`（uv add --dev pytest-timeout 或手改 pyproject 后 uv sync）。
- `[tool.pytest.ini_options]` 加 `timeout = 30`，保留 `asyncio_mode = "auto"`。
- 不要注册 marker，不要加 testpaths。

## 验收（完成前自测，报告给真实结论）

1. `uv run pytest -q; echo "EXIT=$?"` → 1006 个测试全绿，退出码 0（现有断言零改动是前提）
2. `uv run pytest -q 2>&1 | grep -A3 "BARE SLEEPS"` → 记录清单真实输出（证明 recording 生效）
3. `git diff main --stat -- mocode/ docs/` → 空（产品代码与文档都没碰）
4. 新 API 的自我证明：在 conftest 里为新助手写最小 doctest 式验证不算测试文件，可用
   `python -c "import …"` 快速证明 `FakeClock/advance/wait_until` 可导入可调用即可上；
   正式契约测试留给 W2。**不要新建测试文件**（测试文件所有权见总纲）。

## 禁触清单

- `tests/` 下除 `conftest.py` 外的一切文件（一个断言都不许改）
- `mocode/**`、`docs/**`、`AGENTS.md`、任何 spec 文件

## 诚实出口

若 `pytest-timeout` 与 pytest 9 的兼容性导致安装/运作出问题：降级为
`pip`/`uv add --dev pytest-timeout` 的具体可用版本，并在报告里说明版本号；仍不行则
只保留 conftest 的 `wait_until` 自实现超时（每个等待自带 bound），pyproject 段回到
原样，报告"超时门禁未落地，交给 W2"。不许为了装上而改任何测试文件。

## 最终报告格式

工单状态表（T1/T2/T3 各 commit hash+message）/ 自测真实结论（三条命令的真实输出与退出码）/
BARE SLEEPS 记录清单的测试数量与 top 命中 / 偏差与取舍 / 未决问题
