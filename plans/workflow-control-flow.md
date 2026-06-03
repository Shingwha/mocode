# Workflow 控制流引擎 — 实施计划

## 设计原则

1. **最少数据类** — GotoRule / Step / Lane / Phase / Workflow / StepResult，共 6 个
2. **每个字段都必要** — 不设"未来可能用到"的字段
3. **两个 goto 级别** — Step-level 管单步路由，Phase-level 管并行聚合路由
4. **Lane 代替 `parallel`** — 不再有 `parallel: bool`，改用 `lanes` 表达并行
5. **不需要向后兼容** — 旧 `parallel` / `max_attempts` / `halt_if` 全部删除

---

## 一、数据模型（`mocode/app/workflow/__init__.py`）

### 1.1 GotoRule

```python
@dataclass
class GotoRule:
    match: str | None = None   # regex, None = default/fallback
    to: str = "next"           # 目标地址
    max: int = 0               # 此路径最大命中次数, 0 = 不限
```

**目标地址语法：**

| 值 | 含义 |
|---|---|
| `"next"` | 下一步（Step 上下文中=同 Lane 下一 Step；Phase 上下文中=下一 Phase） |
| `"end"` | 结束当前 Lane / Phase |
| `"__end__"` | 终止整个 Workflow |
| `"step_id"` | 跳到同 Lane 内某 Step（仅 Step 级 goto 可用） |
| `"phase.phase_id"` | 跳到某 Phase |

### 1.2 Step

```python
@dataclass
class Step:
    id: str = ""
    task: str = ""
    goto: list[GotoRule] = field(default_factory=list)
```

**变化：** 新增 `goto` 字段。

### 1.3 Lane

```python
@dataclass
class Lane:
    id: str = ""
    name: str = ""
    steps: list[Step] = field(default_factory=list)
```

**新增。** 极简：没有 `output` 字段（取最后一个 Step 的输出），没有自身的 `goto`（路由由 Step 内部的 goto 和 Phase 级 goto 负责）。

### 1.4 Phase

```python
@dataclass
class Phase:
    id: str = ""
    name: str = ""
    steps: list[Step] = field(default_factory=list)   # 顺序模式
    lanes: list[Lane] = field(default_factory=list)    # 并行模式，与 steps 互斥
    max_iterations: int = 0                            # 0 = 继承 workflow
    goto: list[GotoRule] = field(default_factory=list) # 所有 step/lane 完成后
```

**变化：**
- 删除 `parallel: bool`
- 删除 `max_attempts: int`
- 删除 `halt_if: str | None`
- 新增 `lanes: list[Lane]`
- 新增 `max_iterations: int`
- 新增 `goto: list[GotoRule]`

### 1.5 Workflow

```python
@dataclass
class Workflow:
    name: str
    description: str = ""
    phases: list[Phase] = field(default_factory=list)
    path: Path | None = None
    max_iterations: int = 100    # 全局 Phase 入口计数上限

    # Runtime state（由 runner 写入）
    status: str = "idle"         # idle | running | done | error | loop_limit
    phase_index: int = 0
    step_index: int = 0
    results: list[StepResult] = field(default_factory=list)
```

**变化：** 新增 `max_iterations`。

### 1.6 StepResult（不变）

```python
@dataclass
class StepResult:
    phase_index: int
    step_index: int
    task: str
    output: str
    exit_code: int
    duration: float
    error: str | None = None
```

### 1.7 解析逻辑变更

`Phase.from_dict` 需要处理：

```python
@classmethod
def from_dict(cls, data: dict) -> Phase:
    lanes_data = data.get("lanes")
    if lanes_data:
        lanes = [Lane.from_dict(l) for l in lanes_data]
        steps = []
    else:
        lanes = []
        steps = [Step.from_dict(s) for s in data.get("steps", [])]

    return cls(
        id=data.get("id", ""),
        name=data.get("name", ""),
        steps=steps,
        lanes=lanes,
        max_iterations=data.get("max_iterations", 0),
        goto=[GotoRule.from_dict(g) for g in data.get("goto", [])],
    )
```

`Workflow.from_yaml` 新增 `max_iterations` 字段解析。

---

## 二、上下文变量（`mocode/app/workflow/__init__.py`）

### 变量命名空间

| 写法 | 来源 | 已有？ |
|---|---|---|
| `{args.key}` | CLI 参数 | ✅ |
| `{env.VAR}` | 环境变量 | ✅ |
| `{previous}` | 同 Lane 上一个 Step 的输出 | ✅ |
| `{steps.id.output}` | 同 Lane 内某 Step 的输出 | ✅ |
| `{lane.lane_id.output}` | 某 Lane 的最终输出 | ➕ 新增 |

**不引入** `lane.lane_id.steps.step_id.output` — 太复杂，需要时用 `{steps.id.output}` 即可。

### `fill_template` 修改

当前 `fill_template` 只解析 `bucket.key` 两层路径。`lane.lane_id.output` 是三层路径。需要修改 `_RE_PLACEHOLDER` 和 `_replace` 逻辑以支持 `lane.my_lane.output` 这种模式。

当前正则：`r"\{(\w+(?:\.\w+)*)\}"` — 实际上已经支持多级点了，但因为 `_replace` 只 split 一次，所以只支持两层。需要升级 `_replace` 来处理 `lane.xxx.output` → context["lane"]["xxx"]["output"]。

---

## 三、运行器（`mocode/app/workflow/runner.py`）

### 3.1 顶层控制流

```python
async def run(self, args: dict | None = None) -> list[StepResult]:
    wf = self.workflow
    wf.status = "running"
    wf.results.clear()
    wf.phase_index = 0
    wf.step_index = 0

    self._context = {
        "args": args or {},
        "env": dict(os.environ),
        "steps": {},
        "lanes": {},
        "phases": {},
    }

    self._phase_counter: dict[str, int] = {}       # Phase 入口计数
    self._wf_phase_entry_count: int = 0             # 全局 Phase 入口计数
    self._goto_counter: dict[str, int] = {}         # GotoRule 命中计数

    current_phase_id = wf.phases[0].id if wf.phases else None

    try:
        while current_phase_id is not None:
            phase = self._find_phase(current_phase_id)
            if phase is None:
                raise ValueError(f"Phase '{current_phase_id}' not found")

            wf.phase_index = wf.phases.index(phase)

            # ── 熔断检查 ──
            self._wf_phase_entry_count += 1
            if self._wf_phase_entry_count > wf.max_iterations:
                wf.status = "loop_limit"
                self._progress(f"Workflow loop limit reached after {wf.max_iterations} phase executions")
                break

            pid = phase.id or f"__phase_{wf.phase_index}"
            self._phase_counter[pid] = self._phase_counter.get(pid, 0) + 1
            phase_limit = phase.max_iterations or wf.max_iterations
            if self._phase_counter[pid] > phase_limit:
                wf.status = "loop_limit"
                self._progress(f"Phase '{phase.name}' loop limit reached after {phase_limit} entries")
                break

            # ── 执行 Phase ──
            if phase.lanes:
                next_target = await self._run_lanes(phase)
            else:
                next_target = await self._run_steps(phase)

            # ── 确定下一步 ──
            if next_target == "__end__":
                break
            elif next_target and next_target.startswith("phase."):
                current_phase_id = next_target.split(".", 1)[1]
                continue
            elif next_target == "next":
                # 去下一个 Phase
                idx = wf.phases.index(phase)
                current_phase_id = wf.phases[idx + 1].id if idx + 1 < len(wf.phases) else None
                continue
            elif next_target == "end":
                break
            else:
                # 未知目标
                current_phase_id = None

        if wf.status not in ("error", "loop_limit"):
            wf.status = "done"
        return wf.results

    except Exception:
        wf.status = "error"
        raise
```

### 3.2 顺序模式 — `_run_steps`

```python
async def _run_steps(self, phase: Phase) -> str | None:
    """Run phase.steps sequentially. Returns goto target or None."""
    si = 0
    while si < len(phase.steps):
        step = phase.steps[si]
        wf.step_index = si
        self._progress(f"Step {si + 1}/{len(phase.steps)}: {step.task[:40]}")

        sr = await self._exec_step(step, wf.phases.index(phase), si)
        wf.results.append(sr)
        self._store_step(step, sr, lane_id=None)
        self._context["previous"] = sr.output

        if sr.exit_code != 0:
            # 失败 → 检查 goto 看是否要跳转
            pass  # 继续执行 goto 逻辑

        # 解析 goto
        target = self._resolve_goto(step.goto, sr.output, f"step_{phase.id}_{step.id}")
        if target == "next":
            si += 1
        elif target == "end":
            break
        elif target == "__end__":
            return "__end__"
        elif target.startswith("phase."):
            self._store_phase_output(phase)
            return target
        else:
            # step_id: 同 Lane 内跳转
            idx = self._find_step_index(phase.steps, target)
            if idx is not None:
                si = idx
            else:
                self._progress(f"Warning: step '{target}' not found, continuing")
                si += 1

    # 所有 step 跑完 → 检查 Phase.goto
    self._store_phase_output(phase)
    return self._resolve_goto(phase.goto, "", f"phase_{phase.id}")
```

### 3.3 并行模式 — `_run_lanes`

```python
async def _run_lanes(self, phase: Phase) -> str | None:
    """Run all lanes concurrently. Returns goto target or None."""
    self._progress(f"Running {len(phase.lanes)} lanes in parallel")

    async def _run_single_lane(lane: Lane, lane_idx: int) -> str | None:
        """Execute one lane. Returns a target if cross-phase jump needed."""
        lane_output = None
        si = 0
        while si < len(lane.steps):
            step = lane.steps[si]
            sr = await self._exec_step(step, wf.phases.index(phase), si)
            wf.results.append(sr)
            self._store_step(step, sr, lane_id=lane.id)
            lane_output = sr.output

            target = self._resolve_goto(step.goto, sr.output, f"step_{phase.id}_{lane.id}_{step.id}")
            if target == "next":
                si += 1
            elif target == "end":
                break
            elif target == "__end__":
                return "__end__"
            elif target.startswith("phase."):
                return target  # 跨 Phase 跳转
            else:
                # step_id jump within same lane
                idx = self._find_step_index(lane.steps, target)
                si = idx if idx is not None else si + 1

        # 存储 lane output
        if lane.id:
            self._context.setdefault("lanes", {})[lane.id] = {
                "output": lane_output or "",
            }
        return None  # lane 正常完成

    tasks = [_run_single_lane(lane, i) for i, lane in enumerate(phase.lanes)]
    results = await asyncio.gather(*tasks)

    # 检查是否有 lane 触发了跨 Phase 跳转
    for target in results:
        if target and target.startswith("phase."):
            self._store_phase_output(phase)
            return target
        if target == "__end__":
            return "__end__"

    # 所有 lanes 完成 → 检查 Phase.goto
    self._store_phase_output(phase)
    return self._resolve_goto(phase.goto, "", f"phase_{phase.id}")
```

### 3.4 Goto 解析引擎

```python
def _resolve_goto(self, rules: list[GotoRule], output: str, key_prefix: str) -> str:
    """Return the target of the first matching rule, or 'next'."""
    for rule in rules:
        rule_key = f"{key_prefix}_to_{rule.to}"
        current_count = self._goto_counter.get(rule_key, 0)

        if rule.max > 0 and current_count >= rule.max:
            continue  # 超过次数限制

        if rule.match is None:
            self._goto_counter[rule_key] = current_count + 1
            return rule.to

        if re.search(rule.match, output):
            self._goto_counter[rule_key] = current_count + 1
            return rule.to

    return "next"
```

### 3.5 辅助方法

```python
def _find_phase(self, phase_id: str) -> Phase | None:
    for p in self.workflow.phases:
        if p.id == phase_id:
            return p
    return None

def _find_step_index(self, steps: list[Step], step_id: str) -> int | None:
    for i, s in enumerate(steps):
        if s.id == step_id:
            return i
    return None

def _store_step(self, step: Step, sr: StepResult, lane_id: str | None) -> None:
    if not step.id:
        return
    entry = {
        "output": sr.output,
        "exit_code": sr.exit_code,
        "duration": sr.duration,
        "error": sr.error or "",
    }
    if lane_id:
        # 存储在 lane 命名空间下
        self._context.setdefault("steps_by_lane", {}).setdefault(lane_id, {})[step.id] = entry
    else:
        self._context.setdefault("steps", {})[step.id] = entry

def _store_phase_output(self, phase: Phase) -> None:
    if not phase.id:
        return
    # Phase output = 最后一个 step 的输出（顺序模式）或 lanes 的合并（并行模式）
    if phase.lanes:
        outputs = []
        for lane in phase.lanes:
            if lane.id and lane.id in self._context.get("lanes", {}):
                outputs.append(self._context["lanes"][lane.id]["output"])
        combined = "\n".join(outputs)
    else:
        # 从 results 中取当前 phase 的最后一个 result
        phase_results = [r for r in self.workflow.results if r.phase_index == self.workflow.phases.index(phase)]
        combined = phase_results[-1].output if phase_results else ""
    self._context.setdefault("phases", {})[phase.id] = {"output": combined}
```

---

## 四、CLI 命令更新（`mocode/app/cli/commands/workflow.py`）

### 4.1 `_show` 方法

需要展示：
- Lanes 及其 Steps（含 goto）
- Steps（含 goto）
- Phase-level goto
- max_iterations（phase 和 workflow 级）

### 4.2 `_create` 方法的 prompt 模板

更新为新格式示例：

```yaml
name: my-workflow
description: 描述
max_iterations: 100

phases:
  - id: phase_id
    name: Phase Name
    max_iterations: 5          # optional
    steps:
      - id: step_id            # optional
        task: Task description
        goto:                  # optional
          - match: "pattern"   # optional regex
            to: target         # "next", "end", "step_id", "phase.xxx", "__end__"
            max: 3             # optional
          - to: default_target # fallback
    goto:                      # optional Phase-level
      - match: "pattern"
        to: phase.other_phase
      - to: phase.next_phase
```

或者 lanes 模式：

```yaml
phases:
  - id: research
    name: 并行调研
    lanes:
      - id: web
        name: 网页搜索
        steps:
          - id: search
            task: 搜索 {args.query}
          - id: summarize
            task: 总结搜索结果
    goto:
      - to: phase.synthesize
```

---

## 五、YAML 示例文件更新（`.mocode/workflows/code-review.yaml`）

重写为使用 lanes 的并行审查流程：

```yaml
name: code-review
description: 并行审查指定路径的代码
max_iterations: 10

phases:
  - id: scan
    name: 代码扫描
    steps:
      - id: overview
        task: 分析项目结构，列出 {args.path} 下的关键文件和模块
    goto:
      - to: phase.review

  - id: review
    name: 并行审查
    lanes:
      - id: correctness
        name: 正确性审查
        steps:
          - id: check
            task: |
              审查以下代码的正确性问题（逻辑错误、边界条件、异常处理）：
              {steps.overview.output}
          - id: output
            task: 输出正确性审查结论
      - id: style
        name: 风格审查
        steps:
          - id: check
            task: |
              审查以下代码的风格和可维护性（命名、重复代码、过度复杂）：
              {steps.overview.output}
          - id: output
            task: 输出风格审查结论
    goto:
      - to: phase.report

  - id: report
    name: 生成报告
    steps:
      - id: summary
        task: |
          综合并行审查结果：
          正确性：{lane.correctness.output}
          风格：{lane.style.output}
          按严重程度分组生成审查报告
```

---

## 六、涉及文件清单

| 文件 | 操作 | 说明 |
|---|---|---|
| `mocode/app/workflow/__init__.py` | **重写** | 新增 GotoRule, Lane；修改 Step, Phase, Workflow；升级 fill_template |
| `mocode/app/workflow/runner.py` | **重写** | 全新 goto 引擎，删除旧 parallel/loop 逻辑 |
| `mocode/app/cli/commands/workflow.py` | **修改** | 更新 _show, _create 展示新字段 |
| `.mocode/workflows/code-review.yaml` | **重写** | lanes 替代 parallel |
| `tests/test_workflow.py` | **重写** | 全新测试集 |

---

## 七、测试计划（`tests/test_workflow.py`）

### 7.1 模型测试
| 测试 | 说明 |
|---|---|
| `test_gotorule_from_dict` | 解析 match/to/max |
| `test_step_from_dict_with_goto` | Step 的 goto 解析 |
| `test_lane_from_dict` | Lane 含 steps |
| `test_phase_with_steps` | Phase 使用 steps 模式 |
| `test_phase_with_lanes` | Phase 使用 lanes 模式 |
| `test_phase_steps_lanes_mutually_exclusive` | steps 和 lanes 互斥，lanes 优先 |
| `test_workflow_max_iterations` | Workflow 解析 max_iterations |

### 7.2 模板变量测试
| 测试 | 说明 |
|---|---|
| `test_lane_output_placeholder` | `{lane.xxx.output}` 解析 |
| `test_phase_output_placeholder` | `{phase.xxx.output}` 解析 |

### 7.3 Runner — 顺序执行
| 测试 | 说明 |
|---|---|
| `test_steps_sequential` | 基本顺序执行 |
| `test_steps_goto_next` | goto: "next" 继续 |
| `test_steps_goto_end` | goto: "end" 提前结束 |
| `test_steps_goto_step_id` | 跳到同 Phase 的另一 Step |
| `test_steps_goto_phase` | 跨 Phase 跳转 |
| `test_steps_goto_end_workflow` | `__end__` 终止 |
| `test_steps_template_filled` | 模板变量填充 |

### 7.4 Runner — Lanes 并行执行
| 测试 | 说明 |
|---|---|
| `test_lanes_execute_all` | 多条 Lane 全部跑完 |
| `test_lanes_step_goto_phase_cancels_others` | 一条 Lane 跨 Phase 跳转 |
| `test_lanes_output_in_context` | lane output 存入 context |
| `test_lanes_phase_goto_after_all_done` | Phase.goto 在 lanes 完成后 |

### 7.5 Runner — Goto 解析
| 测试 | 说明 |
|---|---|
| `test_resolve_goto_match` | 正则匹配命中 |
| `test_resolve_goto_default` | 无 match 的兜底规则 |
| `test_resolve_goto_no_match` | 全不匹配返回 "next" |
| `test_resolve_goto_max_limit` | max 限制后跳过该规则 |

### 7.6 Runner — 熔断保护
| 测试 | 说明 |
|---|---|
| `test_workflow_max_iterations_loop_limit` | 全局熔断 |
| `test_phase_max_iterations_loop_limit` | Phase 级熔断 |
| `test_goto_max_hit_skips_rule` | GotoRule max 命中后跳过 |

### 7.7 CLI 命令测试
| 测试 | 说明 |
|---|---|
| `test_show_displays_lanes` | 展示 lanes 信息 |
| `test_show_displays_goto` | 展示 goto 规则 |
| `test_create_prompt_format` | 新格式 prompt |

---

## 八、实施顺序

```
Step 1: 数据模型
  └── GotoRule, Lane 数据类 + 修改 Step/Phase/Workflow + from_dict

Step 2: 模板变量
  └── 扩展 fill_template 支持 {lane.xxx.output}

Step 3: 模型测试
  └── 验证解析逻辑

Step 4: Runner — goto 引擎
  └── _resolve_goto + _find_phase + _find_step_index

Step 5: Runner — 顺序模式 (_run_steps)
  └── 含 Step.goto 解析

Step 6: Runner — 并行模式 (_run_lanes)
  └── asyncio.gather + 跨 Phase 取消

Step 7: Runner — 顶层循环
  └── Phase 入口计数 + 熔断

Step 8: Runner 测试
  └── 全部 7.3 - 7.6 测试

Step 9: CLI 命令更新
  └── _show / _create

Step 10: YAML 示例更新
  └── 重写 code-review.yaml

Step 11: CLI 测试
  └── 7.7 测试
```

---

## 九、风险与注意事项

1. **跨 Phase 跳转时取消其他 Lane** — 使用 `asyncio.gather` + `return_exceptions=True`，用 `asyncio.CancelledError` 配合 task.cancel()
2. **`lane.xxx.output` 在 lane 完成前不可用** — 当前顺序保证（lanes 并发，但各自内部顺序执行），merge 语义由 Phase.goto 阶段自然实现
3. **`steps` 引用的歧义** — 在 lanes 模式下，`{steps.id.output}` 只能引用同 Lane 内的 Step。跨 Lane 引用需要用 `{lane.xxx.output}`。设计上已经通过 `_store_step` 区分了 lane_id 和 steps 命名空间
4. **Phase `steps` 和 `lanes` 同时出现** — 按互斥处理：lanes 非空时忽略 steps。`from_dict` 中实现
