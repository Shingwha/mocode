# 删除与合并登记（W2 汇总，lead 汇编自各组最终报告）

> 规则：每条删除/合并都注明测试名、去向（合并到哪条测试 / 被哪条覆盖）与理由。
> 计数口径：pytest collect 数（参数化行 = 测试）。五轮之后全量 1056 → 500。

## W1-A（内核，256 → 235）

| 文件 | 删除的测试 | 去向 | 理由 |
|---|---|---|---|
| test_agent_loop/test_retry | `test_a_tool_error_result_carries_the_error_prefix`、`test_an_unknown_tool_result_carries_the_error_prefix`、`test_a_timeout_result_carries_the_timeout_prefix` | `test_the_persisted_result_says_what_happened[3 行]` | 同矩阵参数化，error_code 断言集中一处 |
| test_agent_loop/test_retry | 超时优先级三测（tool-policy/call-level/fallthrough） | `test_the_effective_timeout_is_call_over_tool_over_config[3 行]` | 同矩阵参数化 |
| test_retry | Retry-After 五测（numeric/case-insensitive/raw/date-fallback/honor-off） | `test_a_numeric_header_is_slept_instead_of_backoff[5 行]` | 同矩阵参数化；秒数字面量改性质断言 |
| test_agent_loop | `test_text_arrives_incrementally` + `test_reasoning_deltas_are_separate_from_text` | `test_text_and_reasoning_arrive_separately_and_incrementally` | 同脚本重复 |
| test_agent_loop/test_events | 6 组同形（seq/monotonic、since/replay、sync/async run 路径、summary_key、xml format、render beats callable） | 各自合并为一条双断言测试 | 同形重复 |
| test_agent_loop | `test_error_code_is_reported`、`test_unknown_tool_is_reported_as_not_found`、`test_a_plain_string_result_carries_no_details`、`test_max_iterations_stops_a_tool_loop`、`test_a_system_prompt_rewrite_sticks_for_the_rest_of_the_run` | 并入 failure_statuses 矩阵 / 前缀矩阵 / details 测试 / stop_reason 测试 / no-leak 测试 | 被他处覆盖 |
| test_agent_loop | `test_iteration_and_tool_count_track_the_run` | `RunFinished.tool_calls_made` + mirror 测试 | 唯一独占断言是私有计数器（T3 处理） |
| test_dispatch | `ghost`（not_found）参数行、`test_a_vetoed_call_is_denied_for_both_origins` | 事件与持久化两侧前缀矩阵 | 被他处覆盖 |
| test_core | `test_state_starts_idle`、`test_summary_key_defaults_to_the_first_param` | 与 test_core 装配测试重复 | 跨文件重复 |
| test_state.py | 全部 8 个（RunState 折叠） | **迁移**（非删除）至 `tests/test_events.py::TestContentSoftLimit`，文件 `git rm` | 文件合并 |

## W1-B（宿主+CLI，347 → 371→357）

| 文件 | 删除的测试 | 去向 | 理由 |
|---|---|---|---|
| test_config.py | TestModelEntry 12 个微测 | `test_from_dict_coerces_or_drops`（20 参数） | 同入口参数化，定位靠参数 id |
| test_session.py | PluginMessagesField 3 测 + FrozenRequestFields 若干 | 参数化矩阵 | 同入口参数化 |
| test_plugins.py | missing-name / broken-json 两测 | `test_a_malformed_manifest_rejects_the_plugin[2 行]` | 同规则 |
| test_provider.py | RequestShape 四个单字段微测 | `test_one_field_of_the_request[4 行]` | 同入口参数化 |

## W1-C（内置插件，441 → 406）

| 文件 | 删除的测试 | 去向 | 理由 |
|---|---|---|---|
| test_builtin_codemode.py | 同规则参数化（8+4+3+7+6 组） | 各自的规则测试各保留一次断言 | 参数化各自重复同一条规则 |
| test_builtin_codemode.py | DESCRIPTION 术语清单 ~60 个精确串 | 从 `build_env()` 推导可绑定名 | 改措辞不再失败、改名仍失败（契约） |
| 5 个 MCP 文件 | 合并为 `test_builtin_mcp.py` + `test_builtin_mcp_process.py`；mcp_codemode 并入 codemode | 见组 spec 写死的合并清单 | 文件合并（in-proc 化后附属文件无存在理由） |
| test_builtin_mcp.py | spawn-failure 类 | process 契约文件保留 | 进程契约 |

## W1-R（收敛轮，563）

**W1-R A（235 → 155，登记 108 项节选）**

| 文件 | 去向 | 理由 |
|---|---|---|
| test_core.py（60→32） | 构造断言并入 `test_the_constructor_takes_what_it_is_given`；容器表面并入 `test_build_renders_the_prompt_tree`；pin 契约并入 `test_a_pinned_section_holds_its_bytes_until_refreshed`；schema checker 矩阵合并为 `test_the_checker_walks_the_whole_object_node` / `test_combinators_accept_any_branch_and_reject_ambiguity`；availability ValueError **移入** core 构造校验 | 同形合并 + 移动非删除 |
| test_dispatch.py（49→31） | 结果断言并入 `test_a_call_runs_the_tool_and_publishes_its_events`；truncate 并入 policy 覆盖测试；attribution 矩阵并入 `test_the_attribution_matrix_decides_quietly_or_loudly`；`ghost`/vetoed 参数行并入事件与持久化前缀矩阵 | 同规则矩阵 + 被他处覆盖 |
| test_agent_loop.py（60→45） | tool-events/summary、state/live、prompt 重写三个钩子点、iteration budget、messages/tools 快照、after_response、emit、provider 失败、two readers、sync-tool cancel 各并为一条 | 同脚本/同钩子点合并 |
| test_events.py（26→20） | RunState 折叠组并入 `test_the_lifecycle_folds_its_ending`、`test_a_tool_call_runs_from_started_to_finished`、`test_the_seq_guard_passes_live_events_and_dedups_replays` 等 | 同规则合并 |
| test_retry.py（24→15） | 重放窗口、耗尽、provider policy、backoff 曲线、deadline 开关、budget-bounded-wait 各并为一条 | 同规则合并 |
| test_channel.py（16→12） | since/live、history、close-ends 各并为一条；publishing without readers 被 seq 测试覆盖 | 同形合并 + 覆盖 |

**W1-R B（371 → 248，逐文件登记见各组报告）**：config 34 项（ModelEntry 19 参数行并入 9 条规则分组行，case 全保留）、conversations 16 项、session 10 项、plugins 12 项、provider 6 项、display 4 项、lines 6 项（含确凿重复的 timeout duration 行）、plugin_install 7 项、commands 9 项、runtime 9 项、plugin_env 4 项、cache_protect 4 项、plugin_message 1 项。全部为同契约多入口合并，参数 case 逐一保留。

**W1-R C（406 → 160）**：codemode 删除登记（import 形态/sandbox 代数/ToolBox/Result/facade/deadline 两层的同规则合并）；mcp（loader 矩阵、exposure 规则、prompt section、reconcile 同族合并）；shell_bg（ring bounds/清理路径/限额家族）；tools（cd-env 持久化、超时两层）；`_mcp_fake.py` 删除无使用者的 `ECHO_SERVER` 与 `stdio_entry`（铁律 5）。

## W1-T（终调轮，563 → 475）

| 组 | 删除/合并 | 去向 |
|---|---|---|
| A（155→152） | dispatch timeout-parity 行并入参数矩阵；provenance 两通道并入 `test_events_say_who_asked_and_what_they_are_nested_in`；两个遮蔽式重复定义删后一份；schema checker bool 行折入类型行 | 各合并后测试；死代码直删 |
| B（248→200） | 逐文件贴底线合并（44 项，编号见组报告）：resume 契约两入口、discover 两形态、provider/REPL 入口对、id-only lookup、roundtrip、config 同入口对、display/lines 确凿重复、install exit codes、plugin_install 同名重复定义、env/cli-plugin/message 同形对 | 各合并后测试 |
| C（160→123） | codemode 11 项、mcp 11 项（含跨文件去重：codemode notice 契约在 codemode 侧保留并移植 count 断言，mcp 侧副本删除）、shell_bg 5 项、tools 6 项、builtin_plugins 4 项 | 各合并后测试 |

## W2（500，+25 新增）

| 文件 | 新增 | 内容 |
|---|---|---|
| tests/test_transcript.py | 17 | 消息形状契约：构造省略空字段、多模态提取、坏 JSON 读作无参、跨历史 id 查找 |
| tests/test_host_tools.py | 8 | read_json/write_json 往返与坏文件、decode_bytes 双编码、one_line 截断 |

## 底线核对（W2 T5 实测）

核心 11 文件全部 ≥ 底线：agent_loop 45、dispatch 30、core 30、events 20、channel 12、
retry 15、conversations 35、plugins 25、runtime 12、session 12、provider 12（below-floor: 0）。
契约冒烟：每个内置插件的装配/工具注册/端到端调用路径各至少一条；schema checker 矩阵、
prompt pin、turn/cancel 并发范本、TestParallel 双门控、真子进程进程契约 11 条均在位。
