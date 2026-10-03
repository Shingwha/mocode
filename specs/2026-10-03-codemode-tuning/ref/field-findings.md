> **lead 注（2026-10-03）**：本文是用户首日使用 codemode 的踩坑实录，12 条发现已逐条
> 对照代码核实为真（file:line 证据见各工单）；lead 另核实出 4 个补充问题（print 静默
> 丢失、超时丢部分输出、describe_tool 无滤镜、异常类白名单缺口）。**注意**：本文写作
> 时 `ALL_TOOLS` 尚未改名——本组 D3 已拍板改为 `all_tools()`，文中示例按旧名阅读。

# code mode 使用问题整理与优化建议

| 项 | 值 |
|---|---|
| 日期 | 2026-10-03 |
| 环境 | Windows 11 · Python ≥ 3.12 · 项目 `mocode` v0.4.0 |
| 测试范围 | 首次使用 code mode（`codemode` 工具）的完整摸底 |
| 结论 | 可用且能力不弱（并行扇出 + 聚合 + 跨调用状态都验证通过），但**沙箱契约与文档存在明显偏差**，首次使用需试错 6 次才能跑通 |

---

## 一、摘要

- 阻断性问题 **4 个**：全部是「文档说了 / 实际不是」，或「隐含契约没写」，每次都白跑一轮。
- 能力不透明 **4 个**：返回类型、自省手段、错误定位、可用内建都得靠撞墙试出来。
- 体验细节 **4 个**：并发上限、环境说明、超时提示、输出方式优先级。

最值得优先修的两条：**`import` 语义澄清** 与 **`ToolOutcome` 支持 `.get()`**——这两个分别浪费了整整一轮往返。

---

## 二、实测确认的环境事实

| 事实 | 证据 |
|---|---|
| 沙箱**无文件系统**，所有 I/O 必须走 `tools.*` | 工具描述明确说明 |
| `bash` 是 POSIX 层（msys / git-bash），非 cmd / PowerShell | Windows 上 `find`、`wc -l < f` 均正常执行 |
| 工具在沙箱内用**短名**寻址 | `tools.bash(...)` 成功；`tools["mcp__k__bash"]` 报 `unknown tool` |
| `ALL_TOOLS` 是 `list[{"name","description"}]` | 实测 `len == 8`，键为 `name` / `description` |
| `store` / `load` 跨调用正常 | `visit_count` 依次为 `1 → 2` |
| 并行扇出 77 个 bash 调用耗时 1.24s | 无报错，但**上限未知** |

---

## 三、P0 — 阻断性问题

> 特征：首次使用必踩，且错误信息不给「下一步该干什么」。

### P0-1　`import` 不可用，但文档措辞暗示可 import

- **现象**：`import asyncio` → `ImportError: __import__ not found`
- **矛盾点**：工具描述写「`asyncio`, `json`, `re`, `math`, `datetime`, `textwrap`, `collections`, `itertools` 和 `functools` **are available**」，常规读法是「需要时 import」。
- **建议**：文档改为「以下名字已注入全局，**直接使用、不要 import**」，或让错误信息附带提示。

### P0-2　`asyncio.run()` 不可用

- **现象**：`asyncio.run(main())` → `RuntimeError: asyncio.run() cannot be called from a running event loop`
- **根因**：脚本体**本身就是 async 函数体**，已在事件循环内。
- **建议**：文档首行加「直接写顶层 `await`；**不要**包装 `asyncio.run` / `main()`」。

### P0-3　工具命名两套，且不支持互通

| 场景 | 命名 |
|---|---|
| 工具接口（模型侧可见） | `mcp__k__bash` |
| `ALL_TOOLS` / `search_tools()` 返回 | `bash` |
| 实际可用 | `bash` |

- **现象**：`tools["mcp__k__bash"]` → `unknown tool 'mcp__k__bash'; use search_tools() or ALL_TOOLS`
- **评价**：这条错误信息**质量很好**，是全篇唯一给了正确出路的。
- **建议**：`tools[...]` 同时接受前缀名与短名（或在工具描述里显著标注「沙箱内用短名」）。

### P0-4　返回对象不是 dict，没有 `.get()`

- **现象**：`res.get("content")` → `AttributeError: 'ToolOutcome' object has no attribute 'get'`
- **可用字段**：仅 `.content` / `.details` / `.status`（其中 `.details` 对 `bash` 是 `{"exit_code": N}`，对 `read` 是 `{"lines": N, "total_lines": N}`）
- **文档现状**：只说「A successful call returns an object with `.content`, `.details`, `.status`」，未说明它**不**是 Mapping。
- **建议**：让 `ToolOutcome` 实现 `get()` / `__getitem__` / `keys()`，退一步则在文档加粗警告。

---

## 四、P1 — 能力不透明

| # | 问题 | 现象 | 建议 |
|---|---|---|---|
| P1-1 | **无自省手段** | `dir()` 未开放，只能靠 `res.get()` 报错反推字段 | 开放 `dir()`，或提供 `describe_result(x)` |
| P1-2 | **`ALL_TOOLS` 结构未文档化** | 实测是 dict 列表；按字符串 `sorted()` 直接抛 `TypeError: '<' not supported between instances of 'dict' and 'dict'` | 文档给一行示例，或提供 `tool_names() -> list[str]` |
| P1-3 | **脚本错误不带行号** | `AttributeError: 'ToolOutcome' object has no attribute 'get'` 无行列信息 | 错误包装附上出错行号 + 源码片段 |
| P1-4 | **内建白名单不可见** | `NameError: name 'dir' is not defined`，不知哪些可用 | 文档列出可用内建清单 |

---

## 五、P2 — 体验细节

| # | 问题 | 建议 |
|---|---|---|
| P2-1 | **并发上限未公开** | 文档写明服务端 fan-out cap（本次 77 路并发通过，但属未知边界） |
| P2-2 | **Windows 环境未说明** | 点明 bash 为 POSIX 层（msys/git-bash），跨平台用户不必再试错 |
| P2-3 | **无剩余时间提示** | 长扇出时无法感知额度；`timeout_ms` 只能自己估 |
| P2-4 | **输出方式优先级不明** | `text()` / `console.log()` / `return` 三者关系未讲清（实测 `text` 与 `return` 等价，`console.log` 未验证） |

---

## 六、可直接落地的实现改动

按性价比排序，前两条各能省掉一轮往返：

1. **`ToolOutcome` 实现 Mapping 协议** —— 加 `get` / `__getitem__` / `keys` / `__contains__`，兼容现有属性访问，改动小、收益最大。
2. **`tools[...]` 接受带前缀的全名** —— 在查找时先剥离 `mcp__<server>__` 前缀再匹配，短名写法保持不变。
3. **错误信息带行号** —— 捕获脚本异常后回填 `line N` 与该行源码。
4. **开放 `dir()`** —— 或等价地提供 `describe_result()`。
5. **`search_tools()` 增加 `names_only=True` 参数** —— 返回 `list[str]`，省去调用方 `x["name"]` 的心智负担。

---

## 七、可直接抄进文档的「沙箱使用约定」

```python
# ✅ 正确写法
# 1) 不 import，名字已注入全局
r = await tools.bash({"command": "ls -1"})

# 2) 顶层 await，绝不包 asyncio.run / main()
rows = await asyncio.gather(*[tools.read({"path": f}) for f in files],
                            return_exceptions=True)

# 3) 结果只碰三个字段
text(rows[0].content)
text(rows[0].details.get("exit_code"))

# 4) 扇出 + 聚合 + 过滤：只让摘要进入上下文
text("\n".join(sorted(f"{s.content.strip()}\t{f}" for f, s in zip(files, sizes))))

# 5) 错误隔离
ok = [x for x in rows if not isinstance(x, Exception)]

# ❌ 常见错误
# import asyncio                          → __import__ not found
# asyncio.run(main())                     → already in a running event loop
# tools["mcp__k__bash"]({...})            → unknown tool（应用短名 tools.bash）
# res.get("content")                      → AttributeError（用 res.content）
# sorted(ALL_TOOLS)                       → TypeError（元素是 dict）
# dir(res)                                → NameError
```

---

## 附录 A　本次实测原始错误记录

| 顺序 | 触发写法 | 错误信息 |
|---|---|---|
| 1 | `import asyncio` | `ImportError: __import__ not found` |
| 2 | `asyncio.run(main())` | `RuntimeError: asyncio.run() cannot be called from a running event loop` |
| 3 | `sorted(ALL_TOOLS)` | `TypeError: '<' not supported between instances of 'dict' and 'dict'` |
| 4 | `tools["mcp__k__bash"]({...})` | `CodemodeError: unknown tool 'mcp__k__bash'; use search_tools() or ALL_TOOLS` |
| 5 | `res.get("content")` | `AttributeError: 'ToolOutcome' object has no attribute 'get'` |
| 6 | `dir(res)` | `NameError: name 'dir' is not defined` |
| 7 | `asyncio.ensure_future(main())` | 无输出（未 await，任务未被调度即返回）— 隐性坑，建议文档一并注明 |

第 7 条补充：即便用 `ensure_future` 也**不会自动等待**，脚本结束即丢弃。任何后台任务都需显式 `await`。
