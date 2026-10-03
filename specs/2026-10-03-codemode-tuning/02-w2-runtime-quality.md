⏸️ 待派工 — 前置 W1 合并

# Spec 02 · W2：codemode 运行时质量（波次 W2）

> 分支 `feat/codemode-runtime`；worktree 由 lead 建。
> 前置：W1 已合并 master。深读：`00-overview.md` §2、`ref/field-findings.md`、
> 本工单 §1 代码事实、`01-w1-script-surface.md`（W1 落地后的新表面——`all_tools()`、
> Mapping 结果、短名规则，你的测试要用它们）。
> 绝不操作主检出，绝不 merge/push/tag/checkout master。

## 0. 目标

三件运行时质量事：脚本错误带真实行号与源码片段（修 SyntaxError off-by-one）；
显式 deadline 触发时**保留已攒输出**；新增可选并发上限。**不碰 core**——deadline
由插件内部自包，不依赖 dispatcher 行为变化。

## 1. 已核实的代码事实

- 脚本执行：`runtime.py:71-77`——`source = "async def __codemode__():\n" + textwrap.indent(script, "    ") + "\n"`，
  `compile(source, "<codemode>", "exec")`。**脚本第 k 行 = 报告行号 −1**（wrapper 占第 1 行；
  indent 只加空格不加行）。全插件无 traceback/lineno 处理；错误渲染仅
  `output.py:97` `text += f"\nScript error: {type(error).__name__}: {error}"`；
  `plugin.py:126-127` 捕获。SyntaxError 现在报 `line 2`（实为脚本第 1 行）。
- 超时链：`plugin.py:80-90`——`options.timeout_ms`（ceil 成秒，`max(1,…)`）→ 首行
  `# @options: {…}`（`plugin.py:55,58-73`，显式参数优先）→ `plugins.codemode.timeout_s`
  → `None`（回落 agent `tool_timeout`，默认 240s）。执行在 `plugin.py:118`
  `await run_script(script, env)`；dispatcher 的 `asyncio.wait_for`（`core/dispatch.py:244-259`）
  超时 → `tc.cancel_event.set()` + `status=TOOL_TIMEOUT`；`plugin.py:122-125` 对
  `CancelledError` 原样上抛 → **部分输出全丢**。
- 输出结构：`output.py:24-44` 有序 items；`build_result`（`output.py:103-123`）→
  `compose`（`:81-100`）→ details `{"ok","images","truncated","full_output_path","tool_calls"}`。
- 并发：无任何信号量（全仓 grep 零命中）；`api.py:160-170` 每次调用裸协程。
- env 构建：`api.py:296-348 build_env()`——W1 后含 `all_tools`；`plugin.py:97-118`
  组装 store/registry 并调用。
- 测试锚点：运行时组 `tests/test_builtin_codemode.py:59-107`（仅断言 `"<codemode>" in str`）；
  options/policy 组 `:702-740`（只测取值链，无真实超时用例）；截断/输出组 `:351-463`。

## 2. 工单（三个 commit，每个独立过全量门禁）

### T1 错误带行号与源码片段
commit：`feat(codemode): report script errors with line numbers`
- **D10**：捕获脚本异常时用 `traceback` 提取**最内层 `<codemode>` 帧**的 lineno，
  换算真实行号（−1 wrapper 偏移），渲染为
  `Script error (line N): {Type}: {msg}` + 下一行该行源码（从脚本原文按行切，trim 展示）；
  帧不在 `<codemode>`（如工具内部异常已由 ToolCallError 承载）时维持现格式。
  `SyntaxError` 用其 `lineno` 同样 −1 换算（修 off-by-one），并附该行源码。
- 渲染落在 `output.py`（compose 的 error 段），偏移换算工具放 `runtime.py`（compile 处
  知道 wrapper 形状）；`plugin.py` 把异常与脚本原文传给渲染。
- 测试：第 3 行抛 `ValueError` → `(line 3)` + 该行源码；深一层嵌套函数内的异常仍报
  `<codemode>` 行号；SyntaxError 报真实行号；工具调用失败（ToolCallError）不带行号。

### T2 显式 deadline 保留部分输出
commit：`feat(codemode): keep partial output when a deadline fires`
- **D6**：`plugin.py` 在**显式** deadline（options/`@options`/`timeout_s`）存在时，
  对脚本协程自包 `asyncio.wait_for(deadline)`；`TimeoutError` → 取消脚本任务后**正常返回
  结果**：内容 = 已攒输出 + `\nScript timed out after {n}s.`，details 增 `"timed_out": true`，
  `ok=False`。不抛 ToolError（那会再丢输出）。未设 deadline → 现状不变（dispatcher 兜底）。
  整轮取消（`CancelledError`）仍原样穿透（`plugin.py:122-125` 语义不变）。
- 注意与 dispatcher 的关系：deadline 通常 ≤ `tool_timeout`（240s 默认）所以插件先触发；
  若配置得更大，dispatcher 先触发、行为同现状——文档（W3）写明。
- 测试：假慢工具（sleep）+ 小 `timeout_ms` → 部分输出在、`timed_out` 标记、无
  TOOL_TIMEOUT 状态；未设 deadline 的长脚本仍走 dispatcher 超时（现状）；整轮取消穿透。

### T3 可选并发上限
commit：`feat(codemode): add an optional concurrency cap`
- **D5**：`plugins.codemode.max_concurrency`——正整数；非法（≤0/非 int）report+忽略；
  **默认 None = 不限**（行为零变化）。实现：`plugin.py` 读配置，经参数传入 `build_env`，
  每脚本建一个 `asyncio.Semaphore`；`ToolBox` 的每次调用 acquire/release 包住 dispatcher
  调用（W1 后的 `api.py` 上加参数与包装；gather 语义不变，只是排队）。
- 测试：cap=1 时调用严格串行（用事件顺序断言）；默认不限（并发全部在飞）；非法值 report+不限。

## 3. 验收

1. 每 commit 后 `uv run pytest -q; echo "EXIT: $?"` 全绿、退出码 0、数量 ≥ W1 合并值且递增。
2. `uv run pytest tests/test_builtin_codemode.py tests/test_builtin_mcp_codemode.py -q` 全绿。
3. `git diff --stat <W1 合并点>` 只含 `builtin/codemode/{api.py,runtime.py,plugin.py,output.py}`、`tests/test_builtin_codemode.py`；core/host.py/mcp 包/pyproject/uv.lock/docs/specs 零 diff。
4. `import mocode` 计时 3 次 <1ms；`-X importtime` 无 `mcp`。

## 4. 禁触清单

`mocode/core/**`；`mocode/host/plugin/host.py`；`builtin/mcp/**`；`builtin/codemode/description.py`（W3 所有）；`builtin/codemode/search.py`（无理由不动）；其它测试文件；`pyproject.toml`/`uv.lock`；`README.md`/`docs/**`；spec 文件。

## 5. 最终报告格式

同组惯例：工单状态表｜commit 清单｜自测真实结论｜偏差与取舍（行号换算的边界：多行
字符串/装饰器/嵌套函数；deadline 与 dispatcher 的竞态论证；semaphore 的循环绑定时机）｜
未决问题。遇阻塞报告后停止。
