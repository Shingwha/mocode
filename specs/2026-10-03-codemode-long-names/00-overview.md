✅ 待填 —— lead 收尾时补一行（`✅ 日期 @ 分支`）

# Spec Group · codemode 调用路径：只认全名（2026-10-03）

> Group: `specs/2026-10-03-codemode-long-names/`
> 来源：用户审视 codemode 两种调用路径（`tools.mcp__anysearch__search(...)` 长名 /
> `tools.search(...)` 短名）后裁决：短名应当不存在，只保留完整长名——多个 server 会有
> 同名短名（search、read），长名才能分清楚不同 server 的工具。
> 探索结论由 lead 三个 Explore agent 全仓核实（file:line 证据见 `ref/` 与本仓源码）；
> worker 不改 spec、不写状态；完成信号 = 最终报告 + 分支 commit。git 是唯一进度权威。

---

## 1. 目标

**最高约束（放第一句）：一个 MCP 工具在整套系统里只有一个可寻地址拼写——它的注册全名
`mcp__<server>__<tool>`。prompt 段、目录三件套（all_tools/search_tools/describe_tool）、
脚本 facade、provider 接口 schema、错误信息，全部只讲这一个词汇表。**

根因定性：问题不是"短名没文档"。`mcp__<server>__` 前缀的存在意义就是分 server，而第三级
解析把它剥掉，凭空造出第二个会撞车的裸名空间——于是必须额外养一套治理机制（只收无歧名、
歧义报错列候选、"精确名永远赢"、docstring/description/README/plugins.md 各写一遍规则）。
这是派生命名空间的税。本组从源头删掉这个派生空间，不留兼容垫片：短名 tier 的唯一消费者
是模型契约面（description 文案 + 脚本解析），无外部 API、无序列化表面，删得干净。

## 2. 已拍板决策（不再反复）

| # | 决策 | 依据 |
|---|---|---|
| D1 | 脚本 `tools.<name>` 解析退化为**精确注册名** → 未命中且名字是内建名 → 门面返回内建；其余抛错。归一化 tier（连字符折叠）与短名 tier 一并删除 | 用户裁决；`toolbox.py:103-132,153-179` 为唯一实现点 |
| D2 | `mcp_servers` prompt 段改列**全名**（`mcp/plugin.py:73-91`）。这是**有意打破** `specs/2026-10-03-codemode-tuning` 组不变式 #3"不启用 codemode 时 prompt 字节零变化"——mcp 一启用即变，本组记档。`_PROMPT_TOOL_NAME_LIMIT=30` 不动 | 用户裁决 Q1；prompt 段是移除短名后唯一的"短名发生场"，模型从这学名字 |
| D3 | unknown-tool 报错**教学化**：候选在**报错时**从 registry 现算（注册全名末段 `__`-tail 与所试名折叠后相等者），唯一候选 → `did you mean 'X'?`，多个 → 全列，零候选 → 现状文案。**不为建议常驻映射表** | 用户裁决 Q2；错误即教学，模型脚本内一轮自愈；现算零稳态成本 |
| D4 | 裸名空间归属内建：`mcp__k__store` 在场时 `tools.store` = 内建 store（原"工具赢短名"案例反转）。MCP 工具一律只以全名可达 | D1 的推论；用户"长名才能分清楚"的直接体现 |
| D5 | 测试**原地改写**，不新增测试文件、不加冗余用例：删 `TestShortNameRule` 整类与 `_mcp_short_name` import；三级断言改写为"精确 + miss 行为"；facade 用例改断言全名可达；`TestMcpShortNames` 更名 `TestMcpTools`；mcp 侧 catalogue 断言由 raw 名改全名。**用例总数允许下降**——删的就是该死断言，门禁看全绿不看数量 | 用户补充约束"非必要不加冗余测试" |
| D6 | 口径：代码/注释/docstring 全英文；spec 组叙事随仓库既有惯例中文；commit 小写祈使句 `feat:`/`fix:`/`docs:`/`test:`，标题一行 + 正文动机 | 仓库惯例 |

## 3. 对旧组决策的推翻

`specs/2026-10-03-codemode-tuning`（✅ 已完成，不改写历史）三条决策被本组推翻：

| 旧决策 | 原文要点 | 推翻理由 |
|---|---|---|
| D2（命名） | MCP 工具 = 折叠全名 + 归一化名 + 无歧名短名（歧义报错并列候选） | 派生短名空间即根因；field-findings P0-3 的原始诉求"两套命名不互通"已被旧 D2 用"全收"缝合，缝合面本身成了问题源 |
| D12（prompt 名单） | prompt 列 raw 名，"帮模型建立短名 ↔ 全名映射"；沙箱三拼法全收 | 短名消失后"建立映射"的存在理由消失；prompt 改讲全名（本组 D2），两个词汇表并存的局面终结 |
| D15（门面回退） | 门面回退 = 注册工具（精确 → 归一化 → 短名）→ 内建回退 | 回退级联退化为"精确注册名 → 内建"；裸名归内建（本组 D4） |

## 4. 波次表（单波单 agent）

| 波 | 分支 | 工单 | 内容 | 前置 | 写范围 |
|---|---|---|---|---|---|
| W1 | `feat/codemode-long-names` | `01-w1-long-names.md` | 删两级解析 + 候选报错 + prompt 全名 + 三文档面 + 测试原地改写 | — | `builtin/codemode/**`、`builtin/mcp/plugin.py`、`docs/plugins.md`、`tests/test_builtin_codemode.py`、`tests/test_builtin_mcp.py` |

每 commit 独立过门禁（至少两个目标测试文件绿 + 全量在报告前绿）；lead pre/post 门禁后
`merge --no-ff`；红了整分支打回，不手改。

## 5. 全局不变量（工单引用，违反即打回）

1. `mocode/core/**` 零 diff；`pyproject.toml`/`uv.lock` 零 diff；不碰 `cli/`、`providers/`、`README.md` 配置表。
2. 全量 `uv run pytest -q` 绿，退出码独立确认（`; echo $?`，禁管道吞）；时间纪律守卫（conftest autouse）自动生效——禁裸 sleep。
3. 无兼容垫片、无别名、无过渡期双轨；发现因此失去消费者的 shim 顺带拆。
4. 写入范围外零 diff；遇阻塞报告后停止，不越界自救。
5. 三个文档面（`description.py` 命名段 / `docs/plugins.md` 命名 bullet / `codemode/README.md`）与新行为逐字一致；`grep -rn "short name" mocode/ docs/plugins.md` 零命中（specs/ 历史除外）。
6. worker 不改 spec、不 merge/push/tag、不碰主检出与其它 worktree。

## 6. 验收（组级）

1. 全量 pytest 绿（基线 506 passed / exit 0，2026-10-03 master），worker 报告 pre/post 用例数与删除清单逐条对账 D5。
2. `grep -rn "short name" mocode/ docs/plugins.md` → 仅 specs/ 历史命中。
3. 行为抽验（lead 终验跑）：`tools["mcp__k__bash"]`/`tools.mcp__k__bash` 全名可调；`tools.bash`（MCP 短名）报 `unknown tool 'bash'; did you mean 'mcp__k__bash'?`；两 server 同名时 miss 列两个全名；`tools.read`/`tools.bash` 本地裸名照旧；`mcp__k__store` 在场时 `tools.store` 仍是内建 store。
4. prompt 段 render 输出含全名（结构性断言，不钉整句）。

## 7. 环境

Windows 11 + Git Bash；uv + Python 3.13；路径含非 ASCII 目录名（桌面）；无浏览器；
pytest 每例 30s 超时 autouse；Windows asyncio 子进程 pipe 析容
`PytestUnraisableExceptionWarning` 为已知噪音。
