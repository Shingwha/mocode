# Spec 05 · T3 内容渲染：Markdown 流式 + diff drawer 范例（波次 B）

> 分支 `spec/t3-content`；worktree `C:\Users\shifu\.worktrees\mocode\spec-t3-content`；
> 前置：波 A 全部合并（01 号：流式未完成行进实时区、部分行重写）；深读：总纲（裁决 #2/#5）+ `ref/cli-tui-api.md` §6 T3 行 + "Markdown 流式渲染单独说一句"。
> 只读本 worktree；绝不 merge/push/tag。**本波与 04/06 并行**。

## 现状事实（已核实）

- 01 号工单（本波前置）落地后：流式块的已完成行追加即提交，未完成行在实时区重写（多行回绕按可视行数）；围栏着色的判定点在"行完成时"（开启围栏之后的行可着色，开启行素色——总纲裁决 #2）。
- `lines.py` 的 `Line(text, icon, style, icon_style, note)` 是渲染词汇；`theme.py` 的 `Theme` 有 answer/reasoning/dim/muted 等样式名。
- `pyproject.toml`：`[dependency-groups]` 只有 `dev`；uv.lock 无 rich。
- DrawerRegistry（`plugin.py:56-96`，`override=True` 已支持）已可按 kind 注册 PluginMessage 渲染；transcript 的 PluginMessage 折叠与跟随块已在（T1）。

## 目标

流式回答具备轻量 Markdown 渲染（pi 同款克制路线：**流式期间只做围栏代码块检测与着色**，不重排、不解析全文）；块落定后可选完整渲染（rich，可选依赖）；给出 diff 视图作为自定义 drawer 的官方范例。

## 工单（每项一个 commit）

### T1 流式围栏着色
- 新模块 `mocode/cli/markdown.py`（纯函数、无终端依赖可测）：
  ```python
  class FenceTracker:
      """Feed completed lines; learn fence state; style lines inside a fence."""
      def feed(self, line: str) -> None          # 更新 ``` 围栏状态（含语言标记行）
      def in_fence(self) -> bool
  def style_code_line(line: str, theme: Theme) -> "Line"   # 围栏内行降权/着色（dim + 边框字符可选）
  ```
- 接入点：流式**已完成行**提交时（display.stream 路径），in_fence 则按样式出线；围栏开启行本身素色（总纲裁决 #2）；围栏闭合行可着边框样式。流式期间不做标题/列表/行内标记处理（文档明示的克制）。
- answer 与 reasoning 分开跟踪（各自 tracker）。

### T2 落定完整渲染（rich 可选）
- `[dependency-groups]` 新增 `tui = ["rich"]`；代码 `try: import rich` 守卫。
- 块 seal 时（`_seal_stream`/tool 无关，仅 answer/reasoning）：rich 可用 → 该块经 rich 渲染为 ANSI 后作为多行 Line 提交（代码块边框、标题弱化——rich Markdown 的默认即接近）；不可用 → 保持 T1 的素文本行（现状不回退）。
- **非 TTY 永不使用 rich**（总纲：非 TTY 不装不用——安装了也不用，保持纯追加文本）。流式期间也不用 rich（只有 seal 时一次性）。
- uv lock 更新；README 不动（可选组说明放 `docs/ARCHITECTURE.md`？——不，你禁触它；写进本模块 docstring 与 T3 的范例 README）。

### T3 diff drawer 范例
- 新范例 `examples/plugins/message-drawers/`：`plugin.json` + `mocode/plugin.py`（宿主侧：注册一个 `diff-demo` 工具，emit `PluginMessage(kind="diff-demo/patch", data={"diff": "<unified diff>"})`）+ `mocode.cli/plugin.py`（CLI 侧：`drawers.register("diff-demo/patch", draw_diff)`——unified diff 逐行 +/-/@@ 着色，`Theme` 的 success/error 样式）+ README（这就是 §5.2 drawer 的官方范例；rich 可选组说明）。
- 范例工具用 W2-A 后的 `Tool(schema=..., with_context=True)` 形态。

### T4 测试
- `tests/test_content.py`（新）：FenceTracker 状态机（开/闭/嵌套围栏不识/language 行/无围栏文本）；着色行断言；rich 缺席路径（monkeypatch import 失败）素文本回退；rich 存在路径（真渲染一次，断言含 ANSI 样式码）——rich 仅在有 tui extra 的环境下跑（`pytest.importorskip` 或跳过守卫，Windows CI 无 rich 也必须全绿）。
- `tests/test_display.py`（**本波归你**，01 已合并）：seal 后完整渲染块（rich 开/关两路径）的 golden。

## 验收

1. `uv run pytest -q` 全绿（基线 = 波 A 合并后数量），独立退出码。
2. `uv sync` 默认不装 rich；`uv sync --group tui` 后 rich 路径测试真实通过（两个环境各跑一次，报告真实输出）。
3. 范例插件被真实加载的测试证据。
4. `git diff <merge-base> --stat` ⊆ 写入范围。

## 禁触清单

- `mocode/cli/input.py`、`plugin.py`、`app.py`、`commands.py`、`dialogs.py`（04/07 领地）；`mocode/cli/transcript.py` 仅在 seal 物化点接 markdown 的最小改动（01 已定的结构不改）。
- `mocode/core/**`、`mocode/host/**`、`mocode/testing/**`、`providers/**`。
- `docs/plugins.md`（04 领地）、`docs/ARCHITECTURE.md`（06 领地）。
- tests：`tests/test_input.py`/`test_cli_plugin.py`（04）、`tests/test_session.py`/`test_conversations.py`（06）。
- spec 文件。

## 最终报告格式

工单状态表（T1-T4）｜commit 清单｜自测真实结论（pytest 双环境 + 范例加载证据）｜偏差与取舍｜未决问题。阻塞即停。
