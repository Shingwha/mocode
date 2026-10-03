# 06 — 二轮收敛工单（W1-R，并行三组，分支不变追加 commit）

> 组：`specs/2026-10-03-test-suite-slimming/` · 本工单由 lead 裁定后追加，原有 02/03/04 继续有效。
> 波次 W1-R：模式同 W1（并行三组），**分支与 worktree 不变**（各分支当前已全部合并进 master，
> 在各自分支 HEAD 上追加收敛 commit；三分支基线相同无漂移）。W0/W1 已交付的全局不变量、
> 禁触清单、门禁命令、Git 协议全部继续适用。

## 为什么有这一轮

W1 三组合并后实测（master，`uv run pytest -q`）：

- **1012 passed，44.60s，EXIT=0**；BARE SLEEPS 50 条全部归属产品代码，`tests/` 侧零条目。
- 三组测试数 235 / 371 / 406，均远超总纲波次表的 ≥140 / ≥150 / ≥120 底线；
  但总纲目标 **1006 → 450±50、全量 <15s** 是已拍板决策，实测缺口：
  数量 −500 以上，耗时 −30s 以上。
- 判定：W1 的脆点清理（裸睡/白盒/文案/墙钟）全面达成，"精简"维度未达成——
  三组都选择了保守保留（去重止于底线之上，无负责总量上限的角色）。本轮补上。

**本轮最高约束：行为保护只减量不减级。** 删除的每一处都必须是：同规则重复、
被他处覆盖、参数化拆细过度的行、无脆弱性也无契约价值的微小测试；每个不变量
至少留一条可执行断言。

## 出口（合并后全量，lead 合并时核对）

- 全量 **450–500 个**，**< 15s**（三组合计 ≤470，给 W2 补的 ~25 个空洞测试留位）；
- 每文件底线表（总纲 §保留底线）继续有效，**贴底线是正常状态**，不是意外；
- 组耗时：W1-A ≤2.5s、W1-B ≤3.5s、W1-C ≤8s；
- W1 已清零的纪律保持：`tests/` 侧裸睡零、私有属性断言零、提示词文案断言零
  （display/lines escape golden 豁免除外）、墙钟断言零。

## 各文件收敛目标（当前实测 → 目标区间）

| 组 | 文件 | 当前 | 目标 | 底线 |
|---|---|---|---|---|
| A | test_agent_loop.py | 60 | 45–50 | ≥45 |
| A | test_dispatch.py | 49 | 30–35 | ≥30 |
| A | test_core.py | 60 | 30–35 | ≥30 |
| A | test_events.py | 26 | 20–23 | ≥20 |
| A | test_retry.py | 24 | 15–18 | ≥15 |
| A | test_channel.py | 16 | 12–14 | ≥12 |
| B | test_config.py | 61 | 22–30 | — |
| B | test_conversations.py | 55 | 35–42 | ≥35 |
| B | test_plugins.py | 41 | 25–30 | ≥25 |
| B | test_provider.py | 18 | 12–15 | ≥12 |
| B | test_display.py | 24 | 16–20 | 豁免可轻砍 |
| B | test_lines.py | 25 | 18–22 | 豁免可轻砍 |
| B | test_plugin_install.py | 22 | 12–16 | — |
| B | test_commands.py | 21 | 12–16 | — |
| B | test_session.py | 24 | 12–16 | ≥12 |
| B | test_runtime.py | 25 | 12–16 | ≥12 |
| B | test_cache_protect.py | 18 | 12–15 | — |
| B | test_plugin_env.py | 14 | 8–11 | — |
| B | test_cli_plugin.py | 12 | 8–11 | — |
| B | test_plugin_message.py | 11 | 8–10 | — |
| C | test_builtin_codemode.py | 159 | 55–70 | — |
| C | test_builtin_mcp.py | 152 | 35–45 | — |
| C | test_shell_bg.py | 33 | 15–20 | — |
| C | test_tools.py | 27 | 15–18 | — |
| C | test_builtin_mcp_process.py | 11 | 11（保留） | 进程契约 |
| C | test_builtin_plugins.py | 17 | 10–13 | — |
| C | test_testing.py | 7 | 7（保留） | — |

目标区间内取何值由各 agent 按"每条断言不可再合并"判断；低于区间下限 = 砍到契约之下，
高于上限 = 没收敛干净，两者都需在报告里给理由。

## 慢项压缩要求（当前 top 慢项，逐项处置后写进报告）

| 秒 | 测试 | 处置 |
|---|---|---|
| 4.03 / 2.03 | mcp TestWatchTools 退避两条 | 若产品退避等待无注入点：保留并量化；有注入点（产品模块级 sleep 入口）可 monkeypatch 即时化，报告声明所绕过的产品行为 |
| 2.62 | mcp_process slow child timeout | 假慢孩子的 sleep 压到 0.1–0.3s；close/kill 类窗口同理 |
| 2.03 / 2.01 / 1.10 / 1.01 | mcp 连接失败/静默对等窗 | 等的是连接 bound 的，压窗或参数化合并同类 |
| 1.44 / 1.36 / 1.31 / 1.13×2 / 1.06 / 1.04 / 0.91 | shell_bg completion notification 与 limits | bounded sleep child 的孩子时长压到 0.2–0.5s；测试侧等待一律事件门控；回合边界证明"事件没来"的形态保留 |
| 1.01×3 | codemode deadline 系列 | 脚本内 deadline 压到 0.1–0.2s；TestParallel 双门控范本保留 |

## 各组工单（在原波次 spec 的纪律上追加）

### W1-A-R（分支 test/slim-w1a-kernel，worktree 不变）

T1 内核微重复二刀：test_core 的构造/容器表面矩阵、test_agent_loop 的请求/事件
   同形断言，合并规则优先。
T2 贴底线收敛至出口表；agent_loop ≥45、dispatch ≥30、core ≥30、events ≥20、
    retry ≥15、channel ≥12 全部满足。
T3 组门禁 + 组耗时 ≤2.5s；裸睡/私有/文案/墙钟四项保持零。

### W1-B-R（分支 test/slim-w1b-host-cli，worktree 不变）

T1 config 61 → 22–30（ModelEntry 矩阵参数行合并、同入口微测合并，失败定位靠 id）。
T2 CLI 侧文件按出口表收敛；display/lines 的 escape golden 只删确凿重复，
    不删 offset 机制契约；cache_protect 结构解析助手保留、参数行收敛。
T3 核心 5 文件贴底线（conversations ≥35、plugins ≥25、runtime/session/provider ≥12）。
T4 组门禁 + 组耗时 ≤3.5s；四项纪律保持零；解决 W1-B 遗留未决 1（参数化 collect
    膨胀——本轮以净减收口）。

### W1-C-R（分支 test/slim-w1c-builtins，worktree 不变）

T1 codemode 159 → 55–70：执行原 04 spec T3 写死的 ~60 口径（sandbox 规则矩阵
    合并同规则、错误渲染结构断言保持、DESCRIPTION 可推导覆盖保留、TestParallel
    双门控范本保留、脚本内 sleep 占位保留但时长 0.1–0.2s）。
T2 mcp 152 → 35–45：配置加载/naming/exposure/era 协商/in-proc 会话资源方法/
    工具注册同步/lifecycle 冒烟各留最少代表；同形状矩阵合并；HTTP fake 条目字段
    分组保持在 1–2 个断言内。
T3 shell_bg/tools/builtin_plugins 按出口表 + 慢项表处置；真子进程契约 8 个测试
    保持（collect 11 含参数行）；`tests/_mcp_fake.py` 无使用者的 ECHO_SERVER 顺带删除。
T4 组门禁 + 组耗时 ≤8s（退避两条若无法注入允许保留，量化写进报告）。

## 验收（各组自测，报告给真实结论）

1. 组门禁（组文件全列）绿，EXIT 独立确认；
2. 组 collect 数落在出口表对应区间；
3. 组耗时实测 ≤ 出口值；
4. `grep -rn "sleep("` 组文件 → 仅子进程假件内部/settle/real_time/脚本内语境；
5. `git diff <本轮起点> --stat` 只含本组文件；`git diff <本轮起点> -- mocode/` 空；
6. 删除登记：测试名 + 理由 + 去向（合并到哪条/被哪条覆盖）。

## 禁触

同原波次（conftest.py、pyproject.toml、mocode/**、docs/**、specs/**、他组测试文件
一律禁触；本轮不得新增兼容 shim；铁律 5 发现孤儿入口可顺带拆）。

## 最终报告格式

工单状态表 / commit 清单（hash+message）/ 自测真实结论（命令+输出+退出码）/
测试数前后对比（每文件一行）/ 慢项处置表 / 删除登记（测试名+理由+去向）/ 偏差与取舍 /
未决问题。阻塞即停，不越界自救。
