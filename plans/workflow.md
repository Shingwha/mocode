# /workflow 实现计划

## 目标

在 MoCode 中集成 `/workflow` 功能，用户用 YAML 定义工作流，系统解析后内部调用 `mocode -p` 执行每个步骤，支持串行、并行、循环、任意步引用等模式。

---

## 架构

```
YAML 文件 → Workflow 类（解析+状态） → WorkflowRunner（mocode -p 执行）
              ↑ 可随时查询进度
```

---

## YAML 语法（全部）

```yaml
name: my-workflow
description: 描述

phases:
  - id: phase-id              # 可选，供 {phases.phase-id.output} 引用
    name: 阶段名
    parallel: true             # 可选：并行执行
    max_attempts: 3            # 可选：循环重试次数
    halt_if: 文本               # 可选：循环提前停止条件
    steps:
      - id: step-id            # 可选，供 {steps.step-id.output} 引用
        task: 任务描述
```

### 占位符语法

```
{args.key}               → 用户参数
{steps.step-id.output}   → 某 step 的输出文本
{steps.step-id.exit_code}→ 某 step 的退出码
{steps.step-id.duration} → 某 step 的执行秒数
{steps.step-id.error}    → 某 step 的错误信息
{phases.phase-id.output} → 某 phase 的合并输出
{env.VAR}                → 环境变量
{previous}               → 兼容：上一步/上一阶段输出
```

---

## 五种模式

| 模式 | YAML 写法 | Runner 行为 |
|------|-----------|-------------|
| **串行** | 默认 | step 逐个跑 `mocode -p` |
| **并行** | `parallel: true` | `asyncio.gather` 同时跑多个 `mocode -p` |
| **返回验证** | 生成(串行) + 验证(并行) + 裁决(串行) | 组合串行+并行 stage |
| **循环** | `max_attempts: N` + `halt_if:` | 外层 for 循环包裹 stage |
| **DAG** | 不同 phase 引用不同 `{steps.id.output}` | 任意拓扑，不限于线性 |

---

## 数据模型

```python
@dataclass
class Step:
    id: str = ""
    task: str

@dataclass
class Phase:
    id: str = ""
    name: str
    steps: list[Step]
    parallel: bool = False
    max_attempts: int = 1
    halt_if: str | None = None

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

---

## Workflow 类（状态持有者）

```python
class Workflow:
    @classmethod
    def from_yaml(cls, path: Path) -> Workflow

    # 运行时状态（随时可查询）
    status: str              # idle → running → done / error
    phase_index: int
    step_index: int
    results: list[StepResult]

    # 查询方法
    current_phase  → Phase | None
    current_step   → Step | None
    total_phases   → int
    total_steps    → int
    completed_steps → int
    progress_bar() → "Phase 2/3 · Step 1/2"
    summary()      → 运行总结文本
```

---

## 执行引擎

```python
class WorkflowRunner:
    """每个 step = 一个 mocode -p 子进程。"""

    def __init__(self, workflow: Workflow, mocode_cmd: str = "mocode")

    async def run(self, args: dict | None = None):
        """执行完整工作流。"""
        # 串行 phase: _run_serial()
        # 并行 phase: _run_parallel()
        # 循环: for attempt in range(phase.max_attempts)
        # 每个 step/s 执行后按 id 存入 context["steps"] / context["phases"]
        # 模板替换 fill_template() 支持 {steps.web.output} 点路径
```

### 关键逻辑

```python
# 填充模板
_RE = re.compile(r"\{(\w+(?:\.\w+)*)\}")

def fill_template(template: str, context: dict) -> str:
    # 解析 "steps.web.output" 点路径，从 context 中逐级查找

# 执行后存 context
if step.id:
    context.setdefault("steps", {})[step.id] = {
        "output": sr.output,
        "exit_code": sr.exit_code,
        "duration": sr.duration,
        "error": sr.error or "",
    }
```

---

## WorkflowRegistry

```python
class WorkflowRegistry:
    def __init__(self, dirs: list[Path]):
        # 搜索路径: ~/.mocode/workflows/, ./.mocode/workflows/

    def list(self) -> list[Workflow]
    def get(self, name: str) -> Workflow | None
```

---

## /workflow 命令

| 子命令 | 行为 |
|--------|------|
| `/workflow` | 交互菜单（list / run / show / create） |
| `/workflow list` | 列出所有 YAML 工作流 |
| `/workflow run <name> [args]` | 执行工作流，实时显示进度 |
| `/workflow show <name>` | 显示工作流定义（phase、step 列表） |
| `/workflow create <描述>` | 注入 prompt 让 LLM 创建工作流 |

---

## 文件清单

| 文件 | 行数 | 说明 |
|------|------|------|
| `mocode/workflow/__init__.py` | ~130 | Workflow, Phase, Step, StepResult, WorkflowRegistry, fill_template |
| `mocode/workflow/runner.py` | ~110 | WorkflowRunner — 执行引擎 |
| `mocode/app/cli/commands/workflow.py` | ~160 | /workflow slash command |
| `mocode/app/cli/app.py` | +8 | 注册 registry + command |
| `tests/test_workflow.py` | ~200 | 测试 |
| **总计** | **~400** | |

---

## 实现步骤

1. **`mocode/workflow/__init__.py`** — 数据模型 + Workflow.from_yaml + WorkflowRegistry + fill_template
2. **`mocode/workflow/runner.py`** — WorkflowRunner（串行/并行/循环 + context 存储）
3. **`tests/test_workflow.py`** — 测试
4. **`mocode/app/cli/commands/workflow.py`** — /workflow 命令
5. **`mocode/app/cli/app.py`** — 注册整合

---

## 设计决策

| 决策 | 理由 |
|------|------|
| YAML 定义 | 结构化、可解析、可查进度 |
| `mocode -p` 执行 | 复用已有能力，真隔离，不污染上下文 |
| 每个 step 独立进程 | 不共享上下文，可并行 |
| 零新增依赖 | pyyaml 已有 |
| `{steps.id.output}` 引用 | 支持 DAG 拓扑，不限线性 |
