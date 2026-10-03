# Spec BUILTINS · 内置插件测试重构（波次 W1-C，并行组 C）

> 分支 `test/slim-w1c-builtins`；前置：W0 已合并进 main，本分支从该时刻 main 切出
> 深读：`specs/2026-10-03-test-suite-slimming/00-overview.md`、`01-w0-time-control.md`、
> `docs/testing.md`（background job 一节）、`tests/_mcp_fake.py`

## 目标

最高约束放在第一句：**插件层的"契约冒烟"必须留下——每个内置插件的装配、工具注册、
端到端调用路径各至少一条可执行断言**；净减少来自进程内化、删私有白盒、删逐字文案。
目标 ≥120 测试、组耗时 <8s、真子进程仅 6–8 个进程契约测试、组内裸睡清零。

## 你的文件（独占，组外零 diff）

`tests/test_builtin_mcp.py`、`tests/test_builtin_mcp_resources.py`、
`tests/test_builtin_mcp_subscriptions.py`、`tests/test_builtin_mcp_http.py`、
`tests/test_builtin_mcp_codemode.py`、`tests/test_builtin_codemode.py`、
`tests/test_shell_bg.py`、`tests/test_tools.py`、`tests/test_builtin_plugins.py`、
`tests/test_testing.py`、`tests/_mcp_fake.py`。

**文件合并写死**：MCP 5 文件合并为 2 个——

- `tests/test_builtin_mcp.py`：配置加载/命名/exposure/era 协商/in-proc 会话与资源方法/
  工具注册与同步/lifecycle 冒烟（保留 `make_server` in-proc 路径为主）。
- `tests/test_builtin_mcp_process.py`：**真子进程进程契约**——spawn、exit code、
  stdio framing、kill 后 pid 消亡（`wait_until` 替代 `_read_pid` 轮询与 `sleep(0.3)` 同步）。
  这组不超过 8 个测试；原 `test_builtin_mcp_http.py` 的 HTTP 传输线程 fake 并入此处
  （loopback 不动，禁代理 autouse 保留），或并入 test_builtin_mcp.py，二选一写进你的提交说明。
- `tests/test_builtin_mcp_codemode.py` 并入 `tests/test_builtin_codemode.py` 的 mcp 短名组。
- `test_builtin_mcp_resources.py` / `test_builtin_mcp_subscriptions.py` 并入
  `tests/test_builtin_mcp.py`（资源方法 / 订阅重试两组）。

## 工单（按序执行，每项一个 commit，每 commit 独立过门禁）

### T1 进程内化

- in-proc `make_server`（test_builtin_mcp.py:1116）替代子进程跑协议/会话/注册/资源路径；
  `_mcp_fake.py` 保留给 process 契约文件。
- 子进程假件内部 `time.sleep(3)`（:850）改 0.2s，等待侧改 `wait_until`（等待真实发生，
  不猜时长）。
- `time.sleep(0.3)` 当同步（:1763,1767）与 `sleep(0.5)` 后数 warning（:2061,2094）：
  前者改 `wait_until` 等注册表状态；后者改"reasoning 计数随条件变化"的事件驱动断言
  （订阅侧事件到达后断言，不反向睡大觉）。

### T2 shell_bg 时序清零

- `sleep 5`/`sleep 2`/`sleep 0.4` 子进程保留" bounded sleep child"模式（docs/testing.md
  明言的 Windows 安全形态），但测试侧等待全部走事件（`job.done.wait()` / `wait_until`），
  不在测试进程里裸睡。
- `:527` 的 `_NOTIFY_WINDOW + 0.3` 真实时间窗证明"事件没来"：改为"在该回合内可观测的
  事件集"断言——回合结束（say 回包到达）后断言 events 里无 PluginMessage，回合边界
  本身就是事实。
- `_done()` 的 `wait_for(job.done.wait(), 5)` 保留（有界等待）。

### T3 去私有白盒与逐字文案

- 私有属性断言全套改行为：`session._watch_task` → 订阅事件到达/`subscriptions/listen`
  工具回包；`runtime._ctx.tools` → 可调用集合通过 dispatcher 观察；`runtime._codemode_warned`
  / `_registered` → Notice 事件或 codemode 工具回包。
- `test_builtin_codemode.py` 158 → ~60：删 `repr(self._result())` 逐字、`store._max_value_chars`
  私有常量、错误渲染逐字串（`Script error (line 3): ValueError: boom` → 行号+异常类型+
  消息存在性）、`1 ok / 1 failed` 文案 → 结构计数；`TestParallel` 的 Event 双门控
  （:746-762,1500-1543）保留——范本；`sleep(60)` 占位脚本靠 timeout 截止的保留
  （脚本内 sleep 不是测试进程裸睡）。
- `test_builtin_mcp.py` mcp_status 渲染逐字（`- shown: direct`、两空格缩进）→ 解析结构
  （server 名、exposure 值、工具名集合）；`demo: connected (4 tools)` → (name, count) 元组。
- `test_builtin_plugins.py`（Exported/Unknown effort 等）→ 存在性+错误类型；日期文案不在
  其范围但若命中同理处理。
- `test_tools.py` ls/skill 输出文案（`1 directories`、`Base directory:`）→ 结构（条目集合、
  base_dir 值等于注入值）。

### T4 codemode 超时与顺序

- `test_no_deadline_keeps_dispatcher_fallback` 等 1s 量级：确认超时来自脚本内真实等待
  （可接受，计入组 <8s 预算）；`_slow_tool(delay)` 的 delay 尽量压到 0.05 级。
- `test_builtin_codemode.py:1459` 的 `sleep(0.05)` 后 cancel → 事件门控（工具已启动的
  Event）+ 取消。

## 验收（完成前自测，报告给真实结论）

1. `uv run pytest tests/test_builtin_mcp.py tests/test_builtin_mcp_process.py tests/test_builtin_codemode.py tests/test_shell_bg.py tests/test_tools.py tests/test_builtin_plugins.py tests/test_testing.py -q; echo "EXIT=$?"` → 全绿
2. 测试数：最终 7 文件 collect ≥ 120
3. `grep -rn "sleep(" <你的文件>` → 仅子进程假件内部、settle/real_time、脚本内 sleep 语境
4. 真子进程测试计数 ≤ 8（列清单）
5. `git status --short` → 只有你的文件（含新文件与被删的 4 个 MCP 附属文件）

## 禁触清单

你的文件之外的一切（含 conftest.py、其他测试文件、mocode/**、docs/**、
pyproject.toml、specs/**）。

## 诚实出口

若某 in-proc 转换发现 `mcp` SDK 的进程管理层无法内存对接（例如 stdio transport 硬编码
subprocess）：该场景降级留在 process 契约文件并计数（≤8 的额度可加，但需报告量化理由）；
不得借"转换成本高"把大量用例留在子进程路径。产品 bug 上报不越界改。

## 最终报告格式

工单状态表 / commit 清单（hash+message）/ 自测真实结论（命令+输出+退出码）/ 测试数前后
对比 / 删除登记（测试名+理由）/ 最终文件清单与合并去向 / 偏差与取舍 / 未决问题
