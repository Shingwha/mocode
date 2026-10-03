⏸️ 待派工 — 规格已定（v2 API 全参考见 §1，用户已拍板）

# Spec 06 · W6：codemode v2 —— 脚本表面彻底重构（波次 W6）

> 分支 `feat/codemode-v2`；worktree 由 lead 建（旧 W5 分支/worktree 已删，零 commit）。
> 前置：W4 已合并（基线 1025 passed）。深读：`00-overview.md` §2 决策表（D1–D19 全部有效）、
> `ref/field-findings.md`（用户 12 条）、`ref/model-findings.md`（模型实测 8 条，本波验收
> 重放清单）。**§1 的 v2 参考是本工单的权威契约——实现与它逐字对齐。**
> 绝不操作主检出，绝不 merge/push/tag/checkout master。

## 0. 目标

把 codemode 脚本表面重构为 v2：结果一等公民、批量一等公民、门面宽容目录严格、
门控 import、内部模块拆分。最高约束与全组一致：core 零 diff、不启用时行为零变化、
每 commit 独立过门禁（基线 1025 只增不减）。

## 1. v2 API 参考（权威契约）

```python
# ══ 调用：kwargs 为主，结果是一等公民 ══
page = await tools.read(path="README.md", limit=80)
page.ok            # True
page.content       # 文本本体（str）
page.json()        # content 按 JSON 解析；失败给"内容片段+解析错误"
page.structured    # MCP 工具的 structuredContent 快捷入口（无则 None）
page.details       # 工具特有 {"exit_code": 0, ...}
page.tool          # 解析后的注册名
repr(page)         # <Result ok read 3.1k chars>（可读）
# 单项失败仍抛异常（带工具名 + 行号），快速失败

# ══ 批量是一等公民 ══
rs = await parallel(
    tools.search(query="mcp protocol 2026-07-28"),
    tools.search(query="python sdk v2"),
    tools.extract(url="https://..."),
    concurrency=8,               # 可选，覆盖全局 max_concurrency
)
rs.ok              # [Result, ...] 成功项，保序
rs.failed          # [Result, ...] 失败项（.error 带原因）
rs[i] / len(rs) / iter(rs)       # 列表行为，顺序=入参顺序
# 单项失败绝不炸整批；仅参数错误（非协程）抛异常

# ══ 目录与详情：门面宽容、目录严格 ══
dir(tools)                  # 可调工具名一览（ToolBox.__dir__）
all_tools()                 # [{name, description 一行预览（80 字符+"…"）}]
search_tools("search", limit=8, namespace=None, names_only=True)
describe_tool("search")     # 全文 + schema；裸调或 tools.describe_tool 均可（门面回退）

# ══ 语言面 ══
import asyncio, json, re    # 门控 import：8 模块白名单（asyncio/json/re/math/datetime/
                            # textwrap/collections/itertools/functools）；其余报错指路 tools.*
text(...) / print(...)      # 都进输出，按序追加
image(block)
store("k", v) / load("k")   # 跨调用状态；限额单值 256KB/总量 1MB（JSON 尺寸口径）
return v                    # 脚本成功时追加到最后
exit()                      # 成功收工
# deadline（options.timeout_ms / @options / plugins.codemode.timeout_s）；max_concurrency；
# 脚本错误带 (line N) + 源码行；输出超 max_output_chars 头尾保留+临时文件
```

### 1.1 五个已拍板判断（实现不得偏离）

1. **Result 保留 `.content`**（MCP wire 术语一致），易用性增量来自 `.json()/.structured/
   .ok/.tool/repr`；`ToolOutcome` 更名 `Result`；**W1 的 Mapping 协议保留**（get/getitem/
   keys/contains）。
2. **单项失败抛异常、仅 parallel 捕获**——不对称是设计（文档大字写明）。
3. `all_tools/search_tools/describe_tool` **不改名**。
4. 门面维持 `tools.x` 唯一调用入口（不做顶层平铺）；`tools.codemode` 自调用拒绝最优先。
5. **硬切无别名**（`ALL_TOOLS` 同政策）：旧名零兼容，description/docs/测试同步重写。

## 2. 已核实的现状（动手前读码确认）

- `api.py` 500+ 行混装 ToolBox/ToolOutcome/Store/env 组装——本波拆为
  `toolbox.py`/`result.py`/`store.py`/`env.py`（`api.py` 可删或留 re-export，worker 定，
  报告取舍；**包 `__init__` 不导出内部模块名，测试按新模块路径 import**）。
- `ToolBox._resolve` 三层（精确→归一化→短名，`api.py:221-243` 附近）；错误文案
  `unknown tool {name!r}; use search_tools() or all_tools()`。
- `search.py::rank` BM25 吃全文条目；目录裁断只能发生在返回边界。
- `output.py` 截断标记 `\n…{omitted} chars truncated…\n` + `Full output: <path>`。
- store 限额默认 262144/1048576（`api.py` Store）。
- 门面回退的内建集合：describe_tool/all_tools/search_tools/store/load/text/console/
  image/print/exit。

## 3. 工单（六个 commit，每个独立过全量门禁）

### C1 `refactor(codemode): make tool results a first-class Result`
- `result.py`：Result（五字段+`.error`+`.json()/.structured/.tool`+Mapping 协议+友好 repr）；
  单项调用失败抛 ToolCallError（带工具名）；批次模式构造 ok=False 的 Result。
- 测试：访问器全家、repr、json() 成功/失败路径、Mapping 保活。

### C2 `feat(codemode): add parallel batch calls with per-call failure capture`
- `result.py`（或 `batch.py`，worker 定）：`async parallel(*coros, concurrency=None) -> Batch`；
  Batch=list 子类（`.ok/.failed`，保序）；单项失败捕获、参数错误抛；concurrency 覆盖
  全局信号量。
- 测试：成功/混合失败/全失败/保序/concurrency 上限/非协程报错。

### C3 `refactor(codemode): split the sandbox and forgive the facade`
- 模块拆分（toolbox/result/store/env）；门面回退（内建集合直接返回）；`ToolBox.__dir__`
  返回可调工具名排序列表；未知名报错文案不变。
- 测试：回退全集合、目录仍只列注册工具、dir(tools)、既有行为零回归。

### C4 `feat(codemode): gate imports to the injected modules and trim the catalogue`
- env 注入门控 `__import__`（8 白名单；import/from-import 均通；其余报错指路 tools.*）；
- `all_tools()/search_tools()` 条目描述裁 80 字符+"…"（`_DESCRIPTION_PREVIEW_CHARS=80`；
  正好 80 不裁、空不补、排序仍用全文）。
- 测试：import 全形态+拒绝、裁断边界、排序回归（80 字符后的词仍排前）。

### C5 `feat(codemode): make truncation unmissable`
- `output.py` 截断通知改祈使句：`⚠ {N} chars truncated — before relying on this output,
  read the full result at {path} (e.g. via tools.read)`（worker 精修，必含字符数/路径/
  先读指令/tools.read 提示）。
- 测试：截断文案断言。

### C6 `docs(codemode): rewrite the v2 surface contract`
- `description.py` 围绕 §1 参考全文重写（精炼优先，体量与 W3 版相当）；`docs/plugins.md`
  codemode 节同步（含 store 限额 256KB/1MB+无需预截断、大件 tools.write 落盘+store 存路径
  惯用法、单项抛/parallel 捕获的不对称、门控 import 清单）；`codemode/README.md` 同步
  v2（模块结构、Result/Batch 设计注记）；措辞钉更新+新增（parallel/Result/import/
  截断句）。
- 测试：短语钉全绿。

## 4. 验收

1. 每 commit 后 `uv run pytest -q; echo "EXIT: $?"` 全绿、退出码 0、数量 ≥ 1025 且递增。
2. 写入范围累计：整个 `builtin/codemode/**`（含新建/删除模块文件、description.py、README）
   + `tests/test_builtin_codemode.py` + `docs/plugins.md`；core/host.py/mcp 包/pyproject/
   uv.lock/根 README/其它 docs/其它测试零 diff。
3. **重放清单全过**（用户 12 条 + 模型 8 条，见两份 ref）：import 三模块同条成功、
   `tools.describe_tool` 等价裸调、截断通知含先读指令、store 限额写明、
   `sorted(all_tools() names_only)` 合法、`res.get("content")` 可用、异常类可捕获、
   `(line N)` 行号、ensure_future 语义在文档。
4. **v2 新行为**：parallel 混合失败不炸、Result.json()/structured、dir(tools)。
5. `import mocode` <1ms；`-X importtime` 无 `mcp`；`uv run pytest tests/test_builtin_codemode.py tests/test_builtin_mcp_codemode.py -q` 全绿。

## 5. 禁触清单

`mocode/core/**`；`mocode/host/plugin/host.py`；`builtin/mcp/**`；`builtin/codemode/` 之外
的一切 host 代码；其它测试文件（含 `test_builtin_mcp_codemode.py`）；`pyproject.toml`/
`uv.lock`；`README.md`；`docs/**` 中除 `plugins.md` 外的一切；spec 文件。

## 6. 最终报告格式

工单状态表｜commit 清单（hash+message）｜自测真实结论（含重放清单逐条结论）｜偏差与
取舍（模块拆分形态、api.py 去留、Batch 落点、不对称语义文档句、description 体量对比）｜
未决问题。遇阻塞报告后停止，不越界自救。
