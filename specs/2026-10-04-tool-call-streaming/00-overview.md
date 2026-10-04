# Spec Group · 工具调用的流式三相位（2026-10-04）

> Group: `specs/2026-10-04-tool-call-streaming/`
> 来源：用户要求"从底层重构，让内核更干净清晰，不要打补丁式代码，不需要向后兼容"
> ——具体诉求：后端/嵌入方要能实时预览工具调用（write、edit 等）的状态，且 CLI 终端
> 同步受益。用户已逐条拍板本组全部决策（D1-D8，见下；含两轮澄清：provider DTO 与
> 新事件的并存关系、行业三相位对照）。
> 探索结论由 lead 三个 Explore agent 全仓核实（file:line 证据已汇入本组工单，行号为
> 基线锚会漂移）；worker 不改 spec、不写状态；完成信号 = 最终报告 + 分支 commit。
> git 是唯一进度权威。git 协议沿用本 SKILL.md 约定，本组不重复。

---

## 1. 目标

**最高约束（放第一句）：模型在流式生成工具调用参数期间，事件流必须像 `TextDelta` 一样
逐碎片可见，且每个碎片携带与后续 `ToolCallStarted` / `ToolCallFinished` 完全一致的
`call_id`；为此 tool call 身份的铸造从 dispatch 时机前移到流式时机（dispatcher 仍是
唯一铸造处），CLI 工具行从"执行时占位"提前为"模型点名工具即占位"，三个相位共用一行
原位更新。**

根因定性：缺口不在执行期（`ToolOutput` / `PluginMessage` 机制齐全），而在
`mocode/core/agent.py` 的 chunk 循环只翻译了 `Chunk.text` / `Chunk.reasoning` 两块，
`Chunk.tool_calls` 一直只喂 `StreamAccumulator`、零事件——provider docstring 写明
"the loop turns a chunk stream into the kernel's event stream as it arrives"，
这行翻译缺了第三块。同时身份在 `dispatch.py:_assign_call_id` 二次铸造（流式期拿不到），
是同一个根因的两个面。本组补翻译边、前移身份铸造、折叠 forming 态、CLI 三相位，
四件事一次做完，不留兼容垫片。

## 2. 已拍板决策（不再反复）

| # | 决策 | 依据 |
|---|---|---|
| D1 | 新事件 `ToolCallArgsDelta(call_id, name, arguments)`，`type="tool_call_args_delta"`，`core/events.py` Tool calls 区首位。`index` 不进事件——线上合组细节，事件靠 `seq` 排序 | 用户拍板；对齐 AG-UI/AI SDK 的 ARGS 相位 |
| D2 | 与 provider DTO `ToolCallDelta` **并存**，不合并不改名：`provider.py` 是底层词汇表（`events.py` 现正 `from .provider import Usage`，反向引用成环，物理不可合并）；事件是输出契约第三块（text/reasoning 已有前两块） | 用户追问"会不会冗余"后确认 |
| D3 | 身份前移：dispatcher 新 public `mint_model_call_id(provider_id)`（恒自增 `_call_seq`，provider id 优先，保持"一条序列"现有口径）；loop 槽位首现时调用，`acc.build()` 后按 index 覆写 `call.id`；`run()` 的 model-origin 空 `call_id` → `ValueError`（契约收紧，无兜底）；`_assign_call_id` 收缩为 program-origin 专用 | 用户拍板；唯一调用方 loop 保证非空，codemode 两处均为 program origin（已核实）不受影响 |
| D4 | `RunState`：fold-only 新状态 `TOOL_FORMING`；`ToolCallState.args_text`（fold-only，**不进** `to_dict`——session/export 不落盘 state，零持久化影响，已核实）；`done` 排除 forming；`ToolCallStarted` case 改 update-or-create | forming（模型写参数中）与 running（执行中）是后端两个渲染态 |
| D5 | CLI 三相位一行：首个**带 name** 的碎片 `place(L.tool_pending(name, {}, tools))`（现成建材，`lines.py:167-174`，name-only 形状已被 `test_lines.py:96-97` 钉住）；无 name 碎片不画（部分 JSON 无可摘要信息）；`ToolCallStarted` 原位 `rewrite` 换完整参数；`ToolCallFinished` 结论不变 | 用户要求"CLI 也对应优化，状态更新使用块，简洁清晰"；零新 builder |
| D6 | 文档五处：`docs/embedding.md` 事件表+ToolCallState 叙述、`docs/ARCHITECTURE.md` 计数 12→13+生命周期图、`docs/api.md` 分组事件行+ToolCallState 字段行；`docs/plugins.md` 不动（无新 hook 点） | AGENTS.md "Adding an event class" 四件套是执行清单 |
| D7 | 测试**原地改写**，不新增测试文件；用例数允许结构性变化，报告逐条对账 | 用户"非必要不加冗余测试"惯例；本组无 D5 式删减清单，对账即前后差集 |
| D8 | 口径：代码/注释/docstring 全英文；spec 组叙事中文；commit 小写祈使句 conventional（`feat:`/`fix:`/`test:`/`docs:`），标题一行 + 正文动机 | 仓库惯例 |

## 3. 波次表（单波单 agent）

| 波 | 分支 | 工单 | 内容 | 前置 | 写范围 |
|---|---|---|---|---|---|
| W1 | `feat/tool-call-streaming` | `01-w1-tool-call-streaming.md` | 事件+导出 → 身份前移 → loop 发射 → state 折叠 → CLI 三相位 → 文档 → 测试同步（T1-T6，每 T 一 commit，每 commit 独立绿） | 无 | `mocode/core/{events,agent,dispatch,state}.py`、`mocode/core/__init__.py`、`mocode/plugins/__init__.py`、`mocode/cli/render.py`、`docs/{embedding,ARCHITECTURE,api}.md`、`tests/test_{events,agent_loop,dispatch,display}.py` |

写范围强串行（同一批文件被 T1-T6 依次触碰），不并行、不分波。

## 4. 全局不变量（工单引用，违反即打回）

1. `mocode/core/provider.py`、`mocode/providers/openai.py`、`mocode/testing/`、`docs/plugins.md`、`pyproject.toml`/`uv.lock` **零 diff**。`mocode/cli/lines.py` 零 diff（三相位复用其现有 builder）。
2. 无兼容垫片：model-origin synthesize 删除即契约变更，测试原地改；发现因此失去消费者的 shim 顺带拆。
3. 全量 `uv run pytest -q` 绿，退出码独立确认（`; echo $?`，禁管道吞）；时间纪律 autouse 守卫自动生效——禁裸 sleep。
4. 写入范围外零 diff；遇阻塞报告后停止，不越界自救。
5. 事件四件套（events.py 子类 → `core/__init__` + `plugins/__init__` 双导出 → embedding.md 表 → ARCHITECTURE.md 计数+图）四步全做，双向核对（加了不导=plugin 作者不可见；导了不文档=不可用）。
6. worker 不改 spec、不 merge/push/tag、不碰主检出与其它 worktree。

## 5. 验收（组级）

1. 全量 pytest 绿（基线 **505 passed / exit 0**，2026-10-04 master @ `a43cf7e`），worker 报告 pre/post 用例数对账。
2. 行为抽验（写进测试，报告附真实输出）：脚本 response 带 tool_calls → 事件序列含 `tool_call_args_delta`，碎片按序拼接 == 完整参数 JSON、首片带 name、`call_id` 在 delta/started/finished 三段一致、`ToolCallStarted` 不早于最后一个 delta；空 id 铸造 `call_1`/`call_2`；CLI 一行三态（一次 place + 两次原位 rewrite 的 ANSI 序列）+ 参数流中文本到达的冻结降级。
3. `grep -rn "ToolCallArgsDelta" mocode/ docs/` 落点 = 工单 T1/T6 清单（含 `mocode/core/__init__.py`、`mocode/plugins/__init__.py` 两处导出）。
4. `grep -rn "synthes\|made up" mocode/core/dispatch.py` 无 model-origin 合成残留。

## 6. 环境

Windows 11 + Git Bash；uv + Python 3.13；路径含非 ASCII 目录名（桌面）；无浏览器；
pytest 每例 30s 超时 autouse；`tests/test_display.py` 用 capsys + `strip_ansi`，
无真实 TTY 时 `place()` 返回 None 的路径需显式构造（照抄其既有 fixture 手法）。
