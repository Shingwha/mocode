# 07 — 终调微砍工单（W1-T，并行三组，分支不变追加 commit）
> 状态：✅ 2026-10-03（三分支合计 563→475，W2 补 25 → 500）

> 组：`specs/2026-10-03-test-suite-slimming/` · lead 裁定的补漏波次。
> W1-R 三分支已全部合并进 master（实测 **563 passed / 15.89s / EXIT=0**，
> tests/ 侧裸睡 0）。总纲目标 450±50、全量 <15s 仍差最后一程：
> 数量 −70 左右、耗时 −1s。本轮是**微砍**：继续合并同规则与参数行，
> 严禁触碰任何一条仍有独立契约的断言。

## 本轮最高约束

行为保护只减量不减级：删的每处都必须是"可验证的重复"（同规则矩阵、
参数化拆细过度的行、被他处完整覆盖）。每个不变量至少一条可执行断言。
底线表（总纲 §保留底线）逐文件继续有效，**贴底线是预期状态**。

## 出口（合并后全量落在 480–500，W2 还将补 ~20 个契约测试）

| 组 | 当前 | 出口 | 说明 |
|---|---|---|---|
| W1-A | 155 | **152** | dispatch 31→30、core 32→30（各文件底线已贴，仅此两处余量） |
| W1-B | 248 | **~200** | 核心 5 文件贴底线（conversations 35 / plugins 25 / runtime 12 / session 12 / provider 12 = 96）；其余 9 文件合计 ~104：config ~20、display ~16、lines ~15、plugin_install ~11、commands ~10、cache_protect ~12、plugin_env ~7、cli_plugin ~7、plugin_message ~6 |
| W1-C | 160 | **~120–125** | codemode ~45、mcp ~30、shell_bg ~13、tools ~10、builtin_plugins ~7；**process 11 与 testing 7 保留**（真子进程契约 / MockProvider 契约） |

## 各组工单

### W1-A-T（分支 test/slim-w1a-kernel）
- T1：dispatch 31→30、core 32→30 各砍一处确凿重复（找形似而断言等价的两条合并）；
  其余四个文件已贴底线，不动。
- T2：组门禁绿（155→152 passed）+ 四项纪律保持零。

### W1-B-T（分支 test/slim-w1b-host-cli）
- T1：核心 5 文件贴底线收敛（可砍 conversations 4 / plugins 4 / runtime 4 / session 2；
  provider 已贴底）。合并对象优先：同一契约的多入口断言、场景等价的正反两面。
- T2：其余 9 文件按上出口表砍到 ~104（display/lines 的 escape golden 集仍豁免，
  只删确凿同形重复；cache_protect 结构解析助手保留）。
- T3：组门禁绿（248→~200）+ 组耗时 ≤3.5s + 四项纪律零。

### W1-C-T（分支 test/slim-w1c-builtins）
- T1：codemode 56→~45、mcp 41→~30：继续合并同规则矩阵（sandbox 规则、
  配置加载、naming/exposure、prompt section、tool mapping 各组内找等价行）。
- T2：shell_bg 18→~13、tools 16→~10、builtin_plugins 11→~7：合并等价
  行为路径（cd/env 持久化型、ring bounds 型、AGENTS.md rebuild 型等）。
- T3：组门禁绿（160→~120-125）+ 组耗时 ≤10s（refused-connection 的本机
  2s OS 常数已量化保留，W2 文档登记）+ 四项纪律零。
- process 11 与 testing 7、契约冒烟（每插件装配/注册/端到端各至少一条）、
  TestParallel 双门控范本、deadline 压缩形态一律保留。

## 通用纪律（与 02/03/04/06 相同）

committer 各自独立过组门禁再进下一项；写入范围仅本组文件；mocode/**、
conftest.py、pyproject.toml、docs/**、specs/**、他组文件零 diff；
提交信息英文 conventional、注释中文；删除登记（测试名+理由+去向）写进最终报告；
阻塞即停不越界自救；spec 归 lead 所有不许改。

## 验收

1. 组门禁（组文件全列）绿，EXIT 独立确认；
2. collect 落出口（A=152、B≈200±8、C≈122±5）；
3. 组耗时：A ≤2.5s、B ≤3.5s、C ≤10s；
4. `grep -rn "sleep("` 组文件 → 仅子进程假件内部/脚本内/settle 语境；四项纪律零命中；
5. `git diff <分支上 W1-R 终点> --name-only` 只含本组文件，`-- mocode/` 空；
6. 删除登记：测试名 + 理由 + 去向。

## 最终报告格式

工单状态表 / commit 清单 / 自测真实结论（命令+输出+退出码）/ 每文件前后对比 /
删除登记 / 偏差与取舍 / 未决问题。
