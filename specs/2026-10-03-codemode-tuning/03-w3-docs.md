✅ 2026-10-03 @ feat/codemode-docs（已合并 master；1020 passed exit 0，lead 复核；九条契约 × description/plugins.md/README/代码四表面交叉核对全绿；description 31 行/1960 字符，字符 +34.8% 系强制条款所致，lead 接受该偏差）

# Spec 03 · W3：codemode 文档与模型描述收口（波次 W3）

> 分支 `feat/codemode-docs`；worktree 由 lead 建。
> 前置：W2 已合并 master。深读：`00-overview.md` §2 决策表、`ref/field-findings.md`
> （§七的「沙箱使用约定」是现成素材）、`01`/`02` 工单（W1/W2 落地后的最终表面）。
> 绝不操作主检出，绝不 merge/push/tag/checkout master。

## 0. 目标

三处文档表面与新行为**逐字一致**：`description.py`（模型每次对话读的契约——最重要）、
`docs/plugins.md` 的 codemode 节、新建 `codemode/README.md`（插件目录设计注记，mcp 有、
codemode 没有）。这是整组的收口波：你写下的每句话都要能在代码里指出实现。

## 1. 已核实的文档事实

- `description.py:18-19`：教 `tools["mcp__dev-radius__search"]`（带连字符）——**这个示例
  是错的**（下标精确匹配，注册名已折叠成 `_`）。
- `description.py:32-34`："`asyncio`, … `functools` are available. There is no file
  system, network, timer, `open`, or `import`."——从未说明**名字已注入全局、不要 import**。
- `docs/plugins.md:857-894`：现文提及 restricted `__builtins__`/8 模块/`.content,.details,
  .status`/ALL_TOOLS 快照/max_output_chars/超时链/store 限额；无 asyncio.run 陷阱、无
  ensure_future 注记、无命名规则、无行号、无 `error_code`、无 print 说明。
- 措辞断言测试：`tests/test_builtin_codemode.py:534-572`（description phrase 组）——
  W1/W2 合并后这些断言可能已部分过时，本波统一更新。
- `codemode/README.md` 不存在；`mcp/README.md` 是体例参照（设计注记、非用户手册）。

## 2. 必须写进表面的行为清单（W1+W2 落地后的最终契约）

1. 名字**已注入全局**——`asyncio/json/re/math/datetime/textwrap/collections/itertools/functools`
   与 `tools/text/console/image/print/exit/store/load/all_tools/search_tools/describe_tool`；
   **没有 `import`**（写"直接使用，不要 import"）。
2. 脚本是 async 函数体：顶层 `await`/`return` 合法；**不要** `asyncio.run()`/`main()` 包装；
   `ensure_future`/未 await 的任务不会自动等待。
3. 命名：本身工具用本名（`tools.bash`）；MCP 工具 = 折叠全名 `mcp__<server>__<tool>`
   （`tools["…"]` 与属性同规则）+ **无歧义短名**（歧义报错列候选）。
4. 结果：`ToolOutcome` 四字段 `content/details/status/error_code`，支持 Mapping 访问
   （`res.get("content")` 合法）；失败抛异常（`gather(..., return_exceptions=True)` 可保留
   成功；内置异常类可按名捕获）。
5. 发现：`all_tools()`（启动快照）、`search_tools(query, limit=8, namespace=None,
   names_only=False)`、`describe_tool(name)`。
6. 输出：`text()/console.log()/image()/return/print` 全部进结果、按序追加（return 仅成功
   时追加在最后）；超 `max_output_chars`（默认 12000）头尾保留 + 临时文件路径。
7. 超时：显式 `timeout_ms`/`@options`/`timeout_s` → 超时返回已攒输出 + timed_out 标注；
   未设 → 按工具级超时兜底（部分输出不保留）。
8. `store/load` 语义与限额；脚本 deadline 可配 `plugins.codemode.timeout_s`、并发上限
   `plugins.codemode.max_concurrency`（默认不限）。
9. 一句老话保留：**不是安全沙箱**（模型本来就有 bash）；无文件系统/网络/timer/`open`。

## 3. 工单（一个 commit）

### T1 描述 + docs + README
commit：`docs(codemode): rewrite the script surface contract`
- `description.py`：按 §2 重写。**约束**：这是模型读的 prompt 契约——精炼优先，总量不
  明显超过现文（现文约 30 行；宁可砍例子不可啰嗦），连字符错误示例替换为正确写法，
  附言体例（markdown 列表）沿用现文。
- `docs/plugins.md` codemode 节：与新表面逐字一致（`ALL_TOOLS` → `all_tools()`、四字段、
  行号、print、短名、超时语义、max_concurrency）；保留「不是安全沙箱」与遗留清单
  （`mode="only"`、models 目录、`describe_namespace()`、资源变更订阅等）。
- 新建 `mocode/host/plugin/builtin/codemode/README.md`：仿 `mcp/README.md` 体例——
  设计注记（restricted builtins 的构成与理由、为什么无 import、program-origin 事件
  契约、快照语义、deadline 自包与 dispatcher 的关系、限制清单）。
- 测试：更新 `:534-572` 措辞断言到新文案；加断言钉住关键句（"不要 import"/短名规则/
  四字段/timeout 语义），防止未来回归。

## 4. 验收

1. `uv run pytest -q; echo "EXIT: $?"` 全绿、退出码 0、数量 ≥ W2 合并值。
2. `git diff --stat <W2 合并点>` 只含 `builtin/codemode/description.py`、`builtin/codemode/README.md`、`docs/plugins.md`、`tests/test_builtin_codemode.py`；其余（含 `plugin.py`——description 变了工具描述字节变，但**只在启用 codemode 时**；确认不启用时 prompt 字节不变，由既有测试为证）零 diff。
3. 三表面交叉核对：随机抽 5 条行为，description / plugins.md / README / 代码四处一致。
4. description 长度：与现文相当或更短（报告里给行数/字符数对比）。

## 5. 禁触清单

`mocode/core/**`；`mocode/host/plugin/host.py`；`builtin/codemode/` 下 `api.py`/`runtime.py`/`plugin.py`/`output.py`/`search.py`；`builtin/mcp/**`；其它测试文件；`pyproject.toml`/`uv.lock`；`README.md`（仓库根）；`docs/**` 中除 `plugins.md` 外的一切；spec 文件。

## 6. 最终报告格式

同组惯例：工单状态表｜commit 清单｜自测真实结论｜偏差与取舍（description 增删了什么、
为什么；README 结构；与 §2 清单的逐条对照表）｜未决问题。遇阻塞报告后停止。
