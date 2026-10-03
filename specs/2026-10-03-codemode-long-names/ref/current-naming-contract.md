# Ref · 现行命名契约（组内副本即权威，2026-10-03 master 基线）

> worker 只信本副本的行号锚点（基线时刻）；落笔前以 worktree 内文件为准。

## 1. `mocode/host/plugin/builtin/codemode/toolbox.py`（T1 主场）

```python
#: The MCP naming prefix — the full name of an MCP tool is
#: ``mcp__<server>__<tool>`` (see ``mcp/naming.py``).
_MCP_PREFIX = "mcp__"                                    # L36 删


def _mcp_short_name(full: str) -> str | None:            # L44-59 整函数删
    """The MCP short name of a registered tool name — ``mcp__k__bash`` →
    ``bash`` — or ``None`` for a name that is not MCP-style.
    ...
    """
    if not full.startswith(_MCP_PREFIX):
        return None
    rest = full[len(_MCP_PREFIX):]
    if "__" not in rest:
        return None
    short = rest.rsplit("__", 1)[1]
    return short or None
```

`__init__` 里三张表（L99-132）删两张、留 `self._tool_names`：

```python
        self._tool_names = sorted(
            t.name for t in registry.all() if t.name != "codemode"
        )                                                # 留
        tools = [t for t in registry.all() if t.name != "codemode"]
        counts: dict[str, int] = {}
        for tool in tools:                               # _attr_map 构造 L103-112 删
            attr = normalize(tool.name)
            counts[attr] = counts.get(attr, 0) + 1
        self._attr_map = {... if counts[attr] == 1}
        shorts: dict[str, int] = {}                      # _short_map/_short_names L113-132 删
        ...
```

`_resolve` 现状（L153-179）→ 新语义见工单 T1 的四步级联；歧名分支整段删：

```python
    def _resolve(self, name: str):
        if name == "codemode":
            raise CodemodeError("codemode cannot be called from a script")
        if self._registry.get(name) is not None:
            return name
        target = self._attr_map.get(name)      # 删
        if target is not None:
            return target
        target = self._short_map.get(name)     # 删
        if target is not None:
            return target
        message = f"unknown tool {name!r}; use search_tools() or all_tools()"
        candidates = self._short_names.get(name, [])
        if len(candidates) > 1:                # 歧名分支删
            listed = ", ".join(repr(c) for c in sorted(candidates))
            message += f" — ambiguous short name, candidates: {listed}"
            raise CodemodeError(message)
        if name in self._facade:
            return _FACADE
        raise CodemodeError(message)
```

模块 docstring L1-17 现值（含 "three-tier" 措辞，要改）：

> 4-6 行：`:class:`ToolBox` is the ``tools`` name a script runs with: attribute and
> subscript access bind one tool call through a three-tier resolution
> (exact registered name, normalized name, unambiguous MCP short name), ...`

类 docstring L62-81 现值（含 "The normalized and short forms only resolve when
unambiguous — a collision raises instead of guessing, listing the candidates."，要改）。

`from .search import normalize`（L27）：**保留的唯一理由**是新 `_candidates()` 的折叠比较。

## 2. `mocode/host/plugin/builtin/codemode/description.py`（T3 主场）

L53-59 现值（三层命名段，重写对象）：

```text
`tools` resolves names in tiers: exact registered name
(`tools["mcp__dev_radius__search"]`), normalized form
(`tools.mcp__dev_radius__search`), then the MCP short name (`tools.search`)
when unambiguous — ambiguous short names raise listing the candidates,
exact names win. `codemode` cannot call itself. The facade forgives the
built-ins: `tools.describe_tool`/`tools.text`/`tools.store`/... return the
built-in itself; `dir(tools)` and the catalogue list registered tools only.
```

L19-35 主示例（不动，本就全名）：`describe_tool("mcp__issues__list_issues")` →
`await tools.mcp__issues__list_issues(team="Pi", ...)` → `parallel(*[...])` →
`store("issue_activity", scored)` → `return {...}`。

## 3. `mocode/host/plugin/builtin/mcp/plugin.py`（T2 主场）

`_render_mcp_servers` 内 render 片段（L55-92 基线）：

```python
        lines = []
        visible = set(runtime._ctx.tools.names(audience="program"))
        for key, cfg in runtime.config.items():
            ...
            registered = runtime._registered.get(key, {})
            if session is not None and session.state == STATE_CONNECTED and registered:
                names = sorted(
                    raw for full, raw in registered.items() if full in visible
                )                                        # → 改全名：
                                                         #   sorted(full for full in registered if full in visible)
```

docstring L43-46 现值（"lists its tools' raw names (the registry, decision D12)"）要改写。

事实：`runtime._registered: dict[key, dict[full_name → raw_name]]`（`runtime.py:256-274`，
`current[full_name] = raw_name`）。`visible` = program-audience 投影（全名集）。
`_PROMPT_TOOL_NAME_LIMIT = 30`（L30）不动。

## 4. `docs/plugins.md` L885-896 现值（T4 重写对象）

> - `await tools.<name>(args)` calls a tool — *args* is a dict, or use
>   keyword arguments. Own tools keep their name (`tools.bash`); an MCP tool
>   answers to its folded full name — `tools["mcp__dev_radius__search"]`, or
>   the attribute `tools.mcp__dev_radius__search`, or the same name written
>   with hyphens, which normalizes to it — and, when unambiguous, to its
>   short name (`tools.search`); an ambiguous short name raises listing the
>   candidates, and the exact name always wins. The facade forgives the
>   built-ins — `tools.describe_tool`, `tools.text`, `tools.store`, ... bind
>   the built-in itself, so either spelling works. `dir(tools)` and the
>   catalogue list registered tools only, and `codemode` cannot call itself.

## 5. `mocode/host/plugin/builtin/codemode/README.md` 三处（T4）

- L18-21 模块图：`toolbox.py` — ToolBox: the **three-tier name resolution (exact →
  normalized → unambiguous MCP short name)**, ...
- L64-71 门面段：Registered tools always win — **an ambiguous short name still names its
  candidates**, `codemode` itself is still refused first, ...
- L72-78 snapshot 段：the ToolBox's **normalized-name and short-name maps freeze with it**。
  Exact-name lookup and `describe_tool()` read the live registry instead

## 6. 测试锚点（基线行号，T5）

`tests/test_builtin_codemode.py`：L33 import `_mcp_short_name`；L353-407
`test_the_name_resolution_tiers`；L441-486 `TestFacadeFallout...and_a_tool_wins`；
L488-502 `TestShortNameRule`（8 参数化用例）；L1044-1096
`test_description_covers_the_v2_contract`；L1796-1837 `TestMcpShortNames`
（e2e 脚本常量 `SCRIPT_CALL_ECHO`（L1716）本就跑全名）。

`tests/test_builtin_mcp.py`：L2001-2006 `_tool_lines` helper（结构解析，不动）；
L2122 `assert catalogues["alpha"] == ["ask", "fail", "pic", "search"]`；
L2159 截断用例区。

## 7. 本地裸名工具（不受影响，精确 tier 照旧可达）

`filesystem.py`：`read`（L128）、`write`（L184）、`edit`（L230）；`shell/tool.py`：`bash`（L95）
等。`tools.read(...)` / `tools.bash(...)` 走 `registry.get` 精确命中，不涉两级删除。
