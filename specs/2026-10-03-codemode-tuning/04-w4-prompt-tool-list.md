⏸️ 待派工 — 前置 W3 合并

# Spec 04 · W4：`mcp_servers` 段列 server + 工具名（波次 W4）

> 分支 `feat/mcp-prompt-tools`；worktree 由 lead 建。
> 前置：W3 已合并 master。深读：`00-overview.md` §2 D12、`builtin/mcp/plugin.py`
> 的 `_render_mcp_servers`、`01-w1-script-surface.md`（沙箱短名规则——名单与它配套）。
> 绝不操作主检出，绝不 merge/push/tag/checkout master。

## 0. 目标

`mcp_servers` prompt 段从"每个 server 一行"升级为"server + 该 server 的工具名清单"：
**只列名字，不进 schema**；模型要用哪个工具，经 codemode 的 `describe_tool(name)` 按需取
字段与说明。名单是目录，不是手册。

## 1. 设计决策（D12，已拍板）

1. 渲染数据源：`McpRuntime` 的注册表（`_registered`/`assignments`，raw 工具名）——只列
   **enabled、已连接、非 hidden** 的 server；未连接/连接中的 server 维持现状行（无名单）。
2. 每行格式沿用现段风格：`- {name}: {direct|codemode} — {description}` 后接工具名清单
   （单行逗号连接，或紧凑缩进行——worker 按现段体例定，报告里说明）。
3. **上限**：每 server 最多列 30 个名字，超出追加 `… +{K} more (search_tools() in a
   codemode script)`。
4. **staleness**：段在会话开始（materialize）定稿——订阅新增的工具**不在名单里**但脚本
   立即可用；这半句必须写进段尾或 docs，避免模型把名单当全集。
5. direct server 也列名（模型在工具接口里看到的是折叠全名，名单帮它建立"短名 ↔ 全名"
   映射）；codemode server 的工具本就不在工具接口里，名单是它们唯一的曝光面。
6. hidden server 维持整行跳过。

## 2. 工单（一个 commit）

### T1 名单渲染
- 按 §1 实现（`plugin.py` 渲染闭包读 runtime 注册表；注意插件实例无状态，数据全部来
  自 per-conversation runtime）。
- 测试：两个 server 各带若干工具 → 各自名单正确；超 30 截断 + K 计数；未连接 server
  无名单；hidden server 整行消失；prompt section 冻结语义不破坏（沿用 `TestPluginLifecycle`
  的断言风格）。
- `docs/plugins.md` mcp 节同步（§0 的"名单是目录"语义 + staleness 半句 + describe_tool
  按需取用法的说明）。
- commit：`feat(mcp): list server tool names in the prompt section`

## 3. 验收

1. `uv run pytest -q; echo "EXIT: $?"` 全绿、退出码 0、数量 ≥ W3 合并值。
2. `git diff --stat <W3 合并点>` 只含 `builtin/mcp/plugin.py`、`tests/test_builtin_mcp.py`、`docs/plugins.md`；core/host.py/codemode 包/pyproject/uv.lock 零 diff。
3. `import mocode` <1ms。
4. 段字节数有界：报告给出"1 个 server × 40 工具"场景下该段的字符数（截断生效证据）。

## 4. 禁触清单

`mocode/core/**`；`mocode/host/plugin/host.py`；`builtin/codemode/**`；`builtin/mcp/**` 中
除 `plugin.py` 外的一切；其它测试文件；`pyproject.toml`/`uv.lock`；`README.md`；`docs/**`
中除 `plugins.md` 外的一切；spec 文件。

## 5. 最终报告格式

同组惯例：工单状态表｜commit 清单｜自测真实结论｜偏差与取舍（格式体例、上限取值、
staleness 措辞位置）｜未决问题。遇阻塞报告后停止。
