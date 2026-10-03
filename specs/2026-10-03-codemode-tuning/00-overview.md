✅ 2026-10-03 建组（基线 master `a77c45c`，960 passed；W1 待派工）

# Spec Group · codemode 调优（2026-10-03）

> Group: `specs/2026-10-03-codemode-tuning/`
> 来源：用户首日用 codemode 的踩坑实录（`ref/field-findings.md`，12 条发现 lead 已逐条
> 对照代码核实为真，另发现 4 个补充问题）；讨论后拍板全量修。
> worker 不改 spec、不写状态；完成信号 = 最终报告 + 分支 commit。git 是唯一进度权威。

---

## 1. 目标

把 codemode 沙箱表面从「能撞对」调到「首次即中」：契约与文档零偏差、发现类表面统一、
运行时有护栏。**最高约束**：`mocode/core/**` 零 diff；不启用 codemode 时对外行为零变化
（prompt/工具列表/事件/session 字节不变，基线 960 passed 只增不减）。

## 2. 已拍板决策（不再反复）

| # | 决策 | 依据 |
|---|---|---|
| D1 | 范围全量：契约修复 + 沙箱表面 + 运行时护栏 + 文档收口 | 用户拍板 |
| D2 | 工具命名：本身工具用本名（零变化）；MCP 工具 = 折叠全名 + 归一化名 + **无歧义短名**（歧义报错并列候选）；`tools.x` 与 `tools["..."]` 同一套匹配；修掉 description 带连字符的错误示例 | 用户澄清 + field-findings P0-3；现 `__getitem__` 仅精确匹配（`api.py:133-134`），文档教的 `tools["mcp__dev-radius__search"]` 必失败 |
| D3 | **`ALL_TOOLS` → `all_tools()`**：发现表面统一全小写函数——`all_tools()`（同一启动快照）、`search_tools(query, limit=8, namespace=None, names_only=False)`、`describe_tool(name)`；不留别名；unknown-tool 文案同步 | 用户拍板；DSL 一天大，改名成本≈0；surface 读者是 LLM，常量大写惯例无意义 |
| D4 | 异常白名单：builtins 中全部 `Exception` 子类 + `dir()`；**不**含 `BaseException`/`KeyboardInterrupt`/`SystemExit`/`GeneratorExit`（规则本身即排除——它们不是 Exception 子类），防脚本吞取消信号 | field-findings P1-4 + lead 补充；`gather(return_exceptions=True)` 后要能 isinstance 分流 |
| D5 | 扇出：新增 `plugins.codemode.max_concurrency`（正整数；非法 report+忽略），**默认不限**——行为零变化，护栏用户自开 | 用户拍板 |
| D6 | 超时保部分输出：codemode 内部自包 deadline（**仅当** `options.timeout_ms`/`@options`/`plugins.codemode.timeout_s` 显式设置时），超时 → ok=False 正常结果 + 已攒输出 + "timed out after Ns" 标注；未设置维持现状（dispatcher 的 wait_for 兜底，`core/dispatch.py:244-259`）；**不碰 core**；整轮取消（CancelledError）仍原样穿透 | lead 补充：现在超时丢弃全部部分输出（`plugin.py:122-125` 原样上抛），长扇出最惨死法 |
| D7 | `ToolOutcome` 实现 Mapping 协议（`get/__getitem__/keys/__contains__`，覆盖四字段 `content/details/status/error_code`；`__getitem__` 未知键 KeyError）；文档补写 `error_code` | field-findings P0-4；现纯 dataclass（`api.py:75-93`），第 4 字段无文档 |
| D8 | `print` 接入输出管道（等同 console.log 一条；非字符串 JSON 化同现有惯例）——现在 print 在白名单里但输出进宿主进程、永远不进结果 | lead 补充 |
| D9 | `describe_tool` 加 audience 滤镜：与可调用面同源（program audience 去掉 codemode），不可调用的返回 None | lead 补充：现用 live registry 无滤镜（`api.py:288-293`），能描述调不了的工具 |
| D10 | 脚本错误带真实行号 + 源码片段；换算按 wrapper 偏移（source 第 1 行是 `async def __codemode__():`，脚本第 k 行 = 报告行号 −1），顺带修 SyntaxError off-by-one | field-findings P1-3 + lead 补充 |
| D11 | 文档收口：`description.py`（模型契约）重写——名字已注入全局/直接用别 import、顶层 await 别包 asyncio.run（含 ensure_future 需显式 await）、命名规则、结果四字段与新发现表面、输出方式与截断、deadline 语义；`docs/plugins.md` 同步；新建 `codemode/README.md`（mcp 有、codemode 没有） | field-findings P0-1/P0-2/P2-2/P2-4 |

## 3. 波次表（同包文件强耦合 → 串行，一波一个 agent）

| 波 | 分支 | 工单 | 内容 | 前置 | 写范围 |
|---|---|---|---|---|---|
| W1 | `feat/codemode-surface` | `01-w1-script-surface.md` | ToolOutcome Mapping + 统一命名/短名 + `all_tools()` 改名 + `names_only`；异常类 + `dir`；`print` + `describe_tool` 滤镜 | — | `builtin/codemode/api.py`、`runtime.py`、`tests/test_builtin_codemode.py` |
| W2 | `feat/codemode-runtime` | `02-w2-runtime-quality.md` | 错误行号+片段；超时保部分输出；`max_concurrency` | W1 合并 | `output.py`、`plugin.py`、`runtime.py`、`api.py`、测试 |
| W3 | `feat/codemode-docs` | `03-w3-docs.md` | `description.py` 重写 + docs 同步 + codemode README | W2 合并 | `description.py`、`docs/plugins.md`、`codemode/README.md`（新建）、措辞断言测试 |

每波独立过全量门禁；lead pre/post 门禁后 `merge --no-ff`；红了整分支打回，不手改。

## 4. 全局不变量（每份工单引用，违反即打回）

1. `uv run pytest -q` 全绿、退出码独立确认（`; echo $?`，禁管道吞）；数量相对合并基线只增不减；每 commit 独立过门禁。
2. `mocode/core/**` 零 diff；`pyproject.toml`/`uv.lock` 零 diff；`import mocode` <1ms、SDK 不进导入图。
3. 不启用 codemode 时对外行为零变化；启用时的行为变化必须全部落文档（D11）。
4. 插件实例无状态（状态放 `build()` 产物，不放 `self`）；无全局状态；依赖构造注入。
5. 写入范围外零 diff；遇阻塞报告后停止，不越界自救。
6. 代码/注释/docstring/commit 英文；commit 小写祈使句 `feat:`/`fix:`/`docs:`/`test:` 前缀，标题一行 + 正文动机。
7. worker 不改 spec、不写状态；不 merge/push/tag、不碰主检出与其它 worktree。

## 5. 验收（组级）

1. 每波门禁递增（960 → …），退出码独立确认。
2. **field-findings 附录 A 的 7 条原始错误全部重放**：短名（`tools["mcp__k__bash"]`）、`res.get("content")`、`dir(res)`、按名捕获 `RuntimeError`、`sorted(all_tools())` 不再 TypeError（元素仍 dict，但 `names_only=True` 给了字符串表）、import / `asyncio.run` 由新描述直接规避、`ensure_future` 语义落文档。
3. 文档三表面（description / plugins.md / README）与新行为逐字一致，无一处与代码矛盾。

## 6. 环境

Windows 11 + Git Bash；uv + Python 3.13；路径含非 ASCII 目录名（桌面）；无浏览器；Windows asyncio 子进程 pipe 析构 `PytestUnraisableExceptionWarning` 为已知噪音。
