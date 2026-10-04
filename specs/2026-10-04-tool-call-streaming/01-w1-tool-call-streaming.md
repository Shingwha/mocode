# Spec 三相位 · 工具调用的流式事件、身份与渲染（波次 W1）

✅ 2026-10-04 完成 @ `feat/tool-call-streaming`（五个 commit `bffdbd7`→`faea778`，逐个全量绿；T3+T5 合并为 commit `732e62b`——原因见 deviation：显示测试驱动 loop，拆开任何一半都红）

> 分支 `feat/tool-call-streaming`（worktree 为界，只此一个 agent）；
> 前置：无；深读：`00-overview.md`（本组，D1-D8 与不变量以它为准）。
> 行号为基线锚（master @ `a43cf7e`），会漂移，按语义定位。

## 目标

一段话：**让模型流式生成工具调用参数的过程在事件流里逐碎片可见（新事件
`ToolCallArgsDelta`，身份与 `ToolCallStarted`/`ToolCallFinished` 一致），tool call
身份的铸造从 dispatcher 事后合成前移到 loop 流式期（dispatcher 仍为唯一铸造处），
`RunState` 折叠 forming 态，CLI 工具行提前到"模型点名工具即占位"、三个相位一行原位
更新。** 最高约束：`mocode/core/provider.py` 等红线文件零 diff；无兼容垫片；每个
commit 独立过回归。

## 工单（按序执行，每项一个 commit，每 commit 前受测文件绿）

### T1 · `core/events.py` + 双导出：新事件
- 文件：`mocode/core/events.py`。在 `# ---- Tool calls ----` 区**首位**（`ToolCallStarted` 之前，约 L195）插入：
  ```python
  @dataclass
  class ToolCallArgsDelta(Event):
      """A fragment of a tool call the model is streaming.

      The model produces a tool call the way it produces text: in pieces. The
      first fragment of a call carries its ``name``; every fragment carries the
      ``call_id`` — the identity this call's ``ToolCallStarted`` and
      ``ToolCallFinished`` also carry, so a consumer folds the fragments with
      the call they belong to. Concatenate the ``arguments`` of one ``call_id``
      to get the arguments exactly as the model wrote them. There is no
      separate start event: the first fragment that names a tool is where a
      consumer opens its row.
      """

      call_id: str = ""
      name: str = ""
      arguments: str = ""
      type: ClassVar[str] = "tool_call_args_delta"
  ```
- `__all__`（约 L307-328）按字母序插 `"ToolCallArgsDelta"`（在 `"ToolCallFinished"` 前）。
- 导出两处（AGENTS.md 事件四件套，缺一即打回）：
  - `mocode/core/__init__.py`：events import 块（约 L41-56）按字母序加 `ToolCallArgsDelta`；`__all__`（约 L148-160）同步。
  - `mocode/plugins/__init__.py`：events import 块（约 L72-100）加名；`__all__`（约 L196-202）同步。
- 测试：`tests/test_events.py` `ALL_EVENTS`（L15-27）加 `ev.ToolCallArgsDelta(call_id="c1", name="write", arguments='{"pa')`——唯一性（L31-34）、`to_dict`（L36-44）、summary（L62-67）三条自动覆盖，无需新用例。
- commit：`feat(core): emit a delta for each streamed tool-call fragment`（正文：翻译边第三块）。

### T2 · `core/dispatch.py`：身份铸造前移，model-origin 收紧契约
- 新 public 方法（放在 `run` 之前、`_assign_call_id` 之后）：
  ```python
  def mint_model_call_id(self, provider_id: str) -> str:
      """Identity for a model-side call, minted while the response streams.

      The provider's own id when the endpoint sent one, a kernel-minted
      ``call_<n>`` otherwise. This is the only place a tool call's identity is
      minted: the loop asks for it as a call's first fragment arrives, so the
      deltas it publishes, the ``ToolCallStarted`` the finished response
      triggers and the tool message in the history all share one id. The
      counter is shared with program-origin ids, so the two forms never
      collide.
      """
      self._call_seq += 1
      return provider_id or f"call_{self._call_seq}"
  ```
- `_assign_call_id`（约 L292-312）收缩为 program-origin 专用，签名与 docstring 重写：
  ```python
  def _assign_call_id(self, parent_call_id: str | None) -> str:
      """Identity for a program-origin call: nested under its parent —
      numbered per parent, so siblings read as a series a UI can fold — or a
      standalone ``pcall_<n>``. Model-origin identity is minted by the loop
      while the response streams (:meth:`mint_model_call_id`), never here.
      """
      self._call_seq += 1
      if parent_call_id:
          n = self._nested_seq.get(parent_call_id, 0) + 1
          self._nested_seq[parent_call_id] = n
          return f"{parent_call_id}:{n}"
      return f"pcall_{self._call_seq}"
  ```
- `run()` 的铸造点（约 L145）改为：
  ```python
  if origin == "model":
      if not call_id:
          raise ValueError(
              "a model-origin call arrives with the identity the loop minted "
              "while the response streamed — pass it as call_id"
          )
      cid = call_id
  else:
      cid = self._assign_call_id(parent_call_id)
  ```
- 模块 docstring：program-origin contract 段补一句——model-origin 身份由 loop 经
  `mint_model_call_id` 铸造后随调用传入；`_assign_call_id` 只服务 program origin。
- 测试（`tests/test_dispatch.py`，原地改写）：
  - `TestCallIdentity.test_model_calls_keep_the_provider_id_or_get_one_made_up`（L235-242）改写为：`run("echo", {}, call_id="from-provider")` 保持 id；`run("echo", {})`（model-origin 空 id）`pytest.raises(ValueError)`。
  - 新增 `test_the_loop_mints_model_side_ids_at_stream_time`：`dispatcher.mint_model_call_id("")` == `"call_1"`；`mint_model_call_id("prov-9")` == `"prov-9"`；再一次 `mint_model_call_id("")` == `"call_3"`（恒自增，provider id 也占位，保持"一条序列"）。
  - `test_program_calls_are_numbered_per_parent_or_get_a_pcall_id`（L221-233）**不动**——不铸 model id，计数器语义不变。
- commit：`refactor(dispatch): mint model-side call ids where the stream arrives`（正文：契约收紧无兜底）。

### T3 · `core/agent.py`：chunk 循环发射 delta + build 后覆写 id
- 文件：`mocode/core/agent.py`。`from .events import (...)` 块加 `ToolCallArgsDelta`（按字母序）。
- `acc = StreamAccumulator()`（约 L467）旁加每迭代映射：
  ```python
  acc = StreamAccumulator()
  # index -> call_id, minted at a slot's first fragment: the identity the
  # events announce is the identity the dispatch below will use.
  call_ids: dict[int, str] = {}
  ```
- chunk 循环内（约 L478-487），在 reasoning/text 发布**之后**追加：
  ```python
  for delta in chunk.tool_calls:
      call_id = call_ids.setdefault(
          delta.index, self.dispatcher.mint_model_call_id(delta.id)
      )
      await self._publish(
          ToolCallArgsDelta(
              call_id=call_id, name=delta.name, arguments=delta.arguments
          )
      )
  ```
- `response = acc.build()`（约 L500）之后、`after_response` hook 之前，覆写身份：
  ```python
  # The fragments already announced each call's identity; the accumulator
  # only merged the wire. Force the ids the events carry onto the calls the
  # dispatcher is about to run, so one identity covers all three phases.
  for index, call in enumerate(response.tool_calls or []):
      call.id = call_ids[index]
  ```
- 测试（`tests/test_agent_loop.py`）：`TestEventStream.test_a_tool_turn_reports_its_events_and_summary`（L96-117）扩断言：`deltas = [e for e in events if isinstance(e, ToolCallArgsDelta)]`；首片 `name == "echo"`；碎片拼接 `== started[0].args` 的 JSON 串；`call_id` 与 `started`/`finished` 的 `["c1"]` 一致；`ToolCallStarted` 的 seq 大于最后一个 delta 的 seq。新增 `test_a_call_the_endpoint_never_named_still_has_one_identity`：`tool_call_response("echo", '{"value":"x"}', call_id="")` 起两个调用 → delta 带 `call_1`/`call_2`，与 started/finished 一致，`agent.state.tool_calls` 键即这两个 id。
- commit：`feat(core): publish a tool-call args delta per streamed fragment`（正文：与前移的铸造配套）。

### T4 · `core/state.py`：折叠 forming 态
- `TOOL_RUNNING`（约 L36-39）旁加 fold-only 常量的重写（两个都留，注释重写）：
  ```python
  #: Fold-only tool statuses: a call the model is still writing the arguments
  #: for, and one that is executing. Neither is an event status — the wire
  #: carries only the five terminal ``TOOL_*`` values defined in
  #: :mod:`mocode.core.events`.
  TOOL_FORMING = "forming"
  TOOL_RUNNING = "running"
  ```
- `ToolCallState`（约 L42-59）：加字段与注释：
  ```python
  #: The argument text as it streamed in, before the call was announced for
  #: execution. Fold-only live view: not part of ``to_dict`` — a call that is
  #: running (or finished) shows its parsed ``args`` instead, and a forming
  #: call cannot outlive the turn it is in.
  args_text: str = ""
  ```
- `done`（约 L66-68）：`return self.status not in (TOOL_RUNNING, TOOL_FORMING)`，docstring 同步。
- `apply`（约 L143-193）：`ToolCallStarted` case 之前插新 case；`ToolCallStarted` case 改为 update-or-create：
  ```python
  case ToolCallArgsDelta():
      call = self.tool_calls.get(event.call_id)
      if call is None:
          call = self.tool_calls.setdefault(
              event.call_id,
              ToolCallState(
                  call_id=event.call_id, name=event.name, status=TOOL_FORMING
              ),
          )
      if event.name:
          call.name = event.name
      call.args_text += event.arguments
  case ToolCallStarted():
      call = self.tool_calls.get(event.call_id)
      if call is None:
          call = ToolCallState(call_id=event.call_id, name=event.name)
          self.tool_calls[event.call_id] = call
      else:
          call.name = event.name or call.name
      call.status = TOOL_RUNNING
      call.args = dict(event.args)
  ```
  `import` 块加 `ToolCallArgsDelta`（按字母序）。`to_dict` **不动**（args_text 是 fold-only，D4）。
- 测试（`tests/test_events.py`）：`test_a_tool_call_runs_from_started_to_finished`（L115-144）扩：两个 delta（其一不带 name）→ `state.tool_calls["c1"].args_text == "..."`（拼接）、`.status == "forming"`、`not done`；`ToolCallStarted` 后 `status == "running"`、`args` 落 dict。L121/L132 的 `not c.done` 查询对 forming 必须仍为真（done 语义已改，断言天然覆盖，勿删）。`test_to_dict_is_plain_data`（L193-204）不动——断言 to_dict 无 args_text。
- commit：`feat(core): fold the forming phase into RunState`（正文：forming/running 两态、args_text fold-only）。

### T5 · `cli/render.py`：三相位一行
- `from ..core.events import (...)` 按字母序加 `ToolCallArgsDelta`。
- `draw` 的 match：`case ToolCallStarted():` 之前加 `case ToolCallArgsDelta(): self._tool_forming(event)`。
- 新方法（放在 `_tool_start` 之前）：
  ```python
  def _tool_forming(self, event: ToolCallArgsDelta) -> None:
      """Open the call's row the moment the model names the tool.

      Arguments stream before the call runs, and the first fragment carries
      the name — so the row opens on the tool, and the name-only pending
      line stands there while the rest of the arguments arrive. Later
      fragments draw nothing: partial JSON has no summary to show, and a row
      rewritten per fragment is a row that flickers. ``ToolCallStarted``
      rewrites the row with the final arguments.
      """
      if event.call_id in self._rows or not event.name:
          return
      self._rows[event.call_id] = self._d.place(
          L.tool_pending(event.name, {}, self._conversation.tools)
      )
  ```
- `_tool_start`（约 L111-121）改写为原位路径（注意 `rewrite` 返回 `None`，行柄必须留在 `_rows`）：
  ```python
  def _tool_start(self, event: ToolCallStarted) -> None:
      """Give the call's row the final arguments, so its verdict has somewhere
      to land.

      A row the argument stream already opened is rewritten in place with the
      final arguments; a call no fragment announced — a program-origin call,
      a model that never named the tool — claims its row now. Nothing else
      may be drawn while a batch runs, or the row offsets this row is
      addressed by stop being true — the display freezes the block the moment
      anything else is printed.
      """
      row = self._rows.get(event.call_id)
      line = L.tool_pending(event.name, event.args, self._conversation.tools)
      if row is None:
          self._rows[event.call_id] = self._d.place(line)
      else:
          self._d.rewrite(row, line)
      self._running[event.call_id] = event.args
  ```
  模块 docstring（L1-13）"A tool call owns a row from the moment it starts" 改为
  "from the moment the model names it"的口径。`_tool_done`（L123-126）与 `_clear` 不动。
- 测试（`tests/test_display.py`，原地改写 + 两条新增）：
  - `TestRenderer` 与 `TestLiveBlock` 的行 Census（L200-229、L231-249、L366-384、L386-413、L415-423、L425-440）：按三相位后的行序列调整——forming 行与执行占行是同一行的一次 place，最终仍"一次 place + 两次 rewrite"，`·` 行计数不变（若某用例从 started 起画，改为从首个带 name 的 delta 起画即可，语义等价）。
  - 新增 `test_a_call_gets_its_row_when_the_model_names_it`：流 `ToolCallArgsDelta(name="write")` → `·write…` 行出现；再 `ToolCallStarted(args={"path": "a.py"})` → 同址 rewrite 为 `·write  a.py…`（断言 REWRITE 序列的 row 偏移复用既有 helper）；`ToolCallFinished` → `✓ write  a.py`。断言**恰好一次** place（只有一行 `·`）。
  - 新增 `test_text_after_a_forming_row_freezes_the_block`：forming 行后跟一个 `TextDelta` → 块冻结，`ToolCallFinished` 以追加行落地（`assert not REWRITE.search(out)`、`✓` 在，同型于 L386-413 的既有降级用例）。
- commit：`feat(cli): open a tool call's row when the model names it`（正文：三相位一行原位更新）。

### T6 · 文档四件套 + 公开表面
- `docs/embedding.md`：事件表（L126-127 区）在 `ReasoningDelta` 行后插
  `` | `ToolCallArgsDelta` | `call_id`, `name`, `arguments` | a fragment of a tool call the model is streaming — same `call_id` as its started/finished pair | ``；
  ToolCallState 叙述段（L203-210，"Each `ToolCallState` has ..."）补：`status` is
  `"forming"` while the model streams the arguments, `"running"` while the call
  executes; `args_text` holds the streamed argument text (fold-only, live view)。
- `docs/ARCHITECTURE.md`：L21 `the twelve events a run emits` → `the thirteen`；
  生命周期图（约 L279）`│   │   └─ TextDelta / ReasoningDelta as chunks arrive` →
  `│   │   └─ TextDelta / ReasoningDelta / ToolCallArgsDelta as chunks arrive`。
- `docs/api.md`：L55 分组行 `| `ToolCallStarted` / `ToolOutput` / `ToolCallFinished` | ... | a tool call's three moments, one identity |` 扩为四成员（`ToolCallArgsDelta` / `call_id`, `name`, `arguments` / 加在 Carries 首位，When 改 "a tool call's four moments, one identity"）；L141 `ToolCallState` 行 status 描述改 `"forming" while the model streams the arguments, `"running"` while it executes`。
- commit：`docs: the streamed-arguments phase across the event tables`（正文：四件套+api 求真）。

## 验收（完成前自测，报告给真实结论）

1. 每个 T 后受测文件绿；`uv run pytest -q; echo $?` 全绿，退出码独立确认，报告 pre/post 用例数（基线 505 passed / exit 0）与改写清单逐条对账。
2. 行为抽验（真实输出贴报告）：脚本 `call_tool("echo", {"value": "x"})` 回合事件序列含 `tool_call_args_delta`；碎片拼接 == `'{"value": "x"}'`；首片 name；`call_id` 三段一致；Started 的 seq > 最后 delta 的 seq；空 id 回合 id 为 `call_1`/`call_2`；CLI 一行三态 ANSI 序列（一次 place、两次 rewrite）+ 文本冻结降级。
3. `grep -rn "ToolCallArgsDelta" mocode/ docs/` == T1/T6 落点清单（core/events.py、core/state.py、core/agent.py、cli/render.py、core/__init__.py、plugins/__init__.py、docs/embedding.md、docs/ARCHITECTURE.md、docs/api.md + 测试文件）。
4. `git diff --stat master...HEAD` 写入范围 = 工单清单；`mocode/core/provider.py`、`mocode/providers/openai.py`、`mocode/testing/`、`docs/plugins.md`、`pyproject.toml`、`uv.lock`、`mocode/cli/lines.py` 零 diff（逐文件确认）。

## 禁触清单

- `mocode/core/provider.py`、`mocode/providers/openai.py`（provider 词汇表不动——DTO 与事件并存是 D2 的架构结论）
- `mocode/testing/`（MockProvider 的碎片化回放正是本组的测试地基）
- `docs/plugins.md`、`README.md`、`AGENTS.md`（无新 hook 点；AGENTS.md 的清单是本组执行依据不是修改对象）
- `mocode/cli/lines.py`（三相位复用其现有 builder，零 diff 是设计意图）
- `pyproject.toml`、`uv.lock`、`.zcodeignore`
- specs/ 本组之外的历史组；任何 spec 文件（lead 所有）

## 最终报告格式

工单状态表（T1-T6 各自 commit hash + message）/ commit 清单（hash + message + 一句话动了什么）/ 自测真实结论（四个验收命令的独立退出码与关键输出摘录）/ pre-post 用例数对账与改写清单 / 偏差与取舍（代码与工单草图的任何出入及理由）/ 未决问题（如有，附现场证据）
