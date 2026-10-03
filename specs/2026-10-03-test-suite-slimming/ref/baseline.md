# 基线（lead 采集，2026-10-03）

## 总量

- `uv run pytest -q`：**1056 passed，3 warnings，68.45s**（`time` 实测 1m10.9s 含进程启动）
- 30 个测试文件 + conftest.py，约 1.6 万行；pytest 配置仅 `asyncio_mode = "auto"`
- 无外网调用；零 xfail/skipif；无 CI workflows

## 逐文件测试数（--collect-only）

| 文件 | 数 | | 文件 | 数 |
|---|---|---|---|---|
| test_builtin_codemode.py | 180 | | test_events.py | 18 |
| test_builtin_mcp.py | 117 | | test_provider.py | 18 |
| test_agent_loop.py | 72 | | test_cache_protect.py | 18 |
| test_core.py | 64 | | test_channel.py | 18 |
| test_conversations.py | 55 | | test_builtin_plugins.py | 17 |
| test_config.py | 53 | | test_plugin_env.py | 14 |
| test_dispatch.py | 50 | | test_builtin_mcp_subscriptions.py | 12 |
| test_plugins.py | 41 | | test_cli_plugin.py | 12 |
| test_builtin_mcp_resources.py | 35 | | test_plugin_message.py | 11 |
| test_shell_bg.py | 33 | | test_state.py | 8 |
| test_tools.py | 27 | | test_builtin_mcp_http.py | 6 |
| test_retry.py | 26 | | test_builtin_mcp_codemode.py | 6 |
| test_lines.py | 25 | | test_testing.py | 7 |
| test_runtime.py | 25 | | | |
| test_display.py | 24 | | | |
| test_session.py | 21 | | | |
| test_commands.py | 21 | | | |

按波次汇总：W1-A 7 文件 = 256；W1-B 14 文件 = 347；W1-C 11 文件 = 441；
conftest 无测试。

## 最慢单项（top 12，--durations=25 节选）

| 秒 | 测试 |
|---|---|
| 4.04 | test_builtin_mcp_subscriptions.py::TestWatchTools::test_the_backoff_doubles_and_an_event_resets_it |
| 2.60 | test_builtin_mcp.py::TestModernSession::test_a_slow_call_times_out |
| 2.04 | test_builtin_mcp.py::TestEraNegotiation::test_a_silent_server_fails_the_connect_under_a_bound |
| 2.03 | test_builtin_mcp_http.py::TestConnectionFailures::test_a_refused_connection_is_a_transport_error |
| 2.01 | test_builtin_mcp_subscriptions.py::TestWatchTools::test_a_dropped_stream_re_listens_after_the_backoff |
| 1.43 | test_shell_bg.py 完成公告 ×4（1.43/1.42/1.34/0.96） |
| 1.13 | test_shell_bg.py::TestLimits::test_a_background_deadline_times_the_job_out |
| 1.02 | test_builtin_mcp.py::TestPluginLifecycle::test_a_server_still_connecting_keeps_the_one_line_form |
| 1.02 | test_tools.py::TestBashSession::test_timeout_kills_the_command |
| 1.00 | test_builtin_codemode.py::TestRunTool ×3（deadline 系列） |

异常项：`test_dispatch.py` 有 5 个 0.94–0.95s 的 **teardown**（TestToolPolicy /
TestOutcomeParity / TestBareCoreDispatcher 的 timeout 测试）——teardown 耗时与被测超时
同量级，疑似 teardown 里等真实计时器收尾，留给 W1-A 顺带查（不得改产品代码）。

## 脆弱点库存（explore 双 agent 核实）

| 类别 | 数量 | 位置 |
|---|---|---|
| 提示词/prompt 文案断言 | ~30 行 | test_builtin_plugins.py:47-97、test_conversations.py:562,755-756、test_builtin_mcp.py:1860,1883、test_core.py:87-96,116,149-166、test_plugins.py:382、test_builtin_mcp_codemode.py:119-120 |
| 渲染/通知/错误逐字文案 | ~100 行 | test_display.py、test_lines.py、test_session.py（16 行 md 标题）、test_builtin_codemode.py（~30）、test_builtin_mcp.py（~15）、test_cache_protect.py（~10）、test_commands.py、test_tools.py、test_shell_bg.py、test_plugin_install.py 等 |
| sleep 当同步（无任何事件/条件） | 6 处 | test_builtin_mcp.py:1763,1767,2061,2094、test_builtin_mcp_codemode.py:253、test_builtin_codemode.py:1459 |
| 真实 wall-clock 轮询/子进程 sleep | ~25 处 | test_builtin_mcp.py（3s/0.5s/0.3s/pid 轮询）、test_shell_bg.py（sleep 5/2/0.4×9）、test_builtin_mcp_http.py:286、subscriptions/codemode 轮询助手 |
| 私有属性断言 | ~35 行 | test_builtin_mcp_subscriptions.py:198-311（`_watch_task`）、test_builtin_mcp_resources.py:271-424（`_ctx.tools`/`_codemode_warned`）、test_builtin_mcp.py:1758-2133、test_builtin_codemode.py:987,621、test_conversations.py:665、test_cli_plugin.py:220 |
| 算法字面量/调用序钉死 | ~45 行 | test_retry.py:191-241（0.3/0.7/2.0s）、test_builtin_codemode.py:740,1494（顺序列表）、test_agent_loop.py:443-737（`provider.calls[N]`）、test_cache_protect.py:32-259（按序号钉请求负载）、test_provider.py:73-255（wire payload） |
| 计时器/超时值写死在文案 | 3 处 | test_shell_bg.py:527（_NOTIFY_WINDOW+0.3）、test_lines.py:80、test_builtin_mcp.py:1787 |

正面教材（保留精神，勿删）：test_agent_loop.py FastClock（:552-630）、test_display.py
Event 门控 `_run_parallel`（:284-343）、test_retry.py `_patch_sleep`、test_agent_loop.py
死锁式并发证明（:282-288 明拒 stopwatch 断言）。

## 覆盖空洞

- `mocode/core/transcript.py`：零直接测试（history/export/replay/provider 协议共用格式）
- `mocode/host/prompt.py build_system_prompt`、`host/io.py`、`host/text.py`：零直接测试
- `mocode/cli/input.py`、`cli/text.py`、`cli/dialogs.py`：基本裸奔（终端交互，测试成本高，
  本组不补，登记遗留）
