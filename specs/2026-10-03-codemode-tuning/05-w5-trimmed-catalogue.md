⏸️ 待派工 — 前置 W4 合并

# Spec 05 · W5：目录条目描述裁断（波次 W5）

> 分支 `feat/codemode-catalog-trim`；worktree 由 lead 建。
> 前置：W4 已合并 master（基线 1025 passed）。深读：`00-overview.md` §2 D13、
> `builtin/codemode/api.py` 的 `tool_entries`/`all_tools`/`search_tools` 组装处、
> `search.py` 的 `rank`（BM25 吃 name+description）。
> 绝不操作主检出，绝不 merge/push/tag/checkout master。

## 0. 目标

`all_tools()` 与 `search_tools()` 的条目描述裁到 **80 字符一行预览**（尾部 `…`），
与 W4 的 prompt 名单同哲学：目录便宜、详情按需（全文 + schema 走 `describe_tool()`）。
**硬约束：排序质量不跟着裁**——`search.py::rank` 继续吃完整描述，只在 API 返回边界裁。

## 1. 冻结实现决策（D13）

1. 条目形态：`{"name": <注册全名>, "description": <裁到 80 字符 + "…">}`。
2. 裁剪常量 `_DESCRIPTION_PREVIEW_CHARS = 80` 放 `api.py`；**正好 80 字符不裁**（不
   需要省略号），**空描述保持空**（不补省略号），裁切点优先在词边界（空格）回退
   （worker 自定：简单截断即可，词边界是 nice-to-have，报告取舍）。
3. 裁剪发生在返回边界：`search.py::rank` 内部仍用完整条目排序；`all_tools()` 的
   快照 list 也保留完整描述用于 `search_tools` 排序（快照语义不变），两个 API 出口
   各裁一份。
4. `describe_tool(name)` 不动：全文 description + schema。
5. `names_only=True` 路径不变（纯名字列表）。
6. 文档三表面（`description.py`/`docs/plugins.md`/`codemode/README.md`）同步条目
   形态措辞（"one-line preview … full text via describe_tool"），措辞断言钉住。

## 2. 工单（一个 commit）

### T1 裁断
- 按 §1 实现；测试：>80 字符描述 → 80+`…`；≤80 → 原样；空 → 空；`search_tools`
  的**排序回归测试**——query 命中某工具第 80 字符之后的词时该工具仍排前（证明全文
  排序保住）；`all_tools`/`search_tools` 两出口形状一致；`describe_tool` 仍全文；
  既有断言（短语钉/形状断言）同步。
- commit：`feat(codemode): trim catalogue descriptions to a one-line preview`

## 3. 验收

1. `uv run pytest -q; echo "EXIT: $?"` 全绿、退出码 0、数量 ≥ 1025。
2. `git diff --stat <W4 合并点>` 只含 `builtin/codemode/api.py`、`tests/test_builtin_codemode.py`、`builtin/codemode/description.py`、`docs/plugins.md`、`builtin/codemode/README.md`；core/host.py/runtime.py/plugin.py/output.py/search.py/mcp 包/pyproject/uv.lock 零 diff。
3. 三表面交叉核对条目形态措辞一致；`import mocode` <1ms。

## 4. 禁触清单

`mocode/core/**`；`mocode/host/plugin/host.py`；`builtin/codemode/` 下
`runtime.py`/`plugin.py`/`output.py`/`search.py`；`builtin/mcp/**`；其它测试文件；
`pyproject.toml`/`uv.lock`；`README.md`；`docs/**` 中除 `plugins.md` 外的一切；spec 文件。

## 5. 最终报告格式

同组惯例：工单状态表｜commit 清单｜自测真实结论｜偏差与取舍（词边界取舍、快照
与裁剪的内存形态、排序回归测试的设计）｜未决问题。遇阻塞报告后停止。
