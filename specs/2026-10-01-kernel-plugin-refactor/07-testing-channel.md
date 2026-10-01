# Spec 07 · K12 测试工具公共化 + K15 通道与状态边界（波次 W4-B）

> 分支 `spec/testing-channel`；worktree `C:\Users\shifu\.worktrees\mocode\spec-testing-channel`；
> 前置：W3 已合并；深读：总纲 + `ref/kernel-plugin-api.md` 的 K12、K15。
> 你只读本 worktree 内的文件；绝不操作主检出，绝不 merge/push/tag。
> **本波与 W4-A（spec/shell-bg）并行**：写入范围文件级不相交，见禁触清单。

## 现状事实

- `tests/providers.py`（120 行）：`MockProvider`（罐装 `Response` 回放、末条永远重复、tool-call JSON 参数按 8 字符分片、每次 stream 记录完整请求到 `self.calls`）、`SlowProvider`、`tool_call_response` 帮手、`response_to_chunks` 分块器——仓库私有，插件作者要用只能整段复制。
- `mocode/core/channel.py`：`REPLAY = 1000`、`BACKLOG = 1000` 模块常量（:38,:42）；`EventChannel(*, replay, backlog)` 构造参数已存在，host 从不传。
- `mocode/core/state.py`：`RunState.apply` 把 `TextDelta` 全量累进 `self.content`（:130-131）——长 turn 内存线性增长，无上限。
- 测试导入风格：各测试文件 `from tests.providers import MockProvider, ...`（tests 是包，有 `__init__.py`）。
- `docs/` 现有 4 个文档；无 testing 文档。

## 目标

发布 `mocode.testing` 公共测试工具包（K12），插件作者"对着剧本模型测插件"零复制；channel 参数文档化 + RunState content 软上限（K15）。

## 工单（每项一个 commit）

### T1 `mocode/testing/` 子包（K12）
- 新建 `mocode/testing/__init__.py` + `mocode/testing/providers.py`：迁入 `MockProvider`、`SlowProvider`、`tool_call_response`、`response_to_chunks`、`ARG_FRAGMENT` 等既有实现（**行为逐字保持**——末条重复语义、分片大小、`calls` 记录），并新增断言助手：
  - `async def collect(agen) -> list[Event]`（排空一个 async 迭代器）；
  - `def events_of_type(events, cls) -> list`；
  - `def terminal(events) -> Event`（取终止事件，非终止抛 AssertionError）；
  - 剧本构造器 `def say(text: str) -> Response` / `def call_tool(name, args: dict) -> Response`（薄封装 `tool_call_response` 与纯文本 Response）。
- `mocode/__init__.py` 的惰性导出机制不动（testing 不是热路径，eager 即可）；确认 `import mocode` 计时仍 <1ms。
- **迁移导入**：全部 `from tests.providers import ...` 改 `from mocode.testing import ...`（机械替换，含 conftest）；迁移完成后**删除 `tests/providers.py`**——若 `tests/test_builtin_plugins.py`（W4-A 领地）仍引用它，保留该文件为一行 re-export 并在报告注明，由 lead 在 W4-A 合并后清扫（诚实出口）。

### T2 `docs/testing.md`（K12 文档）
- 新建：测试你的插件——MockProvider 剧本（末条重复语义的坑与正确收尾）、`collect/terminal` 断言、照 `tests/test_plugins.py` 惯例的最小完整示例（tmp_path fixture + `load_plugins(plugin_dirs=[...])` + `MoCode(home=...)`）、`with_context` 工具怎么拿假 ctx。
- `README.md` 或 `docs/plugins.md` 末尾加一行指针（plugins.md 只加指针一行，不动其他内容——W4-A 并行也在改它）。

### T3 RunState content 软上限（K15）
- `core/state.py`：`RunState(content_limit: int = 200_000)`（0 = 不限）；`apply(TextDelta)` 累加后超限则保留尾部并在前部置标记（如 `f"…[{len - kept} chars elided]\n"`）；快照语义不变（`content` 仍是 str，读者无感）。构造参数化即可，不给 config 键（内核内部安全阀，同 `tool_result_limit` 的定位）。
- 测试：超限后内容长度有界、有 elided 标记、未超限零影响。

### T4 channel 文档化（K15）
- `mocode/core/channel.py` docstring：replay/backlog 的语义、推荐值场景表（CLI 单会话默认 1000/1000；多会话 web 嵌入建议下调 replay 防 delta 重放风暴；慢读者必然 dropped>0 靠 state 重同步的哲学）。
- `docs/ARCHITECTURE.md`（本波归你所有）：模块地图补 `mocode/testing`；channel 参数与推荐值一节；RunState content 上限说明。

## 验收

1. `uv run pytest -q` 全绿，独立确认退出码 0。
2. `uv run python -c "from mocode.testing import MockProvider, collect, terminal, say, call_tool; print('ok')"` 真实输出。
3. `import mocode` 计时 <1ms（真实输出）。
4. `grep -rln "tests.providers" tests/` 的结果与你的迁移声明一致（应为空，或仅 test_builtin_plugins.py 且已注明）。

## 禁触清单

- `mocode/host/plugin/builtin/shell.py`、`mocode/core/events.py`、`mocode/host/plugin/context.py`、`mocode/plugins/__init__.py`、`docs/plugins.md` 的正文（只许加指针一行）、`tests/test_builtin_plugins.py`、`tests/test_shell_bg.py`（**全部 W4-A 并行领地**）。
- `mocode/core/agent.py`、`mocode/core/dispatch.py`、`mocode/core/tool.py`、`mocode/core/hook.py`、`mocode/host/**`、`mocode/cli/**`、`mocode/providers/**`。
- `tests/test_agent_loop.py`、`tests/test_tools.py` 等只做机械 import 替换，不改测试逻辑。
- spec 文件（归 lead）。

## 最终报告格式

工单状态表｜commit 清单（hash+message）｜自测真实结论（命令+退出码）｜偏差与取舍（tests/providers.py 是否残留及原因）｜未决问题。遇阻塞：报告后停止，不越界自救。
