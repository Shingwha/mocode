# Spec 01 · W1 — core：efforts 系列 + Provider 协议 + 删 extra_body

> 分支 `config-refactor-w1-core`；前置：无（第一个波）；深读：
> `specs/2026-10-02-config-refactor/00-overview.md`（总纲，必读）。

## 目标

在 core 落地开放的 efforts（推理档位）系列：`Effort = str` +
缺省系列 `EFFORTS`；`ModelSpec` 携带 `efforts`/`effort` 并把
`max_output` 改名为 `max_tokens`；`Provider.stream` 协议增加第 5 参
`effort`；内置 OpenAIProvider 删除 `extra_body`（含 stream_options
可覆盖路径），`effort` 非 None 时发送 `reasoning_effort`。对外行为变化仅限：
新增可选的 reasoning_effort 发送；删除 extra_body 透传能力。

## 写入范围（仅此清单内的文件）

- `mocode/core/provider.py`
- `mocode/core/agent.py`
- `mocode/core/__init__.py`
- `mocode/plugins/__init__.py`
- `mocode/testing/providers.py`
- `mocode/providers/openai.py`
- `examples/core/minimal.py`
- `examples/core/nested.py`
- `tests/test_retry.py`
- `tests/test_agent_loop.py`
- `tests/test_core.py`
- `tests/test_provider.py`
- `tests/test_config.py` —— **仅允许** `TestConfigModelSpec` 中三处
  `spec.max_output` 断言改为 `spec.max_tokens`（W2 将整体重写本文件，
  除此之外一行不动）

## 工单（按序执行，每项一个 commit）

### T1 core/provider.py — Effort/EFFORTS/ModelSpec/协议
- 模块顶部加类型别名 `Effort = str` 与常量
  `EFFORTS: tuple[Effort, ...] = ("low", "medium", "high")`。docstring 说明：
  开放有序系列 —— 三档是缺省，config 可声明任意自定义档位名，档位名由
  provider verbatim 翻译到电线。
- `ModelSpec`：`max_output` 改名 `max_tokens`；新增
  `efforts: tuple[Effort, ...] = EFFORTS` 与 `effort: Effort | None = None`。
  更新类 docstring（effort 缺席 = 请求不带该参数；efforts 是该模型的可选
  档位表，供前端选择器使用）。
- `Provider` 协议 `stream` 签名增加第 5 个位置参数 `effort`（更新协议
  docstring 中关于 dialect 的段落：输出上限叫 `max_tokens`，思考强度叫
  `effort`，由 provider 翻译）。
- `with_retry_stream` 不变（`*args` 原样透传）。
- `__all__` 增加 `Effort`、`EFFORTS`。

### T2 core/agent.py — 透传
- 传给 `with_retry_stream` 的实参：`self.model.max_output` →
  `self.model.max_tokens`，并在其后传 `self.model.effort`（第 5 个位置实参）。

### T3 导出
- `mocode/core/__init__.py`：import 并加入 `__all__`（`Effort`、`EFFORTS`）。
- `mocode/plugins/__init__.py`：SDK 面同步导出（跟随该文件现有从 core
  re-export 的模式）。

### T4 mocode/testing/providers.py — MockProvider
- `stream` 签名加 `effort`，在 `self.calls` 的记录 dict 中增加
  `"effort": effort`。SlowProvider 走 `*args` 无需改。

### T5 mocode/providers/openai.py — 删 extra_body，接 effort
- `__init__` 删除 `extra_body` 参数；`stream_options` 硬编码
  `{"include_usage": True}`（类属性或实例属性，去掉 lift-out 逻辑）。
- `stream(..., effort)`：`effort is not None` 时
  `request["reasoning_effort"] = effort`（自定义档位名 verbatim）。
- 更新模块内注释（原 extra_body/stream_options 段）与类 docstring。
- 构造函数签名变为 `OpenAIProvider(api_key, model="gpt-4o", base_url=None, retry_policy=None)`。

### T6 examples — 两个 provider double
- `examples/core/minimal.py` `EchoProvider.stream`、
  `examples/core/nested.py` `ScriptedProvider.stream` 增加 `effort` 参数
  （位置第 5，unused）。

### T7 测试修复
- `tests/test_retry.py`、`tests/test_agent_loop.py`：所有
  `async def stream(...)` double 加第 5 参 `effort`。
- `tests/test_core.py`：`max_output` → `max_tokens`。
- `tests/test_provider.py`：删除两个 extra_body 用例，改为：
  - `test_effort_is_sent_as_reasoning_effort`：传 `effort="high"`（经新
    构造函数/或 stream 实参 —— 跟随现有 `_stream` helper 形状），断言
    `sent[0]["reasoning_effort"] == "high"`；
  - `test_no_effort_means_no_reasoning_effort_field`：断言缺省请求无
    `reasoning_effort` 键；
  - 保留 `test_empty_tools_become_none_and_streaming_is_requested` 中
    stream_options 断言（现硬编码）。
- `tests/test_config.py`：仅三处 `spec.max_output` → `spec.max_tokens`。

## 验收（完成前自测，报告真实结论）

1. `uv run pytest` 全绿 —— 独立 `echo $?` 确认退出码 0。
2. `uv run python examples/core/minimal.py` 与 `nested.py` 正常运行退出 0。
3. `grep -rn "extra_body" mocode/ tests/ examples/` 无结果（应零命中）。
4. `grep -rn "max_output" mocode/core mocode/testing mocode/providers tests/test_core.py tests/test_provider.py` 无结果。

## 禁触清单

- `mocode/host/**`（含 config.py、runtime.py、conversation.py —— W2）
- `mocode/cli/**`（W3）
- `docs/**`、`README.md`、`AGENTS.md`（W3）
- `tests/test_config.py` 除上述三行断言外的任何部分、`tests/conftest.py`、
  `tests/test_dispatch.py`、`tests/test_conversations.py`、
  `tests/test_plugins.py`、`tests/test_plugin_message.py`、
  `tests/test_runtime.py`、`tests/test_commands.py`（W2/W3）
- 不改 `with_retry_stream` 的行为与签名；不新增 core 对 host/cli 的 import。

## 环境事实

- Windows + Git Bash；仓库 `C:\Users\shifu\Desktop\mocode`；worktree 由
  lead 创建在 `C:\Users\shifu\.worktrees\mocode\config-refactor-w1-core`，
  你在该 worktree 内工作，**绝不操作主检出、绝不 merge/push/tag/checkout
  master**。
- 开工第一步：`uv sync`（worktree 无 .venv，uv 会自动建）。
- 测试全量可能较慢，用 `uv run pytest -q` 也可以，但退出码必须独立确认。

## 最终报告格式

工单状态表（T1–T7）/ commit 清单（hash + message）/ 自测真实结论（命令 +
输出摘要 + 退出码）/ 偏差与取舍 / 未决问题。阻塞即停，不越界自救。
