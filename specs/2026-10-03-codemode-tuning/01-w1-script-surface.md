✅ 2026-10-03 @ feat/codemode-surface（已合并 master `3c0576f`；998 passed exit 0，lead 合并前后各复核一次；命名路线经用户确认维持：折叠规范名做 key、沙箱三拼法全收）

# Spec 01 · W1：codemode 脚本表面（波次 W1）

> 分支 `feat/codemode-surface`；worktree 由 lead 建。
> 前置：master `a77c45c`（960 passed）。深读：`00-overview.md` §2 决策表、
> `ref/field-findings.md`（用户实测）、本工单 §1 的代码事实（lead 已核实，file:line）。
> 绝不操作主检出，绝不 merge/push/tag/checkout master。

## 0. 目标

修 codemode 沙箱的**脚本表面契约**：结果对象可按 Mapping 访问、两套工具命名统一并支持
无歧义短名、发现表面统一为全小写函数、异常类与 `dir()` 入白名单、`print` 不再静默丢失、
`describe_tool` 只描述可调用的工具。全部改动限 `api.py`/`runtime.py` + 既有测试文件。

## 1. 已核实的代码事实（动手前自行读码确认）

- `api.py:296-348 build_env()`：`env = dict(_MODULES)`（`api.py:47-58`，8 个模块）+
  `tools/text/console/image/exit/store/load/ALL_TOOLS/search_tools/describe_tool`。
- `runtime.py:44-52`：`_RESTRICTED_KEYS` 47 个名字 → `RESTRICTED`；`runtime.py:75`
  `env["__builtins__"] = RESTRICTED`。无 `__import__`、无 `dir`；异常类只有
  `Exception/ValueError/KeyError/IndexError/TypeError`；`print` 在白名单但无人捕获。
- `api.py:96-178 ToolBox`：`__getitem__` 仅精确匹配（`:133-134` → `_bind` `:155`）；
  `__getattr__` 精确 → `_attr_map` 归一化（`:119-131` 只收无歧义项，`:136-149`）；
  错误文案 `:147-149/:156-158` = `unknown tool {name!r}; use search_tools() or ALL_TOOLS`；
  `codemode` 自调用拒绝（`:153-154`）；`calls` 计数器是普通属性（`:118`，注意 `__getattr__`
  只在常规属性查找失败后触发，工具名撞上 `calls` 会被遮蔽——维持现状，但短名/归一化
  映射的构建要继续排除 `codemode`）。
- `api.py:75-93 ToolOutcome`：dataclass，字段 `content/details/status/error_code`，
  `__str__`/`to_dict`；`:171-176` 仅 `status=="ok"` 时构造，否则 `ToolCallError`（`:60-72`）。
- `api.py:277-285`：`ALL_TOOLS` = program audience 注册名（去掉 codemode）→
  `[{"name","description"}]`；`api.py:310` 快照进 env。
- `api.py:328-331 search_tools(query, limit=8, namespace=None)` → `search.py:33-73` rank；
  namespace 过滤 `search.py:47-49`；空 query 按注册顺序截断 `search.py:50-51`。
- `api.py:288-293/345 describe_tool`：live registry、无 audience 滤镜。
- 测试锚点：`tests/test_builtin_codemode.py` 887 行——unknown-tool 文案 `:175-180`；
  发现/env 组 `:253-284`（断言 `ALL_TOOLS` 注入与快照）；连字符归一化属性 `:157-163`；
  歧义归一化拒绝 `:165-173`；ToolOutcome 正面断言 `:138-144`；restricted builtins `:109-126`。

## 2. 工单（三个 commit，每个独立过全量门禁）

### T1 Mapping 结果 + 统一命名 + 发现表面改名
commit：`feat(codemode): mapping outcomes and unified tool naming`
- **D7**：`ToolOutcome` 实现 Mapping 协议——`get(key, default=None)`、`__getitem__`
  （未知键 `KeyError`）、`keys()`（四字段名）、`__contains__`；现有 `.content/.details/
  .status/.error_code/__str__/to_dict` 不变。
- **D2**：`ToolBox` 两入口同一套解析——①精确注册名；②归一化名（沿用现有无歧义约束）；
  ③**MCP 短名**：对以 `mcp__` 开头的注册名，剥前缀后 `rsplit("__", 1)` 取工具段建
  短名映射（注意 server 折叠名可含 `__`，如原名 `a//b` → `a__b`，rsplit 语义要测）；
  短名**无歧义**才可解析，歧义时报错列出候选名。错误文案保持现有格式，歧义时追加候选。
- **D3**：`ALL_TOOLS` 常量 → `all_tools()` 函数（闭包捕获同一启动快照 list，语义不变）；
  env 不再注入 `ALL_TOOLS`；unknown-tool 文案改指 `all_tools()`；`search_tools` 增
  `names_only: bool = False`，True 时返回 `list[str]`。
- 测试：新增/更新——Mapping 四方法正面 + 未知键 KeyError；`tools["mcp__k__bash"]` 与
  `tools.bash` 等价；短名可用与歧义候选（构造两个 server 同工具名）；server 名含 `__`
  的 rsplit 用例；`all_tools()` 注入、快照语义、`ALL_TOOLS` 不再存在；`names_only` 两态。
  既有断言（`:175-180` 文案、`:253-284` env）同步更新。

### T2 异常类 + dir 入白名单
commit：`feat(codemode): allow catching builtin exceptions and dir()`
- **D4**：`RESTRICTED` 构建追加——builtins 中全部 `isinstance(x, type) and issubclass(x, Exception)`
  的名字（自动排除 `BaseException`/`KeyboardInterrupt`/`SystemExit`/`GeneratorExit`，
  它们不是 `Exception` 子类，无需特判）；再加 `dir`。
- 保留现有 47 名与既有排除（`open/eval/exec/compile/input/globals/locals/vars/__import__`）。
- 测试：脚本内 `try/except RuntimeError` 可按名捕获；`dir(obj)` 可用；白名单快照断言
  （更新 `:109-126`）；`asyncio.gather(..., return_exceptions=True)` 后 isinstance 分流的端到端用例。

### T3 print 捕获 + describe_tool 滤镜
commit：`feat(codemode): capture print and filter describe_tool`
- **D8**：env 的 `print` 替换为输出管道函数——`print(*args, sep=" ")` 等价一条
  console 级输出项（非字符串 JSON 化，与 `text()` 现有惯例一致）；宿主 stdout 不再收到。
- **D9**：`describe_tool` 改为与可调用面同源（program audience、去 codemode），
  不可调用的名字返回 `None`。
- 测试：print 三态（字符串/多参/非字符串）进结果；describe_tool 对 model-only 工具返回
  None、对 program 工具返回 schema（更新既有相关断言）。

## 3. 验收

1. 每 commit 后 `uv run pytest -q; echo "EXIT: $?"` 全绿、退出码 0、数量 ≥ 960 且递增。
2. `uv run pytest tests/test_builtin_codemode.py tests/test_builtin_mcp_codemode.py -q` 全绿。
3. `git diff --stat a77c45c` 只含 `builtin/codemode/api.py`、`builtin/codemode/runtime.py`、`tests/test_builtin_codemode.py`；`git diff a77c45c -- mocode/core mocode/host/plugin/host.py mocode/host/plugin/builtin/mocode pyproject.toml uv.lock README.md docs specs` 为空（注：`builtin/mcp/**` 也不许碰）。
4. `uv run python -X importtime -c "import mocode"` 无 `mcp`；import 计时 3 次 <1ms。
5. field-findings 附录 A 重放：第 4/5/6 条在新表面下不再报错；第 3 条 `sorted(search_tools("", names_only=True))` 合法（**lead 更正 2026-10-03**：原写 `all_tools(names_only=True)` 系笔误——D3 冻结的 `names_only` 在 `search_tools` 上，`all_tools()` 保持无参最小表面）。

## 4. 禁触清单

`mocode/core/**`；`mocode/host/plugin/host.py`；`builtin/mcp/**`；`builtin/codemode/` 下
`plugin.py`/`output.py`/`search.py`/`description.py`（W2/W3 所有）；其它一切测试文件；
`pyproject.toml`/`uv.lock`；`README.md`/`docs/**`；spec 文件。

## 5. 最终报告格式

工单状态表｜commit 清单（hash+message）｜自测真实结论（命令+退出码+关键输出）｜偏差与
取舍（尤其：短名映射的实现与歧义语义、rsplit 边界、Mapping 协议与 to_dict 的关系、
print 的 sep/end 处理取舍）｜未决问题。遇阻塞报告后停止。
