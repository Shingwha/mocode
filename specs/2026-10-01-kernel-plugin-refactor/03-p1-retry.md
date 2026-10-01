# Spec 03 · K13 重试策略化：编排留内核，策略交给 Provider（波次 W2-B）

> 分支 `spec/p1-retry`；worktree `C:\Users\shifu\.worktrees\mocode\spec-p1-retry`；
> 前置：W1 已合并；深读：总纲 + `ref/kernel-plugin-api.md` 的 K13 与 §0（"重试编排留在内核"）。
> 你只读本 worktree 内的文件；绝不操作主检出，绝不 merge/push/tag。

## 现状事实

- `mocode/core/provider.py`：模块常量 `_MAX_RETRIES = 6`（共 7 次尝试）、`_BASE_DELAY = 1.0`、`_MAX_DELAY = 60.0`、`_JITTER_MAX = 0.5`（:204-207）；`with_retry_stream(provider, *args, max_retries=_MAX_RETRIES)`（:216-256）——重试窗口止于第一个 chunk，用 `provider.is_retriable()` 判定，退避 sleep 可取消。
- `Provider` Protocol（:170-197）：`model` property、`is_retriable(exc)`、`stream(messages, system, tools, max_tokens)`。**无 retry_policy**。
- `mocode/providers/openai.py`：`_retriable_exceptions()` 懒导入 RateLimitError/InternalServerError/APIConnectionError/APITimeoutError；`is_retriable` 只做 isinstance。
- 调用点：`mocode/core/agent.py` 的 `_iterate` 以 5 个位置参数调 `with_retry_stream(provider, messages, system_prompt, tools, max_output)`——**不传 max_retries**。
- `tests/test_retry.py` 既有重试测试（剧本化 provider）；`tests/providers.py` 的 MockProvider/SlowProvider 不声明 retry_policy。

## 目标

重试**编排**仍在内核（安全边界不动：窗口在首 chunk 前关闭、sleep 可取消）；重试**策略**（几次、退多久、尊不尊重 Retry-After）从模块常量变为 `RetryPolicy`，provider 自带默认、调用方显式可覆盖。`agent.py` 调用点零改动。

## 工单（每项一个 commit）

### T1 RetryPolicy + 协议属性
- `core/provider.py`：
  ```python
  @dataclass(frozen=True)
  class RetryPolicy:
      max_attempts: int = 7        # 含首次，对齐现状
      base_delay: float = 1.0
      max_delay: float = 60.0
      jitter: float = 0.5
      honor_retry_after: bool = True   # 429 的 Retry-After 优先于指数退避
  ```
  模块级 `_DEFAULT_RETRY_POLICY = RetryPolicy()` 取代四个常量（常量删除）。
- `Provider` Protocol 声明 `retry_policy` property（`-> RetryPolicy`）。
- **解析必须防御式**：`policy = policy or getattr(provider, "retry_policy", None) or _DEFAULT_RETRY_POLICY`——未声明的 provider（含 tests 里的 MockProvider/SlowProvider）自动落内核默认，**不需要改它们**。

### T2 with_retry_stream 收策略
- 签名：`with_retry_stream(provider, *args, max_retries=None, policy: RetryPolicy | None = None)`——`max_retries` 参数**删除**（全库唯一调用点不传它，安全）；`policy` 显式传入时最高优先。
- 退避按 policy：`delay = min(base_delay * 2**(attempt-1), max_delay)`，jitter 在 `[0, jitter]` 均匀；`honor_retry_after=True` 时先尝试从异常取 `Retry-After`：`getattr(exc, "response", None)` → `.headers`（大小写不敏感）→ 数值秒（int/float）直接用，HTTP-date 格式跳过并落回指数退避；core **不 import openai**——只做鸭子类型探测。
- 重试窗口语义不变（首 chunk 前关闭）、sleep 仍可取消——现有测试必须原样通过。

### T3 OpenAIProvider 默认策略
- `providers/openai.py`：实例属性 `retry_policy = RetryPolicy(honor_retry_after=True)`（官方 API 尊重 429 限流头）；docstring 说明。构造函数不加参数（per-model 配置覆盖归 W3，见总纲取舍 #5）。

### T4 测试 + 文档
- `tests/test_retry.py` 扩展：policy 解析优先序（显式 > provider > 默认）、Retry-After 数值被尊重且优先于指数退避、`max_attempts` 用尽后抛原始异常、jitter 落在界内、未声明 retry_policy 的 provider 用默认（现有 MockProvider 剧本即可覆盖）。
- `docs/providers.md`：`retry_policy` 一节——协议要求、默认值、自定义示例。

## 验收

1. `uv run pytest -q` 全绿，独立确认退出码 0。
2. `git diff <merge-base> -- mocode/core/agent.py` **为空**（调用点零改动的硬证据）。
3. `grep -n "_MAX_RETRIES\|_BASE_DELAY\|_MAX_DELAY\|_JITTER_MAX" mocode/core/provider.py` 零命中。

## 禁触清单

- `mocode/core/agent.py`（零改动是验收项）、`mocode/core/tool.py`、`mocode/core/hook.py`、`mocode/core/events.py`、`mocode/core/dispatch.py`、`mocode/core/prompt.py`（W2-A 领地或本波无关）。
- `mocode/host/**`、`mocode/cli/**`、`mocode/plugins/**`、`examples/**`。
- `tests/providers.py`、`tests/test_agent_loop.py`、`tests/test_tools.py`、`tests/test_config.py`、`tests/test_runtime.py`、`tests/test_builtin_plugins.py`、`tests/test_plugins.py`、`tests/test_cli_plugin.py`、`tests/test_plugin_install.py`。
- `docs/plugins.md`、`docs/ARCHITECTURE.md`、`TODO.md`、`AGENTS.md`、spec 文件。

## 最终报告格式

工单状态表｜commit 清单（hash+message）｜自测真实结论（命令+退出码）｜偏差与取舍｜未决问题。遇阻塞：报告后停止，不越界自救。
