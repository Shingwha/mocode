✅ 2026-10-02 @ feat/codemode

# Spec 02 · W1b：`codemode` 内置插件（波次 W1b）

> 分支 `feat/codemode`；worktree `C:\Users\shifu\.worktrees\mocode\feat-codemode`。
> 前置：无（W0 基线）。深读：`00-overview.md` + `ref/codemode-dsl.md` + `ref/pi-codemode.md`
> + `ref/mocode-api.md`。
> 你只读本 worktree 内文件；绝不操作主检出，绝不 merge/push/tag。

## 0. 现状事实（已核实，2026-10-02）

- `AgentLoop.dispatcher` 是唯一工具执行入口（`core/agent.py:172` 构造，
  `core/dispatch.py:91` 类）。`ToolDispatcher.run(name, args, *, call_id="", parse_error=None,
  origin="model", parent_call_id=None, timeout=None) -> DispatchResult`（`:121`）。
- `DispatchResult` 字段：`status, content, details, error_code, duration, call_id`
  （`core/dispatch.py`）。`status ∈ {"ok","error","timeout","denied","not_found"}`。
- `origin="program"` 契约（`core/dispatch.py` 模块 docstring）：事件进 channel、
  **不进 messages**、**不折叠 tool_calls_made**；`call_id` 被分配为 `<parent>:<n>`。
- `ToolCallContext`（`core/hook.py:62`）字段含 `tool_call_id`、`tool_args`、`emit`、
  `cancelled`（`cancel_event`）、`tool_timeout`、`tool_result_limit`。codemode 用 `with_context=True`
  拿它取 `tool_call_id`（做 parent 归属）与 `cancelled`。
- `Tool` 支持 async `func`（`is_async` 自动检测，`run_async` await）。
- `ToolPolicy(timeout: int|None, result_limit: int|None)`，`policy` 可为 callable(args)->ToolPolicy
  （`core/tool.py:220`；bash 的 `timeout` 参数就这么映射）。
- `HostContext.agent`（`host/plugin/context.py:169`）→ `ctx.agent.dispatcher`；
  `HostContext.plugin_state("codemode")`（`:129`）是随 session 持久化的 JSON dict，
  `close()`/`prepare()` 与 call time 都拿同一个 ctx。
- `SnapshotToolRegistry`：`ctx.tools.names(audience="program")` 给脚本可见工具；
  `ctx.tools.get(name)` 取单个 Tool；`all()` 是管理视图。
- 测试 fixture：`tests/conftest.py` 的 `plugin_host(...)`、`make_agent(...)`、
  `wired(...)`、`script/say/call_tool`（`from mocode.testing import ...`）。
- `AgentConfig.tool_timeout` 默认 240（`core/agent.py:71`）；`tool_result_limit` 50000（`:70`）。

## 1. 目标

实现 `mocode/host/plugin/builtin/codemode/` 包：一个 `codemode` 工具，模型写 **Python 脚本**，
脚本经 `dispatcher`（`origin="program"`）并行调用工具、过滤结果，只有脚本输出回到模型。
DSL 见 `ref/codemode-dsl.md`（**冻结**）。

## 2. 冻结契约

### 2.1 文件布局（唯一允许的新增路径）

```
mocode/host/plugin/builtin/codemode/
├── __init__.py      # 导出 PLUGIN、CodemodePlugin；无副作用
├── plugin.py        # CodemodePlugin
├── runtime.py       # run_script、_ScriptExit、restricted builtins
├── api.py           # ToolBox、ToolOutcome、Output、Store、发现helper、env 组装
├── output.py        # 渲染 / head+tail 截断 / 临时文件
├── search.py        # rank(query, tools)
└── description.py   # DESCRIPTION 常量（给模型的说明书）
```

`tests/test_builtin_codemode.py`（新增，唯一测试文件）。

### 2.2 工具定义

```python
Tool(
    name="codemode",
    description=DESCRIPTION,
    schema={
        "type": "object",
        "properties": {
            "script":  {"type": "string", "description": "Python source to run"},
            "options": {
                "type": "object",
                "properties": {
                    "timeout_ms":       {"type": "integer"},
                    "max_output_chars": {"type": "integer"},
                },
            },
        },
        "required": ["script"],
    },
    func=run,                 # async def run(args, ctx) -> ToolResult
    with_context=True,
    availability="model",     # 只给模型；脚本内禁止再调 codemode
    tags=frozenset({"codemode"}),
    policy=lambda args: ToolPolicy(timeout=<seconds>),   # 见下
)
```

- `timeout`：`options.timeout_ms // 1000`（向上取整）；无则 `plugins.codemode.timeout_s`，
  为 0/缺省时返回 `ToolPolicy(timeout=None)` ⇒ 落回 `AgentConfig.tool_timeout`（240s）。
- `result_limit` 不设（自管截断），保持 config 默认。
- **递归保护**：脚本内 `tools`/`ALL_TOOLS`/`search_tools` 都不含 `codemode`；
  即使显式 `tools["codemode"]` 也抛 `CodemodeError("codemode cannot be called from a script")`。

### 2.3 脚本执行（`runtime.py`）

```python
class _ScriptExit(Exception):
    """exit() 正常结束。"""

async def run_script(script: str, env: dict) -> object:
    source = "async def __codemode__():\n" + textwrap.indent(script, "    ") + "\n"
    code = compile(source, "<codemode>", "exec")
    env["__builtins__"] = RESTRICTED
    exec(code, env)                       # 单 dict：函数 __globals__ 即 env
    return await env["__codemode__"]()
```

- 空/纯空白脚本 → `CodemodeError("script is empty")`（在 exec 前判）。
- `SyntaxError` 等 `compile`/`exec` 异常 → 脚本失败结果（见 4），带上异常文本。
- **取消必须透传**：`except asyncio.CancelledError: raise` 放在最前。
- `RESTRICTED` 白名单（`__builtins__` 的 dict 形态；**不是安全边界**，docstring 写明）：

```python
RESTRICTED = {
    k: getattr(builtins, k) for k in (
        "abs","all","any","bool","dict","enumerate","Exception","float","format",
        "frozenset","getattr","hasattr","int","isinstance","iter","len","list","max",
        "min","next","object","print","range","repr","reversed","round","set","sorted",
        "str","sum","tuple","type","ValueError","KeyError","IndexError","TypeError",
        "zip","map","filter","divmod","pow","chr","ord","bytes","bytearray","slice",
    )
}
```

不提供 `open`、`__import__`、`eval`、`exec`、`compile`、`globals`、`locals`、`vars`、
`input`、`exit`/`quit`（Python 内置的那个）。模块使用靠注入的 `env`（见 2.4），不靠 import。

### 2.4 注入的全局（`api.py` 组装 env）

| 名字 | 类型 | 语义 |
|---|---|---|
| `asyncio` | module | 并行用 `asyncio.gather` |
| `json`, `re`, `math`, `datetime`, `textwrap`, `collections`, `itertools`, `functools` | module | 只读工具库 |
| `tools` | `ToolBox` | 见 2.5 |
| `text(value)` | fn | 追加输出项（str 原样，其它 `json.dumps(default=str)`） |
| `console` | obj | `.log/.info/.warn/.error/.debug` → `text(" ".join(map(str,args)))` |
| `image(block)` | fn | 追加图像块到 output（不落文本） |
| `exit()` | fn | `raise _ScriptExit()` |
| `store(key, value)` | fn | overlay 写入（value 为 None ⇒ 删除） |
| `load(key)` | fn | 读 overlay/backing，缺失返回 `None` |
| `ALL_TOOLS` | list[dict] | 脚本启动时快照：`[{"name","description"}]`，不含 codemode |
| `search_tools(query, limit=8, namespace=None)` | fn | 同步，返回同上 dict 列表（`search.rank`） |
| `describe_tool(name)` | fn | 同步，返回 `{"name","description","schema"}` 或 `None` |

- **不注入** `models`、`describe_namespace`（Out of scope，见 00 §2）。
- `ALL_TOOLS` 是**快照**（脚本开始时算一次），不是 live 视图；docstring 写明。

### 2.5 `ToolBox`（`api.py`）

```python
class CodemodeError(Exception): ...

class ToolOutcome:                      # 脚本拿到的返回值
    content: str
    details: dict
    status: str
    error_code: str | None
    def __str__(self) -> str: return self.content
    def to_dict(self) -> dict: ...

class ToolBox:
    def __getitem__(self, name): ...    # 精确名
    def __getattr__(self, name): ...    # 精确名，回退到"归一化名 → 注册名"映射
    def _bind(self, name):
        async def call(args=None, **kwargs):
            ... # 见下
        return call
```

- 解析：先按精确名 `registry.get(name)`；`__getattr__` 找不到时查 `_attr_map`
  （`normalize(reg_name) == attr` 的唯一命中）；仍无 → 抛
  `CodemodeError(f"unknown tool {name!r}; use search_tools() or ALL_TOOLS")`。
- 参数：`args` 是 dict，或关键字 `**kwargs`；两者合并（`{**(args or {}), **kwargs}`）。
- 调用：`result = await dispatcher.run(name, merged, origin="program", parent_call_id=parent_id)`。
- `status != "ok"` → `raise ToolCallError(name, result)`，异常 `str()` 为
  `f"{name}: {result.content}"`，并挂 `.result = result`。
- 成功 → 返回 `ToolOutcome(content=result.content, details=result.details, status=result.status,
  error_code=result.error_code)`。
- `parent_id` 是 codemode 工具自身的 `call_ctx.tool_call_id`。

### 2.6 输出与截断（`output.py`）

- `Output`：`items: list[str]`、`images: list[dict]`；`text(v)`；`render_body()` = `"\n".join(items)`。
- 结果 content 形态：
  - 成功：`f"Script completed in {ms}ms\n{body}"`（body 为空则只有首行）。
  - 失败：`f"Script failed in {ms}ms\n{body}\nScript error: {type(e).__name__}: {e}"`
    （body 为空则不插空行）。
- `return value`：非 None 时按 `text(value)` 追加。
- `image(block)`：把 block 放进 `details["images"]`，并在 body 追加 `f"[image: {mime}]"`。
- 截断：`max_output_chars = options.get("max_output_chars") or plugin_config("codemode").get(
  "max_output_chars", 12000)`；超出则保留前 `max//2` 与后 `max//2`，中间插
  `\n…{n} chars truncated…\n`，全文写 `tempfile`（`<tmp>/mocode-codemode-<uuid>.txt`，UTF-8），
  在结果末尾追加 `\nFull output: <path>`。
- 返回 `ToolResult(content=<最终文本>, details={"ok": bool, "images": [...],
  "truncated": bool, "full_output_path": str|None, "tool_calls": int})`。
- `tool_calls` 由 ToolBox 计数（每次 `dispatcher.run` +1）。

### 2.7 `store` / `load`（`api.py`）

- backing = `ctx.plugin_state("codemode")`（**只在 `run` 内通过闭包拿到**，不要放 self）。
- overlay `_pending: dict`；`load` 先查 pending 再查 backing；`store(k, None)` 记删除哨兵。
- **成功才 commit**：正常返回或 `_ScriptExit` → 把 pending 应用到 backing；异常 → 丢弃。
- 限额（超限 → `CodemodeError`）：
  - 单个 value `json.dumps` 后 ≤ `store_max_value_chars`（默认 262144）；
  - 全部 value `json.dumps` 后 ≤ `store_max_total_chars`（默认 1048576）。校验在 commit 前。

### 2.8 `search.rank`（`search.py`，无依赖）

确定性 BM25-lite，冻结算法（agent 照此实现，保证测试可断言）：

1. tokenize：`re.findall(r"[a-z0-9_]+", (name + " " + description).lower())`；
   query 同法。
2. 每个候选算分：query token 命中 name 记 2 分、命中 description 记 1 分；全 query token
   都命中额外 +1；无命中不算。
3. `namespace` 参数：给定则先过滤 `name` 以 `mcp__<ns>__` / `normalize(ns)` 归一后匹配。
4. 按 `(-score, name)` 排序，取前 `limit`（默认 8），返回 `[{"name","description"}]`。
5. query 为空 → 返回入口注册序前 limit 个。

### 2.9 `DESCRIPTION`（`description.py`）

必须教模型 **Python 而非 JS**，内容覆盖：async 函数体、顶层 `await`/`return`、
`await tools.<name>(args)`、非法标识符用 `tools["name"]`、失败抛异常 + `asyncio.gather(..., return_exceptions=True)`、
`text/console/return/image`、`store/load`、`search_tools/describe_tool/ALL_TOOLS`、`exit()`、
`# @options: {"timeout_ms": ...}`、禁止递归。全文草案见 `ref/codemode-dsl.md` §5，可微调但不得改变语义。

### 2.10 插件（`plugin.py`）

```python
class CodemodePlugin(Plugin):
    name = "codemode"
    description = "Run a Python script that calls other tools"
    def build(self, ctx: BuildContext) -> None:
        host = ctx                              # 装配后同一对象变成 HostContext
        ctx.tools.register(codemode_tool(host))
PLUGIN = CodemodePlugin()
```

- `codemode_tool(host)` 返回 2.2 的 Tool，闭包捕获 `host`；call time 用
  `host.agent.dispatcher`、`host.plugin_state("codemode")`、`host.plugin_config("codemode")`。
- `build()` 保持同步、无 I/O，**不注册 prompt section**（说明全在 DESCRIPTION）。
- 不设 `prepare`/`close`（无资源）。

### 2.11 配置键

```jsonc
"plugins": { "codemode": {
  "enabled": false,                 // 默认关（opt-in）
  "timeout_s": 0,                   // 0 ⇒ 落回 AgentConfig.tool_timeout
  "max_output_chars": 12000,
  "store_max_value_chars": 262144,
  "store_max_total_chars": 1048576
}}
```

## 3. 工单（每项一个 commit）

### T1 `runtime.py`：exec 包装 + 白名单 + 取消透传
- 实现 2.3；单测：顶层 await/return、语法错误、空脚本、`asyncio.CancelledError` 透传。
- commit：`feat(codemode): execute a Python script in a restricted namespace`

### T2 `api.py`：ToolBox + ToolOutcome + discovery + store
- 实现 2.5 / 2.7 与 `ALL_TOOLS/search_tools/describe_tool`。
- 单测：属性/下标调用、归一化名、未知工具报错、失败抛异常带 result、
  gather+return_exceptions、store 成功提交/失败丢弃/限额。
- commit：`feat(codemode): expose the tool box, discovery and store`

### T3 `output.py` + `search.py`
- 实现 2.6 / 2.8；单测渲染格式、head+tail 截断、临时文件、rank 确定性。
- commit：`feat(codemode): render, truncate and rank output`

### T4 `plugin.py` + `description.py`
- 按 2.2 / 2.9 / 2.10；DESCRIPTION 对齐 `ref/codemode-dsl.md` §5。
- commit：`feat(codemode): register the codemode tool`

### T5 `tests/test_builtin_codemode.py` 收口
- 端到端：`plugin_host(plugins=[PLUGIN], tools=<registry with echo/bash-like tools>)`，
  调 `codemode` 脚本，断言输出、program-origin 事件、`messages` 不含脚本内部调用、
  `tool_calls_made` 不计、plugin_state 持久化。
- commit：`test(codemode): cover the plugin end to end`

## 4. 验收（完成前自测，报告真实结论）

1. `uv run pytest -q` 全绿，独立退出码 0，数量 **≥ 700**。
2. `uv run pytest tests/test_builtin_codemode.py -q` 全绿。
3. `uv run python -c "from mocode.host.plugin.builtin.codemode import PLUGIN; print(PLUGIN.name)"`
   → `codemode`。
4. `grep -rn "codemode" mocode/core/` 无命中。
5. `git diff --stat` 只含 `builtin/codemode/**` 与 `tests/test_builtin_codemode.py`。
6. 手工 smoke（写进报告）：一段 `asyncio.gather` 调两个 echo 工具的脚本，真实输出正确。

## 5. 禁触清单

- `mocode/core/**` 全部。
- `mocode/host/**` 中除 `builtin/codemode/**` 外的一切。
- `README.md`、`docs/**`、`AGENTS.md`、`pyproject.toml`（W2）。
- 其它测试文件（只许新增 `tests/test_builtin_codemode.py`）。
- `mocode/host/plugin/builtin/mcp/**`（W1a 的领地）。
- spec 文件（归 lead）。

## 6. 最终报告格式

工单状态表｜commit 清单（hash+message）｜自测真实结论（命令 + 退出码）｜
偏差与取舍（尤其：白名单取舍、store 限额、DESCRIPTION 措辞）｜未决问题。
遇阻塞：报告后停止，不越界自救。
