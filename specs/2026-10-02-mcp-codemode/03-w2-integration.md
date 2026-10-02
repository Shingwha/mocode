✅ 2026-10-02 @ feat/mcp-codemode-integration

# Spec 03 · W2：集成 + 文档 + 端到端（波次 W2）

> 分支 `feat/mcp-codemode-integration`；worktree `C:\Users\shifu\.worktrees\mocode\feat-mcp-codemode-integration`。
> 前置：W1a（`feat/mcp-stdio`）与 W1b（`feat/codemode`）**都已合并**。
> 深读：`00-overview.md` + 两份工单 + `ref/mocode-api.md`。
> 你是唯一允许改 `host.py`、`README.md`、`docs/**` 的波；绝不操作主检出，绝不 merge/push/tag。

## 0. 现状事实（已核实，2026-10-02）

- `mocode/host/plugin/host.py:58` `builtin_plugins()` 返回固定顺序的 8 个 `PLUGIN`：
  `filesystem, shell, skills, default_prompts, session, help, effort, cache_protect`。
- `mocode/host/plugin/loader.py` 模块 docstring 与 `docs/plugins.md:11`、`README.md:181`、
  `docs/ARCHITECTURE.md:396` 都写着 `mcp.json (recognised, not served yet)` —— **本波要改**。
- `docs/plugins.md` "Rules of the road" 有一行保留名：`filesystem, shell, skills,
  default-prompts, session, help, effort, cache-protect`；README 有 "Built-ins" 表。
- `docs/ARCHITECTURE.md` 的模块图/内置插件计数需与新顺序一致。
- W1a/W1b 的产物：`builtin/mcp/`（导出 `PLUGIN`）、`builtin/codemode/`（导出 `PLUGIN`），
  及各自测试。二者在 W1 内自测已证明可独立加载。

## 1. 目标

把两个插件接入默认装配路径（内置列表 + 保留名），更新四份文档使 `mcp.json` 从
"recognised, not served" 变为 "served by the `mcp` builtin"；写一份跨插件的端到端测试，
证明 `mcp` 的 program-only 工具能被 `codemode` 脚本调用且遵守 program-origin 契约。

## 2. 冻结改动

### 2.1 `builtin_plugins()` 顺序（唯一允许的 host 代码改动）

```python
return [
    filesystem.PLUGIN,
    shell.PLUGIN,
    skills.PLUGIN,
    mcp.PLUGIN,          # 新增
    codemode.PLUGIN,     # 新增
    default_prompts.PLUGIN,
    session.PLUGIN,
    help.PLUGIN,
    effort.PLUGIN,
    cache_protect.PLUGIN,
]
```

- import 加进函数内的 `from .builtin import (...)` 惰性块（保持 `builtin_plugins()` 惰性 import 的现状）。
- **保留名**：`mcp`、`codemode` 进入 docs/plugins.md 的保留名行；README built-ins 表加两行。
- 顺序理由写进代码注释一行：MCP（工具来源）在 codemode（编排）之前，与 MCP 的
  `default_exposure=auto` 读 `plugins.codemode.enabled` 无关（那是配置判断）。

### 2.2 `loader.py` docstring 一行

`mocode/host/plugin/loader.py` 顶部文件布局注释里的
`mcp.json ... (standard; recognised, not served yet)` 改为
`(standard; served by the `mcp` builtin)`。**只改这一句 docstring**，不改任何代码逻辑。

### 2.3 文档

| 文件 | 改动 |
|---|---|
| `README.md` | 布局树的 `mcp.json` 说明；Built-ins 表加 `mcp` / `codemode` 两行（名称 + 一句话）；Configuration 节的 `plugins` 示例补 `mcp`/`codemode` 键 |
| `docs/plugins.md` | 布局树 `mcp.json` 说明；"Rules of the road" 保留名补 `mcp`/`codemode`；新增一节 "The `mcp` and `codemode` builtins"：配置、exposure 表、启用方式、codemode DSL 一句话 + 指向 `ref` 不适用（外部读者看不到 spec，故在 docs 里写全 API 摘要） |
| `docs/ARCHITECTURE.md` | 模块图里 builtin 计数/列表补两个；`mcp.json` 行改为 served |
| `AGENTS.md` | **不改**（其"Adding a builtin plugin"清单指向 docs/plugins.md 与 README，已由本波更新） |

文档 API 摘要必须与 `ref/codemode-dsl.md` §2–§5 一致；不得承诺 v1 未实现的能力
（`only`、`models`、`describe_namespace`、HTTP、resources 要标 "not yet"）。

### 2.4 端到端测试 `tests/test_builtin_mcp_codemode.py`（新增）

用 W1a 的假 stdio server 方式（可复用其 helper；若 helper 在 `test_builtin_mcp.py` 内，
把可复用的 builder 提到本文件或一个 `tests/_mcp_fake.py` 小 helper —— 但**不得改** W1 的测试
断言，只许可新增/抽取 helper 且不得让 W1 测试重写）。用例：

1. **默认装配**：`make_mc()` 建 conversation（`plugin_dirs=[]`），断言
   `conv.tools` 里 `mcp_status`、`codemode` 都在；模型 audience 不含 program-only MCP 工具。
2. **exposure=direct**：server 的某工具模型可见；`codemode` 也能调。
3. **exposure=codemode（默认，因 codemode 启用）**：工具对模型不可见、对 program 可见。
4. **脚本调 MCP**：`plugins.codemode.enabled=true` + 一个 codemode-exposure 的假工具，
   让 `codemode` 工具跑一段脚本 `text((await tools.mcp__<srv>__<tool>({...})).content)`，
   断言输出正确、`ToolCallStarted/Finished(origin="program", parent_call_id=<codemode call id>)`
   出现在事件流、且 `conv.messages` 里**没有**该 MCP 调用的 tool message。
5. **warning**：codemode 禁用 + codemode-exposure 工具 → prepare 后收到一条 `Notice(level="warn")`，
   且只收到一次。
6. **关闭**：`await conv.aclose()` 后假 server 子进程退出（`proc.returncode is not None`），
   `mcp_status` 的 runtime 已 shutdown。

## 3. 工单（每项一个 commit）

### T1 注册进 builtin 列表 + loader docstring
- 2.1 / 2.2；`uv run pytest -q` 全绿（注册可能影响默认测试的 prompt/工具断言，
  逐条检查 `tests/` 是否因新增内置而有断言漂移；若有，**只改受影响的既有测试断言**，
  并在报告逐条列明"为什么必须改"）。
- commit：`feat: register the mcp and codemode builtins`

### T2 文档四件
- 2.3；确保无 "not served yet" 残留：`grep -rn "not served" README.md docs/ AGENTS.md`.
- commit：`docs: document the mcp and codemode builtins`

### T3 端到端测试
- 2.4；新增 `tests/test_builtin_mcp_codemode.py`。
- commit：`test: cover mcp and codemode together`

## 4. 验收（完成前自测，报告真实结论）

1. `uv run pytest -q` 全绿，独立退出码 0，数量 **≥ W1 合并后的数量**（报告实际值）。
2. `uv run pytest tests/test_builtin_mcp_codemode.py -q` 全绿。
3. `grep -rn "not served" README.md docs/ AGENTS.md` 零命中。
4. `uv run python -c "from mocode.host.plugin.host import builtin_plugins;
   print([p.name for p in builtin_plugins()])"` 真实输出含 `mcp`、`codemode`，顺序符合 2.1。
5. `git diff --stat` 只含 2.1 的 `host.py`、`loader.py` docstring、四份文档、
   端到端测试（以及 T1 明确列出的受影响既有测试）。**core 零 diff。**
6. `import mocode` 计时 <1ms（`uv run python -X importtime -c "import mocode"` 或重复计时）。

## 5. 禁触清单

- `mocode/core/**` 全部。
- `mocode/host/**` 中除 `plugin/host.py`（仅 `builtin_plugins()` 与 import）与
  `plugin/loader.py`（仅那一句 docstring）外的一切。
- `mocode/host/plugin/builtin/mcp/**`、`builtin/codemode/**`（W1 的产物；除非端到端测试
  需要抽取 helper 到新文件，不得改其实现）。
- `docs/testing.md`、`docs/embedding.md`、`docs/api.md`、`docs/providers.md`（除非
  "not served" 命中，否则不改）。
- spec 文件（归 lead）。

## 6. 最终报告格式

工单状态表｜commit 清单（hash+message）｜自测真实结论（命令 + 退出码）｜
偏差与取舍（尤其：T1 是否必须改既有测试断言，逐条理由）｜未决问题。
遇阻塞：报告后停止，不越界自救。
