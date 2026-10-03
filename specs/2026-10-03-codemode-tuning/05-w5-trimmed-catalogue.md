⏸️ 待派工 — 分支已建（`feat/codemode-catalog-trim` @ `11af383`，规格已扩充：本文件即权威）

# Spec 05 · W5：codemode 目录裁断 + 门控 import + 门面回退 + 截断/store 加固（波次 W5）

> 分支 `feat/codemode-catalog-trim`；worktree `C:/Users/shifu/.worktrees/mocode/feat-codemode-catalog-trim`。
> 前置：W4 已合并（基线 1025 passed）。深读：`00-overview.md` §2 D13–D16（本工单全部依据）。
> 绝不操作主检出，绝不 merge/push/tag/checkout master。

## 0. 目标

四件事一个波：①目录条目描述裁 80 字符预览（排序仍用全文）；②白名单门控 `import`；
③`tools.x` 门面对内建名回退解析；④输出截断通知改祈使句 + store 限额写进描述。

## 1. 已核实的代码事实

- `api.py`：`tool_entries`（`[{"name","description"}]` 来源，`all_tools` 快照与 `search_tools`
  共用）、`ToolBox._resolve`（精确 → 归一化 → 短名，`:221-243` 附近）、`build_env` 组装处；
  排序在 `search.py::rank`（**不许动**）。
- `output.py`：`truncate_body` 的 `\n…{omitted} chars truncated…\n` 与 `compose` 的
  `Full output: <path>`（`:63-123`）。
- 内建名集合（回退白名单）：`describe_tool`/`all_tools`/`search_tools`/`store`/`load`/
  `text`/`console`/`image`/`print`/`exit`。
- store 限额：`api.py` Store 默认 262144/1048576，按 `json.dumps` 尺寸。

## 2. 工单（四个 commit，每个独立过全量门禁）

### T1 目录裁断
commit：`feat(codemode): trim catalogue descriptions to a one-line preview`
- 条目 `{name, description 裁 80+"…"}`；正好 80 不裁、空描述不补省略号；裁剪只在
  `all_tools()`/`search_tools()` 返回边界（内部快照与排序保留全文）；`describe_tool` 不动；
  `names_only` 不动。常量 `_DESCRIPTION_PREVIEW_CHARS = 80` 放 `api.py`。
- 测试：>80→80+`…`；≤80→原样；空→空；**排序回归**（query 命中 80 字符之后的词仍排前）；
  两出口形状一致；`describe_tool` 仍全文。

### T2 门控 import
commit：`feat(codemode): allow importing the injected modules`
- `api.py` 注入 `__import__` 到 env：白名单 = 8 个已注入模块名（`asyncio/json/re/math/
  datetime/textwrap/collections/itertools/functools`）；命中返回已加载模块（支持
  `import x` 与 `from x import y`，fromlist 路径同）；未命中抛带引导的错误（点明
  no fs/network、指向 `tools.*`）。**不动 `runtime.py`**（RESTRICTED 原样，`__import__`
  以 env 全局注入即可生效）。
- 测试：`import asyncio` 成功且 `asyncio is 注入的模块`；`from json import dumps` 成功；
  `import os` 报引导错误；`import asyncio.subprocess` 拒绝（子模块不在白名单）。

### T3 门面回退解析
commit：`feat(codemode): resolve script builtins through the tools facade`
- `ToolBox` 解析顺序追加最后一层：注册工具三层都未命中时，名字在内建集合里 → 返回
  内建函数（不报错）；不在 → 维持现有 unknown-tool 报错（文案不变）。
- `tools.codemode` 自调用拒绝维持最优先。
- 文档三表面措辞改"门面宽容、目录严格"（facade resolves builtins too; the catalogue
  lists registered tools only）。
- 测试：`tools.describe_tool("x")` 与裸调等价；`tools.all_tools()` 可用；`tools.store/load/
  text` 回退可用；`tools.nope` 仍报 unknown tool；目录仍不含内建名（all_tools 断言）。

### T4 截断通知 + store 限额描述
commit：`feat(codemode): make truncation unmissable and store limits explicit`
- `output.py` 截断通知改祈使句：`⚠ {N} chars truncated — before relying on this output,
  read the full result at {path} (e.g. via tools.read)`（具体措辞 worker 精修，要求：含
  省略字符数、文件路径、明确的"先读再总结"指令、提示可用 tools.read）；`compose` 的
  `Full output:` 行并入该句或同步措辞（worker 定，报告）。
- `description.py` + `docs/plugins.md`：store 一节写明默认限额（单值 256 KB / 总量
  1 MB，按 JSON 序列化尺寸计）+"无需预截断"；大件出路写进文档（`tools.write` 落盘 +
  `store` 存路径的惯用法）。
- 测试：截断用例断言新通知文案（含路径与指令）；store 限额措辞钉。

## 3. 验收

1. 每 commit 后 `uv run pytest -q; echo "EXIT: $?"` 全绿、退出码 0、数量 ≥ 1025 且递增。
2. 写入范围：T1/T2/T3 限 `builtin/codemode/api.py` + `tests/test_builtin_codemode.py`；
   T4 加 `builtin/codemode/output.py`/`description.py`/`docs/plugins.md`/`README.md`。
   四 commit 累计 `git diff --stat 11af383` 不得超出：`api.py`、`output.py`、
   `description.py`、`README.md`、`docs/plugins.md`、`tests/test_builtin_codemode.py`；
   `runtime.py`/`plugin.py`/`search.py`/core/host.py/mcp 包/pyproject/uv.lock 零 diff。
3. 用户实测场景重放：①`import re, asyncio, json` 成功；②`tools.describe_tool('mcp__anysearch__search')`
   成功返回 schema；③长输出触发截断 → 通知含"先读全文"指令。
4. `import mocode` <1ms；`-X importtime` 无 `mcp`。

## 4. 禁触清单

`mocode/core/**`；`mocode/host/plugin/host.py`；`builtin/codemode/runtime.py`/
`plugin.py`/`search.py`；`builtin/mcp/**`；其它测试文件；`pyproject.toml`/`uv.lock`；
`README.md`；`docs/**` 中除 `plugins.md` 外的一切；spec 文件。

## 5. 最终报告格式

同组惯例：工单状态表｜commit 清单｜自测真实结论（含三场景重放）｜偏差与取舍｜未决问题。
遇阻塞报告后停止。
