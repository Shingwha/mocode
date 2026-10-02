# ref · Python codemode DSL（冻结规范）

> 本文件是 `codemode` 插件的**行为契约**。W1b 按此实现，测试按此断言。
> 语义对齐 `ref/pi-codemode.md`（pi 的 JS 版），差异只在语言：JS 的 Promise 换成 Python 的
> async/await。凡本文件与工单冲突，以工单为准；凡与 pi 冲突，以本文件为准（这是 mocode 的目标形态）。

---

## 1. 一句话

模型写一段 **Python**，脚本里 `await tools.<name>(args)` 调用工具、`asyncio.gather` 并行、
在脚本内过滤大结果，**只有脚本的输出回到模型**。脚本跑在 `dispatcher(origin="program")` 上，
所以脚本的调用可被事件流观察，但不进 `messages`、不计入 turn 的 `tool_calls_made`。

## 2. 工具输入

```jsonc
{
  "script": "text('hi')\ntext((await tools.read({'path': 'README.md'})).content[:200])",
  "options": { "timeout_ms": 30000, "max_output_chars": 4000 }   // 可选
}
```

`script` 必填。`options` 可选；也支持脚本首行注释 `# @options: {"timeout_ms": 60000}`，
显式 `options` 参数优先于注释行。

## 3. 执行模型

- 脚本被包进一个 async 函数体：

  ```python
  async def __codemode__():
      <script>          # 整体缩进 4 空格
  ```

  因此 **顶层 `await` 与顶层 `return` 都合法**（`return` 的值按 §5 输出）。
- 执行环境：一个 dict 作为 globals，`exec(compile(...), env)`；`__builtins__` 被替换为
  受限白名单（见 W1b 工单 2.3）。**这不是安全沙箱**：模型本来就有 bash，这里只是
  稳定 API + 资源约束。
- 取消（dispatcher 超时 / turn 取消）以 `asyncio.CancelledError` 进入脚本，**必须原样透传**，
  不得吞掉转成普通失败。
- 禁止脚本再调 `codemode`（`tools` 里不含它，显式调用抛错）。

## 4. 全局命名（冻结）

| 名字 | 说明 |
|---|---|
| `await tools.name(args)` | 调工具；`args` 为 dict，也可用关键字参数 |
| `tools["exact-name"](args)` | 名字含非法标识符时用（如 `mcp__dev-radius__search`） |
| `text(v)` / `console.log(...)` | 追加输出；`text` 的 str 原样、其它 JSON 化 |
| `image(block)` | 追加图像块（进 details，文本里是 `[image: mime]` 占位） |
| `return v` | 顶层 return，等价 `text(v)` |
| `exit()` | 正常结束（后续代码不执行，仍算成功） |
| `store(k, v)` / `load(k)` | 小 JSON 状态；`store(k, None)` 删除；成功才提交 |
| `ALL_TOOLS` | 脚本启动时的快照：`[{"name","description"}]`，不含 codemode |
| `search_tools(q, limit=8, namespace=None)` | 同步，按相关度返回上表同形 items |
| `describe_tool(name)` | 同步，返回 `{"name","description","schema"}` 或 `None` |
| `asyncio`, `json`, `re`, `math`, `datetime`, `textwrap`, `collections`, `itertools`, `functools` | 只读标准库 |

**不提供**：`models`、`describe_namespace`、`open`、`__import__`、`eval`、`exec`、`input`、
文件系统、网络、timer。

## 5. 返回值与输出

- `tools.x()` 成功返回一个对象，字段 `content: str`、`details: dict`、`status: str`、
  `error_code: str | None`；`str(obj)` 等于 `obj.content`。
- `tools.x()` 失败（status ≠ ok）**抛异常**，异常 `str()` 是工具错误文本，带 `.result`
  （原始返回对象）。用 `asyncio.gather(..., return_exceptions=True)` 保留其余成功结果。
- 结果 content：
  - 成功：`Script completed in <N>ms` 换行接输出体；
  - 失败：`Script failed in <N>ms` 换行接（已有输出 +）`Script error: <Type>: <msg>`。
- 超过 `max_output_chars`（默认 12000）：保留头尾、中间标 `…<N> chars truncated…`，
  全文写到临时文件，末尾给 `Full output: <path>`。
- `image()` 的块进 `ToolResult.details["images"]`；模型读到的文本里是占位。

## 6. 示例（这些必须能跑）

### 6.1 并行读 + 过滤

```python
paths = ["src/core/tool.py", "src/core/dispatch.py", "src/core/hook.py"]
results = await asyncio.gather(*[tools.read({"path": p}) for p in paths])
for path, r in zip(paths, results):
    lines = r.content.splitlines()
    text(f"## {path} — {len(lines)} lines")
    text("\n".join(lines[:40]))
```

### 6.2 砍掉大输出

```python
r = await tools.bash({"command": "uv run pytest -q", "timeout": 120})
failures = [l for l in r.content.splitlines() if l.startswith("FAILED")]
text(f"failures={len(failures)}")
for line in failures[:20]:
    text(line)
```

### 6.3 失败可恢复

```python
outcomes = await asyncio.gather(
    tools.read({"path": "README.md"}),
    tools.read({"path": "missing.txt"}),
    return_exceptions=True,
)
for o in outcomes:
    text(f"✗ {o}" if isinstance(o, Exception) else f"✓ {o.content[:200]}")
```

### 6.4 store / load 跨调用

```python
seen = set(load("seen_commits") or [])
log = await tools.bash({"command": "git log --format=%H -30"})
commits = log.content.split()
fresh = [c for c in commits if c not in seen]
store("seen_commits", sorted(seen | set(commits)))
if not fresh:
    text("nothing new")
    exit()
text(f"{len(fresh)} new commits")
return {"new": fresh}
```

### 6.5 发现并调用 MCP 工具

```python
for t in search_tools("search github code", limit=5):
    text(f"{t['name']} — {t['description']}")
hits = await tools["mcp__dev-radius__search"]({"query": "webhook retries"})
return hits.content
```

## 7. 错误与边界

| 情形 | 行为 |
|---|---|
| 空脚本 | 失败：`Script error: CodemodeError: script is empty` |
| 语法错误 | 失败，错误文本含 `SyntaxError` 位置 |
| 未知工具 | `CodemodeError: unknown tool 'x'; use search_tools() or ALL_TOOLS` |
| 调 `codemode` | `CodemodeError: codemode cannot be called from a script` |
| 工具失败 | 抛异常（含 `.result`）；未捕获 → 脚本失败 |
| `exit()` | 成功结束 |
| 超时 / turn 取消 | `asyncio.CancelledError` 透传，由 dispatcher 记 `timeout:` / 取消 |
| store 超限 | `CodemodeError`，且**不提交任何** pending |
| 脚本成功但 store 超限 | 同上（先校验后 commit） |

## 8. `DESCRIPTION` 草案（给模型的说明书，英文，可微调措辞但语义不得变）

```text
Run a Python script that calls other tools. Only the script's output reaches you,
so use it to run calls in parallel and filter large results before they enter the
conversation.

The script runs as the body of an async function: top-level `await` and top-level
`return` are allowed. Available names:

- `await tools.<name>(args)` — call a tool. Use `tools["exact-name"]` when the name
  is not a valid Python identifier, e.g. `tools["mcp__dev-radius__search"]`.
  A successful call returns an object with `.content` (text), `.details` (dict),
  `.status`; `str(result)` is `.content`. A failed call raises; use
  `asyncio.gather(..., return_exceptions=True)` to keep the successful ones.
- `text(value)` / `console.log(...)` — append output. `return value` does the same.
- `image(block)` — show an image block.
- `store(key, value)` / `load(key)` — small JSON state kept across codemode calls.
  `store(key, None)` deletes it.
- `search_tools(query, limit=8)` / `describe_tool(name)` / `ALL_TOOLS` — discover
  callable tools (including tools not listed in the tool interface).
- `exit()` — end the script successfully.

`asyncio`, `json`, `re`, `math`, `datetime`, `textwrap`, `collections`, `itertools`
and `functools` are available. There is no file system, network, timer, `open`, or
`import`. `codemode` cannot call itself. A whole-script deadline can be set with
`# @options: {"timeout_ms": 60000}` on the first line, or the `options` argument.
```

## 9. 与 pi 的对照

| pi（JS） | mocode（Python） |
|---|---|
| 原始 JS 作为整个 tool 输入 | `{"script": "<python>"}`（mocode 工具参数是 JSON） |
| `tools.X(args)` 返回 Promise | `await tools.X(args)` |
| `Promise.allSettled` | `asyncio.gather(..., return_exceptions=True)` |
| `text/image/console/return/exit` | 同名 |
| `store/load` | 同名 |
| `ALL_TOOLS/searchTools/describeTool` | `ALL_TOOLS/search_tools/describe_tool`（同步） |
| `describeNamespace` | 不做（v1） |
| `models.classify/generateImages` | 不做（v1） |
| QuickJS 256MB VM | 进程内 exec + 白名单；**无内存硬上限**（登记遗留） |
| `// @options: {...}` | `# @options: {...}` 或 `options` 参数 |
