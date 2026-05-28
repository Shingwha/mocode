# Goal Feature Implementation Plan

## Context

MoCode 0.3 新增 goal 功能：LLM 通过 Tool 自主创建/管理目标，GoalHook 在 `after_iteration` 评估条件并注入反馈，通过 `continue_loop` 机制让 `_loop()` 继续循环。一切由 Agent 自行管理。

同时统一消息注入原则：**只注入 user 消息，LLM 自己决定如何回复**。Compact 也遵循此原则，去掉硬编码的 assistant 确认消息。

## Flow

```
LLM 调用 goal(action="set", goal="所有测试通过")
  → GoalHook.condition 被设置
  → LLM 继续工作（调用工具...）
  → LLM 给出最终回复（无工具调用）→ _loop() else 分支
  → after_iteration 触发
  → GoalHook 评估条件，注入 user 反馈 + continue_loop=True
  → _loop() 同步 messages，continue 回到 while 顶部
  → provider.call(messages, ...) → LLM 对 user 反馈生成新回复
  → ... LLM 继续工作 ...
  → LLM 认为目标达成，调用 goal(action="clear")
  → GoalHook.condition = None
  → 下次 after_iteration 不注入不设 continue_loop → 正常 break
```

## Design Principles

1. **只注入 user 消息**：Compact 和 Goal 都只注入 user 消息，不需要 assistant 确认。LLM 自然生成回复。
2. **GoalHook 只提供信息，不做决策**：评估结果注入 messages，LLM 自行决定下一步。
3. **LLM 通过 Tool 管理 goal 生命周期**：set/status/clear。

## Core Changes

### `mocode/core/hook.py` — AgentHookContext 加 1 个字段
```python
continue_loop: bool = False
```

### `mocode/core/agent.py` — `_loop()` else 分支加 3 行
```python
else:
    self._messages.append(self._assistant_msg(response))
    await self.hooks.after_iteration(ctx)
    self._messages = ctx.messages      # 同步 hook 的消息修改
    if ctx.continue_loop:              # hook 要求继续
        ctx.continue_loop = False      # 重置，下一轮需要重新设置
        continue
    break
```

注意：每次 continue 前重置 `continue_loop = False`，避免 hook 忘记设置时无限循环。

### `mocode/tools/compact.py` — 简化 compact_messages()

统一"只注入 user 消息"原则 + 去掉 `keep_recent_turns` 参数。Summary 本身就是完整上下文，不需要保留原始消息。

**`compact_messages()` 简化为：**
```python
# 改前：保留 recent_cleaned + assistant 确认
new_messages = [
    {"role": "user", "content": f"[Context Summary]\n{summary}\n[End of summary]"},
    {"role": "assistant", "content": "Understood, I will continue based on the summary."},
    *recent_cleaned,
]

# 改后：只有 summary
return [{"role": "user", "content": f"[Context Summary]\n{summary}\n[End of summary]"}]
```

**同时删除：**
- `compact_messages()` 的 `keep_recent_turns` 参数
- `find_turn_starts()` — 不再需要分割点计算
- `strip_tool_messages()` — 不再需要清理 recent 消息
- `CompactHook.__init__` 的 `keep_recent_turns` 参数
- `CompactTool` 工厂的 `keep_recent_turns` 参数

## Files to Create

### 1. `mocode/prompts/goal.py` — 评估器 prompt

用 `Prompt` + `Section`（对标 `mocode/prompts/compact.py`）：

- `goal_evaluator_system_prompt: Prompt` — 要求输出 `MET: true/false` + `REASON: <text>`
- `GOAL_USER_TEMPLATE: str` — `{condition}` + `{conversation}` 占位符

### 2. `mocode/tools/goal.py` — 核心实现

#### `GoalEvaluator`
```python
class GoalEvaluator:
    def __init__(self, provider: Provider): ...

    async def evaluate(self, condition: str, messages: list[dict]) -> tuple[bool, str]:
        # 复用 compact.format_messages_for_summary() 序列化对话
        # 调用 provider，解析 MET:/REASON: 响应

    @staticmethod
    def _parse_response(text: str) -> tuple[bool, str]:
        # 按行扫描 MET: true/false 和 REASON: ...
```

#### `GoalHook(AgentHook)`

只覆盖 `after_iteration`。始终注册但 `condition=None` 时不做任何事。

```python
class GoalHook(AgentHook):
    def __init__(self, evaluator: GoalEvaluator, max_turns: int = 50):
        self._evaluator = evaluator
        self._max_turns = max_turns
        self.condition: str | None = None
        self._turn_count: int = 0

    async def after_iteration(self, ctx):
        if not self.condition:
            return
        self._turn_count += 1
        if self._turn_count > self._max_turns:
            ctx.messages.append({
                "role": "user",
                "content": f"[Goal] Max turns ({self._max_turns}) reached. Stopping.",
            })
            self.condition = None
            return
        met, reason = await self._evaluator.evaluate(self.condition, ctx.messages)
        if met:
            feedback = f"[Goal] Goal achieved: {reason}"
        else:
            feedback = f"[Goal] NOT YET MET: {reason}\nOriginal goal: {self.condition}"
        ctx.messages.append({"role": "user", "content": feedback})
        ctx.continue_loop = True
```

注意：即使评估为 met 也继续循环。LLM 读到 "Goal achieved" 后自己决定是否 clear。

#### `GoalTool` — LLM 管理接口

```python
def GoalTool(
    evaluator: GoalEvaluator,
    goal_hook: GoalHook,
    get_messages: Callable[[], list[dict]],
) -> Tool:
    # action: "set" | "status" | "clear"
    # set → goal_hook.condition = args["goal"]; goal_hook._turn_count = 0
    # status → evaluate and return current state
    # clear → goal_hook.condition = None
```

Tool 参数：
```json
{
  "action": {"type": "string", "enum": ["set", "status", "clear"]},
  "goal": {"type": "string", "description": "Required for 'set' action"}
}
```

### 3. `tests/test_goal.py`

对标 `tests/test_compact.py`：
- `TestGoalEvaluator` — 解析 true/false/fallback、evaluate 调用
- `TestGoalHook` — 无 goal 时不操作、注入 user 反馈、max_turns 安全阀、continue_loop 设置
- `TestGoalIntegration` — _loop() 中 continue_loop 续循环
- `TestGoalTool` — set/status/clear

## Files to Modify

| 文件 | 改动 |
|------|------|
| `mocode/core/hook.py` | `AgentHookContext` 加 `continue_loop: bool = False` |
| `mocode/core/agent.py` | `_loop()` else 分支加 3 行（sync + check + reset） |
| `mocode/tools/compact.py` | `compact_messages()` 去掉 assistant 确认消息 |
| `mocode/prompts/__init__.py` | 加 goal prompt 导出 |
| `mocode/tools/__init__.py` | 加 GoalHook, GoalTool, GoalEvaluator 导出 |
| `main.py` | `create_agent()` 中注册 GoalHook + GoalTool |

## Reused Code

| 复用项 | 来源 |
|--------|------|
| `format_messages_for_summary()` | `mocode/tools/compact.py:37` |
| `Prompt` + `Section` | `mocode/core/prompt.py` |
| `AgentHook` + `AgentHookContext` | `mocode/core/hook.py` |

## Implementation Order

1. `mocode/core/hook.py` — 加 continue_loop
2. `mocode/core/agent.py` — 加 continue_loop 检查
3. `mocode/tools/compact.py` — 去掉 assistant 确认消息
4. `mocode/prompts/goal.py` — 评估器 prompt
5. `mocode/tools/goal.py` — GoalEvaluator + GoalHook + GoalTool
6. `mocode/prompts/__init__.py` — 加导出
7. `mocode/tools/__init__.py` — 加导出
8. `tests/test_goal.py` — 全量测试
9. `main.py` — create_agent() 中注册

## Verification

```bash
uv run pytest tests/test_goal.py -v
uv run pytest tests/ -v
```
