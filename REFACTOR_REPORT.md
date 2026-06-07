# MoCode 0.3 重构报告

> 生成时间：2025-07  
> 分析维度：项目结构 · 代码复杂度 · 代码重复 · 命名与风格  
> 分析范围：`mocode/` 全部 89 个 `.py` 文件 + `tests/` 20 个测试文件

---

## 📊 总览

**MoCode 0.3 的架构分层清晰（core/app/tools 三层严格隔离），核心设计决策合理，但存在若干"热点文件"（builtin.py、session.py、search.py）承载过多职责，以及消息内容提取、Agent 构造等逻辑在 5 处重复实现——整体健康度为 B+，通过针对性重构可提升至 A。**

### 量化摘要

| 维度 | 评分 | 关键数字 |
|------|------|----------|
| 分层清晰度 | ⭐⭐⭐⭐⭐ | core 零 app 依赖，严格执行 |
| 模块内聚性 | ⭐⭐⭐⭐ | 3 个文件职责混合 |
| 文件大小均衡 | ⭐⭐⭐ | 15 文件超 300 行，4 个高风险 |
| 代码重复 | ⭐⭐⭐ | 5 处 `extract_text` 重复，~150 行可消除 |
| 命名与风格 | ⭐⭐⭐⭐ | 整体优秀，12 处公开 API 缺返回类型注解 |
| Docstring 一致性 | ⭐⭐⭐ | 混用 4 种风格 |

---

## 🔴 高优先级（必须重构）

### R-01 · 消除消息内容提取的 5 处重复

**问题描述：** `content` 字段可能是 `str` 或 `list[dict]`（OpenAI 多模态格式），5 个文件各自实现了几乎相同的 `isinstance` → 遍历 → 判断 type 逻辑，共约 40 行重复代码。修改一处容易遗漏其他位置。

**涉及文件：**
- `mocode/app/session.py` — `_text_content()`
- `mocode/tools/compact.py` — `format_messages_for_summary()` + `build_fallback_summary()`
- `mocode/app/cli/display.py` — `render_messages()`
- `mocode/providers/openai.py` — `strip_image_content()`

**建议方案：**
```python
# mocode/tools/utils.py（或 mocode/core/message_utils.py）
def extract_text(content: Any, *, image_placeholder: str = "[image]") -> str:
    """从 str 或 list-of-parts content 中提取纯文本。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for p in content:
            if isinstance(p, dict):
                if p.get("type") == "text":
                    parts.append(p.get("text", ""))
                elif p.get("type") == "image_url":
                    parts.append(image_placeholder)
            elif isinstance(p, str):
                parts.append(p)
        return "\n".join(parts)
    return str(content) if content else ""
```
所有 5 处替换为 `extract_text(msg.get("content", ""))`。

**预估工作量：** 1-2 小时  
**预期收益：** 消除 ~40 行重复，统一 image 占位符行为，降低维护成本

---

### R-02 · 拆分 `app/cli/commands/builtin.py`（509 行）

**问题描述：** 单文件包含 `_connect`、`_connect_edit`（93 行 / 嵌套 8 层）、`_connect_extra_body`、`_model`、`_session` 等十余个命令处理器，是最难定位和修改的文件。

**涉及文件：** `mocode/app/cli/commands/builtin.py`

**建议方案：** 按功能域拆分为子模块：

```
mocode/app/cli/commands/
├── __init__.py
├── builtin.py          # 保留为入口，re-export 各子模块
├── connect.py          # /connect + /connect:edit + /connect:extra_body
├── model.py            # /model
├── session.py          # /session (list, show, rm, clear, export)
├── provider.py         # /provider (list, info)
└── misc.py             # /help, /quit, /clear, /compact 等短命令
```

其中 `_connect_edit()` 应改为策略模式：

```python
EDITORS = {
    "name": _edit_name,
    "apikey": _edit_apikey,
    "baseurl": _edit_baseurl,
    "models": _edit_models,
    "extra_body": _edit_extra_body,
    "delete": _delete_provider,
}
handler = EDITORS.get(chosen)
if handler:
    dirty = await handler(ctx, entry) or dirty
```

**预估工作量：** 3-4 小时  
**预期收益：** 消除 8 层嵌套，每个命令文件控制在 100 行以内，可独立定位和测试

---

### R-03 · 拆分 `session.py` 中的 `_render_session_md()`（150 行 / 嵌套 8 层）

**问题描述：** `_render_session_md()` 是典型的 "God Function"——在单个函数中完成 YAML 头部、标题、概览、系统提示、用户消息、助手消息、工具调用、工具结果的全部渲染，使用 `while i < len(msgs)` 状态机，极难理解和修改。

**涉及文件：** `mocode/app/session.py`

**建议方案：** 拆为多个子函数 + 将状态机改为按 role 分组：

```python
def _render_frontmatter(session) -> list[str]: ...
def _render_overview(session) -> list[str]: ...
def _render_turn(turn_msgs, turn_num) -> list[str]: ...
def _render_tool_call(tc) -> list[str]: ...
def _render_tool_result(tc_id, content, msgs) -> list[str]: ...
```

同时将 `_render_session_md` 中的 `while` 循环改为 `itertools.groupby` 或提取为 `walk_messages()` visitor，复用于 `display.render_messages()`。

**预估工作量：** 2-3 小时  
**预期收益：** 嵌套从 8 层降至 2-3 层，每个子函数可独立测试，消除与 `display.py` 的遍历逻辑重复

---

### R-04 · 为 12 处公开 API 补齐返回类型注解

**问题描述：** 多个公开方法缺少返回类型注解，影响 IDE 自动补全、类型检查和文档可读性。

**涉及文件与位置：**

| 文件 | 方法 | 建议添加 |
|------|------|----------|
| `app/cli/app.py:339` | `run()` | `-> None` |
| `app/cli/app.py:346` | `run_oneshot()` | `-> None` |
| `app/cli/app.py:257` | `switch_provider()` | `-> None` |
| `app/cli/display.py:145` | `spinner()` | `-> AsyncContextManager[None]` |
| `app/cli/display.py:150` | `print()` | `-> None` |
| `app/cli/display.py:256` | `clear_screen()` | `-> None` |
| `tools/bash.py:137` | `restart()` | `-> None` |
| `core/skill.py:59` | `load_content()` | `-> str` |
| `core/tool.py:125` | `derived()` | `-> ToolRegistry` |
| `app/workflow/models.py:322` | `total_nodes` | `-> int` |
| `app/workflow/graph.py:171` | `collect_ancestors()` | `-> set[str]` |
| `main.py:28` | `read_stdin_if_piped()` | `-> str \| None` |

**建议方案：** 逐文件添加，可配合 `mypy --strict` 验证。

**预估工作量：** 1 小时  
**预期收益：** 消除类型盲区，提升 IDE 体验，为后续 `mypy` CI 铺路

---

### R-05 · 修复参数遮蔽 Python 内置函数

**问题描述：** 4 处参数名遮蔽了 `id()` 或 `format()` 内置函数，可能在函数体内误用导致难以排查的 bug。

**涉及位置：**
- `app/cli/spinner.py:242` — `id: str` → `seg_id: str`
- `app/cli/display.py:135` — `id: str` → `seg_id: str`
- `app/cli/display.py:141` — `id: str` → `seg_id: str`
- `core/builder.py:62` — `format: str` → `fmt: str`

**建议方案：** 批量重命名参数，全局搜索确认无遗漏。

**预估工作量：** 30 分钟  
**预期收益：** 消除潜在命名冲突风险

---

## 🟡 中优先级（建议重构）

### R-06 · 拆分 `tools/search.py`（478 行）

**问题描述：** `GlobTool` 和 `GrepTool` 两个完全独立的工具合在一个文件。`_grep_vfs()` 和 `_grep_real_fs()` 结构几乎相同，且各自有 6-7 个参数。

**建议方案：**
1. 拆为 `tools/glob.py` + `tools/grep.py`
2. 合并两个 grep 实现为一个，让调用方传入不同的文件迭代器：
```python
def _grep(pattern, file_iter, *, output_mode, max_results, context_lines): ...
```

**预估工作量：** 2 小时  
**预期收益：** 消除 grep 重复实现（~60 行），每个工具文件 < 250 行

---

### R-07 · 将 `SubAgent` 引擎从 `tools/` 提升到 `core/`

**问题描述：** `tools/subagent.py` 同时包含子代理引擎（`SubAgent`、`SubAgentConfig`、`SubAgentResult`）和工具包装（`SubAgentTool`）。子代理是核心概念，不应仅放在 tools 层。

**建议方案：**
- 将 `SubAgent`、`SubAgentConfig`、`SubAgentResult` 移至 `core/subagent.py`
- `tools/subagent.py` 仅保留 `SubAgentTool` 工具包装，import 自 `core`

**预估工作量：** 1-2 小时  
**预期收益：** 核心概念归位到 core 层，消除 tools 对核心引擎的"反向拥有"

---

### R-08 · 提取 `AgentLoop` 构造的公共模式

**问题描述：** 三处（`app.py`、`subagent.py`、`executor.py`）各自组装 `AgentConfig` + `AgentLoop`，字段映射逻辑重复约 30 行。

**建议方案：**
```python
# core/builder.py 或 core/agent.py
@classmethod
def from_agent(cls, parent: "AgentLoop", **overrides) -> "AgentLoop":
    """基于 parent agent 的配置创建新 AgentLoop。"""
```

或在 `Agent` builder 上增加 `Agent.from_agent(parent)` 方法。

**预估工作量：** 1-2 小时  
**预期收益：** 消除 ~30 行重复构造逻辑，统一 Agent 配置传递

---

### R-09 · 提取 `TimingHook` 基类

**问题描述：** `CLIDisplayHook` 和 `_WorkflowNodeHook` 有几乎相同的 `ToolTimingTracker` 使用模式（init/reset/start/complete/elapsed），约 20 行重复。

**建议方案：**
```python
# core/hook.py
class TimingHook(AgentHook):
    """自动追踪 tool 执行时间的基类 Hook。"""
    def __init__(self):
        self._tracker = ToolTimingTracker()

    async def on_response(self, ctx): self._tracker.reset()
    async def on_tool_start(self, ctx): self._tracker.start(ctx.tool_call_id)
    async def on_tool_complete(self, ctx): self._tracker.complete(...)
```

**预估工作量：** 1 小时  
**预期收益：** 消除 ~20 行重复，新 Hook 可直接继承获得计时能力

---

### R-10 · 统一 Docstring 风格为 Google Style

**问题描述：** 代码库混用 4 种 docstring 风格（Google、NumPy、RST、纯描述），影响一致性。

**建议方案：** 全部统一为 Google Style：
```python
def func(arg1: str, arg2: int) -> bool:
    """简短描述。

    Args:
        arg1: 参数1说明。
        arg2: 参数2说明。

    Returns:
        返回值说明。

    Raises:
        ValueError: 异常说明。
    """
```

**涉及文件：** `core/skill.py`、`app/cli/display.py`、`app/cli/textutils.py`、`app/workflow/models.py` 等约 10 处。

**预估工作量：** 2-3 小时  
**预期收益：** 文档风格统一，可接入 `pydocstyle` CI 检查

---

### R-11 · 改善模糊变量名

**问题描述：** 若干变量名过于简短，缺乏可读性。

| 位置 | 当前 | 建议 |
|------|------|------|
| `app/cli/hook.py:18` | `self._d` | `self._display` |
| `app/cli/display.py:91` | `for s in summaries` | `for summary in summaries` |
| `app/cli/theme.py:78` | `def _s(text, *codes)` | `def _styled(text, *codes)` |
| `tools/file.py:68` | `def _read_text(p, ...)` | `def _read_text(path, ...)` |
| `app/cli/app.py:131` | `for t in [...]` | `for tool in [...]` |

**预估工作量：** 30 分钟  
**预期收益：** 提升代码可读性，降低新人上手成本

---

### R-12 · 合并 `_emit()` 重复方法

**问题描述：** `DAGRunner._emit()` 和 `Executor._emit()` 方法体完全相同（3 行）。

**建议方案：** 将 `_emit` 提取到 `EventEmitter` mixin，或让 `Executor` 不存储 `_on_event`，改由 `DAGRunner` 统一代理。

**预估工作量：** 30 分钟  
**预期收益：** 消除重复，单一事件派发入口

---

### R-13 · 复用 `require_file()` 路径验证 + 提取 `count_lines()`

**问题描述：** `file.py` 的 `_write()` 和 `_append()` 手动做了与 `utils.require_file()` 相同的 `is_dir()` 检查；行数计算 `content.count("\n") + ...` 也重复了 2 次。

**建议方案：**
- `_write` / `_append` 直接调用 `require_file()`
- 提取 `count_lines(content: str) -> int` 到 `tools/utils.py`

**预估工作量：** 30 分钟  
**预期收益：** 消除 ~6 行重复，路径验证逻辑单一来源

---

## 🟢 低优先级（可选优化）

### R-14 · 合并零散小文件

| 文件 | 行数 | 建议 |
|------|------|------|
| `app/utils.py` | 35 | 并入 `app/__init__.py` 或 `app/config.py` |
| `app/cli/textutils.py` | 65 | 并入 `display.py` |
| `tools/skill.py` | 37 | 并入 `tools/__init__.py` |

**预估工作量：** 30 分钟  
**预期收益：** 减少文件数量，降低目录浏览噪音

---

### R-15 · 考虑 `auto_from_dict()` 辅助函数

**问题描述：** 4+ 个 dataclass 手写了结构相同的 `from_dict()` / `to_dict()`，这是项目有意的设计选择（避免 Pydantic），但代码量较大。

**建议方案：** 在 `app/utils.py` 中提供轻量辅助：

```python
def dataclass_from_dict(cls, data: dict):
    """按字段名和默认值从 dict 构造 dataclass。"""
    fields = {f.name: f.default for f in fields(cls)}
    return cls(**{k: data.get(k, fields[k]) for k in fields if k in data or fields[k] is not MISSING})
```

**优先级：低** — 改动范围大，收益中等，需评估对现有序列化逻辑的兼容性。

---

### R-16 · 决定 `_normalize` / `_strip_prefix` 的可见性

**问题描述：** `core/virtualfs.py` 中的 `_normalize()` 和 `_strip_prefix()` 标记为模块私有，但被 `tools/search.py` 跨模块导入。

**建议方案：** 去掉 `_` 前缀使其成为正式公共 API，或在 `search.py` 中自行实现。

---

### R-17 · 决定 `_WorkflowNodeHook` 的可见性

**问题描述：** `app/workflow/hooks.py` 中的 `_WorkflowNodeHook` 使用 `_` 前缀，但被 `runner.py` 和 `executor.py` 以 `# noqa: F401` 重新导出。

**建议方案：** 如果是公共 API，去掉 `_` 前缀；如果仅内部使用，改为直接 import 而非 re-export。

---

### R-18 · 提取 `walk_messages()` visitor

**问题描述：** 3 处（`session.py`、`display.py`、`compact.py`）以几乎相同的 while-loop 按 role 分支遍历消息，特别是 tool_call 与 tool 结果的关联逻辑重复约 60 行。

**建议方案：** 提取为 callback/visitor 模式：

```python
def walk_messages(messages, *, on_user=None, on_assistant=None, on_tool=None):
    """按 role 分组遍历消息，自动关联 tool 结果与 tool_call。"""
```

**预估工作量：** 2-3 小时（需仔细处理三处的差异）  
**预期收益：** 消除 ~60 行重复，统一消息遍历语义

---

### R-19 · 为 Hook 方法补齐参数类型注解

**涉及位置：**
- `app/cli/hook.py` — 所有 hook 方法的 `ctx` 参数缺少 `AgentHookContext` 类型
- `app/workflow/hooks.py` — 同上

**预估工作量：** 30 分钟  
**预期收益：** 提升类型安全

---

### R-20 · 提取 `parse_bool()` 工具函数

**问题描述：** LLM 传入 boolean 参数时可能返回 string `"true"` 而非 `True`，当前在 `search.py` 中手动转换。

**建议方案：**
```python
def parse_bool(val: Any, default: bool = False) -> bool:
    if isinstance(val, str):
        return val.lower() in ("true", "1", "yes")
    return bool(val) if val is not None else default
```

**预估工作量：** 15 分钟  
**预期收益：** 为未来更多 boolean 参数工具提供统一处理

---

## 📋 重构路线图

### 阶段一：消除重复，建立基础设施（1-2 天）

**目标：** 消除最广泛的代码重复，建立可复用的工具函数层。

| 任务 | 编号 | 工作量 | 依赖 |
|------|------|--------|------|
| 创建 `extract_text()` 并替换 5 处重复 | R-01 | 1-2h | 无 |
| 创建 `count_lines()` + 复用 `require_file()` | R-13 | 30min | 无 |
| 创建 `parse_bool()` | R-20 | 15min | 无 |
| 提取 `TimingHook` 基类 | R-09 | 1h | 无 |
| 合并 `_emit()` 重复方法 | R-12 | 30min | 无 |
| 修复 `id`/`format` 参数遮蔽 | R-05 | 30min | 无 |
| 改善模糊变量名 | R-11 | 30min | 无 |

**预期收益：** 消除 ~130 行重复代码，建立 `tools/utils.py` 工具函数层，消除命名隐患。

---

### 阶段二：拆分热点文件（2-3 天）

**目标：** 将超大文件拆分为职责单一的小文件，降低单文件复杂度。

| 任务 | 编号 | 工作量 | 依赖 |
|------|------|--------|------|
| 拆分 `builtin.py` → `connect.py` + `model.py` + `session.py` + `misc.py` | R-02 | 3-4h | 无 |
| `_connect_edit()` 改为策略模式 | R-02 | (含上) | 无 |
| 拆分 `_render_session_md()` 为子函数 | R-03 | 2-3h | R-01（需先有 `extract_text`） |
| 拆分 `search.py` → `glob.py` + `grep.py` | R-06 | 2h | 无 |
| 合并 `_grep_vfs` / `_grep_real_fs` | R-06 | (含上) | 无 |

**预期收益：** 消除全部 4 个高风险超大文件，最大文件从 509 行降至 ~150 行，嵌套深度从 8 层降至 3 层。

---

### 阶段三：架构优化（1-2 天）

**目标：** 修正架构边界，提升层间一致性。

| 任务 | 编号 | 工作量 | 依赖 |
|------|------|--------|------|
| 将 `SubAgent` 引擎移至 `core/` | R-07 | 1-2h | 无 |
| 提取 `Agent.from_agent()` 工厂方法 | R-08 | 1-2h | 无 |
| 提取 `walk_messages()` visitor | R-18 | 2-3h | R-01 |
| 决定 `_normalize`/`_strip_prefix` 可见性 | R-16 | 15min | 无 |
| 决定 `_WorkflowNodeHook` 可见性 | R-17 | 15min | 无 |

**预期收益：** 核心概念归位，Agent 构造逻辑单一来源，消息遍历统一。

---

### 阶段四：文档与类型完善（1 天）

**目标：** 补齐类型注解和文档，为 CI 质量门禁铺路。

| 任务 | 编号 | 工作量 | 依赖 |
|------|------|--------|------|
| 为 12 处公开 API 补齐返回类型注解 | R-04 | 1h | 无 |
| 为 Hook 方法补齐参数类型注解 | R-19 | 30min | 无 |
| 统一 Docstring 为 Google Style | R-10 | 2-3h | R-02, R-03（避免冲突） |
| 合并零散小文件 | R-14 | 30min | 无 |

**预期收益：** 全部公开 API 有类型注解和文档，可接入 `mypy --strict` + `pydocstyle` CI。

---

### 阶段五：可选深度优化（按需）

| 任务 | 编号 | 工作量 | 备注 |
|------|------|--------|------|
| 评估 `auto_from_dict()` 辅助函数 | R-15 | 2-3h | 需评估兼容性 |
| HTTP 工具 error decorator | — | 1h | 等有第三个 HTTP 工具时再做 |
| 添加 `mypy --strict` CI | — | 1h | 阶段四完成后 |

---

## 附录：重构影响矩阵

```
                    修改频率（高→低）
                    ┌─────────────────────────────────┐
                    │                                   │
         高 │   R-01 extract_text      R-02 builtin.py  │
            │   R-03 session.md        R-06 search.py   │
    复      │                                           │
    杂      ├───────────────────────────────────────────┤
    度      │                                           │
    （高→低）│   R-08 Agent.factory    R-09 TimingHook   │
            │   R-10 Docstring        R-04 Type hints   │
         低 │   R-05 id/format        R-11 Vars         │
            │                                           │
                    └─────────────────────────────────┘

    优先处理右上角（高复杂度 × 高修改频率）的 R-01/R-02/R-03/R-06
```

---

*本报告基于静态分析生成，建议团队评审后按路线图分阶段执行。每阶段完成后运行 `uv run pytest` 确认无回归。*
