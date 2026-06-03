# Workflow Display 更新 — 实施计划

## 现状分析

控制流引擎（goto/lanes/circuit breaker）已实现，但 display 层未同步更新。具体问题：

1. **`_step_done` 是死代码** — `WorkflowRunner._step_done()` 定义了回调签名（第 48-50 行），但 `_run_steps` 和 `_run_single_lane` 中从未调用它。步骤完成后没有任何视觉反馈（除了 spinner detail）。

2. **`workflow_step_done` 缺少 lanes 上下文** — 当前签名是 `(phase_name, exit_code, duration)`，无法区分步骤来自哪条 Lane。

3. **`workflow_start` 显示扁平数量** — `N phases · M steps` 没有传达 lanes 的并行语义。

4. **`workflow_summary` 只显示最后一个结果** — 在 lanes 模式下，`wf.results` 中来自不同 Lane 的结果交错排列，只显示最后一个不具有代表性。

5. **goto 跳转无视觉反馈** — 当 goto 触发 phase/step 跳转时，用户看不到任何提示。

6. **Lane 取消无视觉反馈** — 一条 Lane 触发跨 Phase 跳转时，其他 Lane 被静默取消。

## 设计原则

1. **最少破坏性** — `StepResult` 加 `lane` 字段（默认 None，零迁移成本）
2. **回调签名升级** — `_step_done` 从 `(str, int, float)` 改为传 `StepResult`（仅一个消费者，内部 API）
3. **视觉层次** — Lane 步骤用前缀缩进，Phase 级用分隔符，继承已有颜色体系
4. **结构化摘要** — lanes 模式下按 Lane 分组输出，而非扁平显示最后一个

## 涉及文件

| 文件 | 操作 | 说明 |
|---|---|---|
| `mocode/app/workflow/__init__.py` | **修改** | `StepResult` 加 `lane` 字段 |
| `mocode/app/workflow/runner.py` | **修改** | 接入 `_step_done` 调用；更新回调签名；加 goto/jump/cancel 进度消息 |
| `mocode/app/cli/display.py` | **修改** | `workflow_start` / `workflow_step_done` / `workflow_summary` 三个方法 |
| `mocode/app/cli/theme.py` | **修改**[可选] | 加 `icon_lane` / `icon_goto` / `icon_cancel` |
| `tests/test_workflow.py` | **修改** | 更新回调签名相关的测试；加新的 display 测试 |

## 实施步骤

### Step 1 — `StepResult` 加 `lane` 字段

**做什么：** 在 `StepResult` dataclass 加 `lane: str | None = None`。

**文件：** `mocode/app/workflow/__init__.py`

**验证：** `StepResult(lane="correctness")` 正常工作；现有测试不感知该字段。

---

### Step 2 — Runner 更新回调签名 + 接入 `_step_done` 调用

**做什么：**
- `_step_done(self, phase_name: str, exit_code: int, duration: float)` → `_step_done(self, sr: StepResult, phase_name: str, lane_name: str | None = None)`
- 在 `_run_steps` 和 `_run_single_lane` 中，每次 `_exec_step` 后调用 `self._step_done(sr, phase.name, lane_name=lane.name if lane else None)`
- `_store_step` 时设置 `sr.lane = lane.id`（或通过 `_exec_step` 传参）

**进度消息（通过 `self._progress`）：**
| 事件 | 消息 |
|---|---|
| goto 跳转到同 Phase 的 Step | `→ goto: step_{target}` |
| goto 跨 Phase 跳转 | `→ goto: phase.{target}` |
| goto 终止 Workflow | `→ goto: __end__` |
| Lane 被取消 | `→ lane cancelled (other lane triggered cross-phase jump)` |
| 熔断触发 | `→ loop limit: {reason}` |

**文件：** `mocode/app/workflow/runner.py`

**验证：** 测试套件通过；`StepResult.lane` 在 lane steps 中正确填充。

---

### Step 3 — `Display.workflow_step_done` 更新

**做什么：** 更新签名和渲染逻辑。

```python
# 旧
def workflow_step_done(self, phase_name: str, exit_code: int, duration: float):
    ...

# 新
def workflow_step_done(self, sr: StepResult, phase_name: str, lane_name: str | None = None):
    ...
```

**渲染格式：**

顺序模式（无 lane）：
```
  ✓ Phase 1/3 · overview  1.2s
```

并行 lanes 模式：
```
  ◇ Phase 2/3: 并行审查  (2 lanes)
    │ correctness · ✓ check   2.1s
    │ style       · ✓ check   1.8s
    │ correctness · ✓ output  0.5s
```

**颜色/图标：**
- Lane 前缀用 `icon_lane`（`│`，DIM 颜色）
- Lane 名称用 `SOFT_CYAN` 或 `icon_lane` 颜色
- 首次出现某 Lane 时打印 Lane header（`◇ correctness`）

**文件：** `mocode/app/cli/display.py`

**验证：** 运行 lanes workflow 看到结构化输出。

---

### Step 4 — `Display.workflow_start` 更新

**做什么：** 当 Phase 有 lanes 时，显示 lane 数量：

```
● code-review  3 phases · 5 steps (2 parallel lanes)
```

计算方式：`sum(len(p.lanes) for p in wf.phases if p.lanes)`

**文件：** `mocode/app/cli/display.py`

---

### Step 5 — `Display.workflow_summary` 保持不变

**做什么：** 保持当前行为——只显示最后一个 StepResult 的输出。不管是顺序模式还是 lanes 模式，都只展示最终结果，不按 Lane 展开，避免杂乱。

```
■ code-review  5 steps  7.5s

[最后一个 step 的输出]
```

**文件：** 无需修改

---

### Step 6 — Theme 添加图标（可选但推荐）

**做什么：** 在 `Theme` dataclass 添加新图标字段：

```python
icon_lane: str = "│"
icon_goto: str = "→"
icon_cancel: str = "↯"
icon_phase: str = "◇"
```

**文件：** `mocode/app/cli/theme.py`

---

### Step 7 — 测试更新

**做什么：**
- `test_step_result_with_lane` — `StepResult` 支持 `lane` 字段
- `test_workflow_step_done_lane` — mock display，验证 lane info 正确传递
- `test_workflow_step_done_called` — runner 实际调用了 `_step_done`
- 更新任何 mock `_on_step_done` 的测试（如果有）

**文件：** `tests/test_workflow.py`

---

## 实施顺序

```
Step 1: StepResult.lane 字段
  └── __init__.py

Step 2: Runner 回调接入
  └── runner.py (调用 _step_done + 进度消息)

Step 3: workflow_step_done 显示
  └── display.py

Step 4: workflow_start 显示
  └── display.py

Step 5: workflow_summary 结构化
  └── display.py

Step 6: Theme 图标 (可选)
  └── theme.py

Step 7: 测试
  └── test_workflow.py
```

## 风险与注意事项

1. **回调签名破坏性变更** — `_step_done` 从 `(str, int, float)` 变成 `(StepResult, str, str | None)`。唯一消费者是 `Display.workflow_step_done`，已在 Step 3 同步更新。内部 API，风险低。

2. **`StepResult.lane` 的向后兼容** — `@dataclass` 默认值 `None` 确保零迁移成本。旧的 `StepResult(pi, si, ...)` 调用继续工作。

3. **`workflow_summary` 保持简单** — 只显示最后一个 step 的输出，不作 Lane 分组展开，避免杂乱。

4. **results 交错排序** — 在 `wf.results: list[StepResult]` 中，Lane 步骤交错排列（因为并发执行）。摘要只取最后一个，不受顺序影响。
