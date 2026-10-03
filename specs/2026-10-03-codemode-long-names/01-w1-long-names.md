# Spec 长名 · codemode 调用路径只认全名（波次 W1）
> 分支 `feat/codemode-long-names`（worktree 为界，只此一个 agent）；
> 前置：无；深读：`00-overview.md`（本组）+ `ref/decision-reversal.md` + `ref/current-naming-contract.md`（组内副本即权威，勿回主检出差找）。

## 目标

一段话：**让注册全名 `mcp__<server>__<tool>` 成为 MCP 工具在全仓唯一的可寻地址拼写**——脚本 facade 删掉归一化与短名两级解析、unknown 报错改为报候选全名、mcp_servers prompt 段改列全名、三文档面同步、测试原地改写。最高约束：`mocode/core/**` 零 diff；不加兼容垫片。

## 工单（按序执行，每项一个 commit，每个 commit 前至少两个目标测试文件绿）

### T1 · `toolbox.py`：精确解析 + 报错现算候选
- 文件：`mocode/host/plugin/builtin/codemode/toolbox.py`（行号为基线锚，会漂移）。
- 删：`_MCP_PREFIX`（L36）、`_mcp_short_name()`（L44-59）、`_attr_map` 构造（L103-112）、`_short_map`/`_short_names` 构造（L113-132）、`_resolve` 的二三级（L165-170）与歧名分支（L172-176）。
- 新 `_resolve` 语义（严格按序）：
  1. `name == "codemode"` → `CodemodeError("codemode cannot be called from a script")`（原文案不动）；
  2. `self._registry.get(name) is not None` → 返回 `name`（精确注册名，含本地裸名工具 `read`/`bash`）；
  3. `name in self._facade` → 返回 `_FACADE`（内建名归内建，D4）；
  4. 否则抛 `CodemodeError(self._unknown_message(name))`。
- 候选现算（这是保留 `from .search import normalize` 的唯一理由——折叠比较让 `web-search` 也能找到 `mcp__srv__web_search`）：
  ```python
  def _candidates(self, name: str) -> list[str]:
      """Registered names ending in the same ``__``-tail as *name* — the
      did-you-mean set when a call misses. Folded, so a hyphenated miss
      still finds the folded registered tail."""
      folded = normalize(name).rsplit("__", 1)[-1]
      return sorted(
          tool.name for tool in self._registry.all()
          if tool.name.rsplit("__", 1)[-1] == folded
      )

  def _unknown_message(self, name: str) -> str:
      base = f"unknown tool {name!r}; use search_tools() or all_tools()"
      found = self._candidates(name)
      if len(found) == 1:
          return f"{base} — did you mean {found[0]!r}?"
      if len(found) > 1:
          listed = ", ".join(repr(candidate) for candidate in found)
          return f"{base} — candidates: {listed}"
      return base
  ```
  句式（测试钉形状，三句式）：唯一候选 `unknown tool 'search'; use search_tools() or all_tools() — did you mean 'mcp__anysearch__search'?`；多候选 `... — candidates: 'mcp__a__search', 'mcp__b__search'`；零候选维持 `unknown tool 'search'; use search_tools() or all_tools()` 现状尾句。
- 改写模块 docstring（L1-17）与类 docstring（L62-81）：三层措议、"ambiguous short name"字样、快照冻结归一化/短名表的句子全部删除；补：裸名空间归内建、MCP 工具只以全名可达、miss 报候选全名。docstring 英文，读起来像周围代码。
- `__dir__`/`tool_entries`/`describe_tool_entry` 不动（本就全名）。语义抽检：`tools["mcp__k__bash"]` 与 `tools.mcp__k__bash` 可调；`tools.bash`（MCP 短名）报 did-you-mean；`mcp__k__store` 在场时 `tools.store` 是内建 store。
- commit：`feat(codemode): resolve scripts to the exact registered name`（正文动机：删派生命名空间）。

### T2 · `mcp/plugin.py`：prompt 段改列全名
- 文件：`mocode/host/plugin/builtin/mcp/plugin.py`（L55-92 render）。
- 事实（已核实，`runtime.py:256-274`）：`runtime._registered.get(key, {})` 是 `full_name → raw_name` 映射。`names = sorted(raw for full, raw in registered.items() if full in visible)` → 改 `names = sorted(full for full in registered if full in visible)`。
- docstring L43-46 "lists its tools' raw names (the registry, decision D12)" 改写为全名口径 + 一个词汇表（prompt 段、目录三件套、脚本可寻地址同名字）。截断 tail 文案与 `_PROMPT_TOOL_NAME_LIMIT = 30` 不动。
- server 标题行（`- {cfg.name}: {how}` + description）不动。
- commit：`feat(mcp): list full tool names in the mcp_servers section`。

### T3 · `description.py`：模型契约命名段重写
- 文件：`mocode/host/plugin/builtin/codemode/description.py`（L53-59 段）。
- 把三层命名段改写为一个名字口径：注册全名即唯一拼写，属性或下标同一串；目录三件套与 `mcp_servers` prompt 段列的就是它；miss 时报候选全名（给一句示例句式）。删 "hyphens which normalize to it" 与一切短名措辞。主示例（L19-35，本就全名）与其余段落不动。
- commit：`docs(codemode): teach one tool name — the registered full name`。

### T4 · 用户文档与插件 README 同步
- `docs/plugins.md` L885-896 命名 bullet 重写：自有工具保留本名（`tools.read`）；MCP 工具只应答注册全名——`tools["mcp__dev_radius__search"]` 或属性 `tools.mcp__dev_radius__search`，同一串；miss 报候选全名。删连字符教学与短名措辞。其余段落不动。
- `mocode/host/plugin/builtin/codemode/README.md` 三处：L18-21 模块图（"three-tier name resolution (exact → normalized → unambiguous MCP short name)" → exact-name resolution + 报错候选）、L64-71 门面段（"an ambiguous short name still names its candidates" 等措术改为新错误语义）、L72-78 snapshot 段（删"归一化/短名表随快照冻结"句，精确查找与 `describe_tool()` 走 live registry 的语义保留）。
- commit：`docs: one tool-name vocabulary across script, prompt and catalogue`。

### T5 · 测试原地改写（不新增文件）
- `tests/test_builtin_codemode.py`：
  - 删 L33 `_mcp_short_name` import；删 `TestShortNameRule` 整类（L488-502）。
  - `TestToolBox.test_the_name_resolution_tiers`（L353-407）改写为：精确名优先不变；归一化拼写与裸短名**不再可达**，报错带候选（单/多两种句式各一例）；`codemode` 自调用拒绝保留；顺手把 `_box`  fixture 用法中靠短名的中转断言换成全名。
  - `TestFacadeFallback...and_a_tool_wins`（L441-486）改写并更名（如 `..._and_the_full_name_reaches_the_mcp_tool`）：`mcp__k__store` 在场时 `box.store` 是内建 store；`box["mcp__k__store"]` 绑 MCP 工具。
  - `TestMcpShortNames`（L1796-1837）更名 `TestMcpTools`（e2e 脚本本就跑全名，不动脚本本体，只更名与类 docstring）。
  - `test_description_covers_the_v2_contract`（L1044-1096）：如其期望词表含被删措辞则同步改；否则不动。
  - 收尾自查 `grep -n "short" tests/test_builtin_codemode.py` 零命中。
- `tests/test_builtin_mcp.py`：catalogue 断言由 raw 名改全名（L2122 `["ask", "fail", "pic", "search"]` → 全名形如 `mcp__alpha__ask` 等，按 fake 注册事实落笔）；截断用例（L2159 区域）同步。`_tool_lines` helper 结构解析不动。
- commit：`test: rewrite the name-resolution assertions onto full names`（正文记 pre/post 用例数）。

## 验收（完成前自测，报告给真实结论）

1. `uv run pytest tests/test_builtin_codemode.py tests/test_builtin_mcp.py -q; echo $?` 绿（退出码独立确认）。
2. `uv run pytest -q; echo $?` 全绿；报告 pre/post 用例数（基线 506 passed / exit 0）与删除清单逐条对账 D5。
3. `grep -rn "short name" mocode/ docs/plugins.md` 零命中（specs/ 历史除外）。
4. 行为抽验（写进测试或报告附真实输出）：上面 T1 的四条语义 + prompt 段 render 含全名。

## 禁触清单

`mocode/core/**`、`cli/`、`providers/`、`pyproject.toml`、`uv.lock`、`README.md`、
`specs/**`（本组只读）、`tests/` 除上述两文件外的任何文件、`.zcode/`、主检出与其它 worktree。

## 最终报告格式

工单状态表（T1-T5 各一行：状态/commit hash+message）/ commit 清单 / 自测真实结论（命令 + 退出码 + pre/post 用例数）/ 偏差与取舍 / 未决问题 / 阻塞记录（有则记，无则写无）。

## 环境事实

Windows 11 + Git Bash；uv + Python 3.13；路径含非 ASCII（桌面）；无浏览器；
pytest 每例 30s 超时 autouse；裸 sleep 会被 conftest 守卫打红——等待用
`asyncio.Event` / `wait_until(predicate, bound=…)` / `settle()`；
Windows asyncio 子进程 pipe 析构 `PytestUnraisableExceptionWarning` 为已知噪音，忽略。
