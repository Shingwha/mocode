# Ref · 推翻依据（组内副本即权威）

> worker 只信本副本，不回 `specs/2026-10-03-codemode-tuning/` 主组差找（该组 ✅ 已完成，不改写历史）。
> 下列三节原文抄录自该组 `00-overview.md` 拍板表与 `ref/field-findings.md`，本组 D1-D4 即对它们的推翻。

## 旧 D2（命名，本组推翻）

> | D2 | 工具命名：本身工具用本名（零变化）；MCP 工具 = 折叠全名 + 归一化名 + **无歧义短名**（歧义报错并列候选）；`tools.x` 与 `tools["..."]` 同一套匹配；修掉 description 带连字符的错误示例 | 用户澄清 + field-findings P0-3；现 `__getitem__` 仅精确匹配（`api.py:133-134`），文档教的 `tools["mcp__dev-radius__search"]` 必失败 |

## 旧 D12（prompt 名单，本组推翻其命名路线一半）

> | D12 | **prompt 名单**（用户拍板 2026-10-03）：`mcp_servers` 段每个可达 server 下列**工具名清单**（只名字不 schema）；用法（字段/description）按需经 codemode `describe_tool()` 获取；每 server 名单设上限，超出提示 `search_tools()`；段在会话开始定稿，订阅新增的工具不在名单但脚本可用（文档写明）。命名路线同时拍板：折叠规范名做 key（注册表/事件/权限稳定），沙箱三拼法全收（短名/连字符全名/精确名），与 Claude Code 同构 | 用户讨论裁决；pi declarations 模式的名单版 |

保留不动：名单本身、按需 `describe_tool()`、上限与 `search_tools()` 指针。推翻的只有"名单列 raw 名 + 三拼法全收"这半句。

## 旧 D15（门面回退，本组推翻其级联）

> | D15 | **门面回退解析**（用户拍板 2026-10-03，修正原"报错提示"裁决）：`tools.x` 查找 = 注册工具（精确 → 归一化 → 短名）→ **内建名回退**（`describe_tool`/`all_tools`/`search_tools`/`store`/`load`/`text`/`console`/`image`/`print`/`exit` 直接返回内建函数）；**目录仍只列注册工具**——门面宽容、目录严格；`tools.codemode` 自调用拒绝维持 | 模型猜 `tools.describe_tool` 零往返浪费；报错提示省不了那轮（脚本抛错即死，仍须重发 codemode） |

本组保留：内建名回退、目录只列注册工具、`codemode` 自调用拒绝。推翻的只有级联里的两级（归一化、短名）。

## 旧组不变式 #3（被本组有意打破，仅限 prompt 字节这一项）

> 3. 不启用 codemode 时对外行为零变化；启用时的行为变化必须全部落文档（D11）。

新组 D2：mcp 一启用，`mcp_servers` 段名单即由 raw 名变全名——prompt 字节变化与 codemode 开不开无关，本组记档为有意行为变更。

## field-findings P0-3 原文（短名 tier 的历史来处）

> ### P0-3　工具命名两套，且不支持互通
>
> | 场景 | 命名 |
> |---|---|
> | 工具接口（模型侧可见） | `mcp__k__bash` |
> | `ALL_TOOLS` / `search_tools()` 返回 | `bash` |
> | 实际可用 | `bash` |
>
> - **现象**：`tools["mcp__k__bash"]` → `unknown tool 'mcp__k__bash'; use search_tools() or ALL_TOOLS`
> - **评价**：这条错误信息**质量很好**，是全篇唯一给了正确出路的。
> - **建议**：`tools[...]` 同时接受前缀名与短名（或在工具描述里显著标注「沙箱内用短名」）。

读法：P0-3 的原始诉求是"两套命名不互通"。旧 D2 用"全收"缝合了它；本组的判断是缝合面本身成了问题源——短名空间会撞车（多个 server 同名 search/read），于是回到"一套命名"：prompt 段、目录、脚本、错误全部只讲注册全名，unknown 报错负责把 miss 教回全名（D3）。
