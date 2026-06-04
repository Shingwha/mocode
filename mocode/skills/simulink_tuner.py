"""Built-in skill: Simulink Tuner — MATLAB Simulink PID parameter tuning via matlab.engine.

Source: ai-tuner-amesim/.claude/skills/simulink-tuner/
"""

from __future__ import annotations

from mocode.core.skill import Skill

# ── Skill body ──────────────────────────────────────────────

_SKILL_CONTENT = """\
# Simulink AI 调参技能

## 概述

通过编写 Python 脚本调用 `matlab.engine` 连接 MATLAB/Simulink，实现 AI 驱动的控制器参数整定闭环：
**设定目标 → 读取参数 → AI 建议调整 → 执行仿真 → 分析结果 → 记录文档**。

所有调参过程统一用 Markdown 文档管理，保存在**用户项目根目录**的 `tuning_logs/` 目录下。

> **重要：** `tuning_logs/` 必须创建在用户的项目根目录（即用户的工作目录），**不是**本 skill 所在的 `.claude/skills/simulink-tuner/` 目录下。执行前确认当前工作目录是否为用户项目根目录。

---

## 调参脚本规范

所有 Python 脚本遵循以下规范：

1. **文件命名**：`tune_<model_name>_<timestamp>.py` 或功能脚本如 `explore_model.py`、`read_params.py`
2. **参数硬编码在字典中**，方便 AI 修改：
   ```python
   TUNING_CONFIG = {
       "model_path": r"C:/path/to/model.slx",
       "block_path": "model_name/PID Controller",
       "params": {"P": 1.0, "I": 0.1, "D": 0.01},
       ...
   }
   ```
3. **每个脚本可独立运行**：包含 `if __name__ == "__main__"` 入口
4. **结果输出 JSON**：方便 AI 解析和记录
5. **执行前确保虚拟环境已激活**：Git Bash `source .venv/Scripts/activate`，CMD/PowerShell `.venv\\Scripts\\activate`，然后 `python <script>.py` 运行

---

## 工作流程

> **强制要求：** 必须严格按照 Phase 0 → 1 → 2 → 3 → 4 → 5 的顺序执行，不得跳过或调换阶段。进入每个 Phase 前，**必须先阅读对应的 references 文档**，再开始执行。每个 Phase 的 `> 参见 ...` 行指向的文件就是该阶段必须阅读的文档。

> **实时日志要求：** 每个 Phase 完成后**立即**将结果追加到调参日志文件中，不得等到最后一次性写入。日志文件在 Phase 2 确认调参目标时创建，后续每个 Phase 完成后追加对应章节。如果中途失败或中断，已完成的记录不会丢失。

---

### Phase -1: 检查已有进度（首个 Phase）

**在执行任何操作之前**，首先检查用户项目根目录下是否存在 `tuning_logs/` 目录及日志文件：

1. **检查 `tuning_logs/` 目录是否存在**
2. **如果存在，读取最新的日志文件**（按文件名排序，取最新的 `YYYY-MM-DD_*.md`）
3. **从日志中提取已完成的 Phase 信息**：
   - 环境信息（Phase 0）→ 如果已有且 MATLAB 版本/路径一致，**跳过 Phase 0**
   - 模型探索结果（Phase 1）→ 如果已有且模型路径一致，**跳过 Phase 1**
   - 调参需求（Phase 2）→ 如果已有，向用户确认是否沿用或修改
   - 迭代记录（Phase 3）→ 如果已有，展示历史结果，询问是继续优化还是重新开始

4. **检查虚拟环境是否已就绪**：
   - `.venv/` 目录是否存在
   - `matlab.engine` 是否可导入
   - `numpy` 是否已安装
   - 如果全部就绪，**跳过 Phase 0 的环境安装步骤**，仅采集环境信息

**跳过规则：**
- 已完成的 Phase 直接跳过，不重复执行
- 日志中记录了失败原因的 Phase 需要重新执行
- 用户明确要求重新开始时，不跳过任何 Phase

---

### Phase 0: 环境准备

> **开始前阅读：** `vfs://simulink-tuner/references/environment_setup.md`

确认 Python 3.10 虚拟环境已创建并激活，安装 `matlab.engine` 和 `numpy`，验证 MATLAB 连接可用，采集环境信息。

**前置条件：** 用户已安装 MATLAB 且 License 可用。如未安装或版本不兼容，立即告知用户终止。

**安装步骤（按顺序）：**
1. `uv venv --python 3.10` — 创建虚拟环境
2. 激活虚拟环境
3. 安装 `matlab.engine`（通过 MATLAB 的 `setup.py`，**不用** `uv pip install`）
4. `uv pip install numpy` — 仿真数据计算必需
5. 验证 MATLAB 连接
6. 采集环境信息

**完成后立即写入日志：** 将环境信息追加到日志文件的"环境信息"章节。

### Phase 1: 模型探索

> **开始前阅读：** `vfs://simulink-tuner/references/model_exploration.md`

1. **自动探索模型结构** — 运行 `explore_model.py` 扫描所有 block，识别 PID/控制器类型，列出可调参数
2. **读取当前参数** — 运行 `read_params.py` 读取目标 block 的当前参数值和仿真时长
3. **读取被控对象参数** — 读取 Transfer Fcn 的分子/分母，了解系统特性
4. **读取输入信号参数** — 读取 Step block 的 `After`（幅值）、`Time`（起始时间），确认 setpoint
5. **读取输出变量配置** — 读取 To Workspace block 的 `VariableName` 和 `SaveFormat`，确认数据提取方式
6. **记录模型结构** — 通过以下方式获取模型的信号流图，写入日志：
   - **优先：** 请用户上传 Simulink 模型截图（在 Simulink 编辑器中截取模型窗口）
   - **备选：** 请用户口头描述信号流（如"Step → Sum → PID → Transfer Fcn → Scope"）
   - **补充：** 根据 `find_system` 扫描结果，AI 自动推断并用 ASCII 绘制简化信号流图

   记录格式示例：
   ```markdown
   ### 模型结构

   ```
   Step(200) → [+] → PID Controller → Transfer Fcn 1/(10s+1) → Output
                ↑                                         |
                └──────────── (反馈) ─────────────────────┘
   ```

   **截图：** （如有，嵌入图片链接或文字说明）
   ```

**关键注意事项：**
- `sim()` 返回 `Simulink.SimulationOutput` 对象，**不是**直接的数组
- 数据提取方式：`simOut = eng.sim(model_name, nargout=1)`，然后 `simOut.tout` 和 `simOut.simout.Data`
- 如果 `SaveFormat` 是 Timeseries，用 `.Data`；如果是 Structure，则用 `.signals.values`

**完成后立即写入日志：** 将模型结构（含信号流图）、当前参数、被控对象信息追加到日志文件。模型结构记录是后续调优的重要参考，必须包含完整的信号流向和各 block 参数。

### Phase 2: 确认调参目标

向用户确认以下信息：

- **调参目标**：超调量 < X%、上升时间 < Xs、稳态误差 < X%、settling time < Xs
- **调参模块**：从 Phase 1 扫描结果中选择
- **调参参数**：选择要调整的参数（P, I, D, N）
- **参数范围**：每个参数的搜索上下限
- **输入信号**：测试信号类型（阶跃/斜坡/正弦）及幅值（从 Step block 的 `After` 参数读取）
- **仿真时长**：默认根据系统特性建议
- **约束条件**：控制量饱和、安全约束等
- **最大迭代次数**：默认 20

**完成后立即写入日志：** 创建日志文件 `tuning_logs/YYYY-MM-DD_<描述>.md`，写入环境信息（Phase 0）和调参需求。

### Phase 3: 循环参数寻优

> **开始前阅读：** `vfs://simulink-tuner/references/tuning_strategies.md`，然后根据选定策略阅读 `vfs://simulink-tuner/references/script_template_ai_guided.md` 或 `vfs://simulink-tuner/references/script_template_grid_search.md`

根据参数空间和需求选择策略：

| 模式 | 适用场景 | 说明 |
|------|----------|------|
| **AI 引导逐步调** | 参数少（≤3）、需理解响应 | 每轮 AI 决定参数 |
| **网格搜索** | 参数少、范围明确 | 遍历组合找全局最优 |
| **AI + 网格混合** | 参数较多 | AI 选区间 + 网格搜索 |

编写并运行寻优脚本，直到目标达标或终止条件触发。

**每轮迭代后立即写入日志：** 每次仿真完成后，立即将本轮参数和指标追加到日志文件的"调参过程"章节。格式：

```markdown
### 迭代 #N

| 参数 | 值 |
|------|-----|
| P | X.X |
| I | X.X |

| 指标 | 数值 | 目标值 | 达标 |
|------|------|--------|------|
| 上升时间 | Xs | < Xs | ✅/❌ |
| 超调量 | X% | < X% | ✅/❌ |
| 调节时间 | Xs | < Xs | ✅/❌ |
| 稳态误差 | X | < X | ✅/❌ |

**小结：** [一句话总结本轮效果]
```

**AI 引导收敛后自动切换策略：** 如果 AI 引导连续 3 轮无改善（ITAE 变化 < 1%），自动切换到网格搜索模式，在当前最优参数附近进行细粒度搜索。

### Phase 4: 结果分析

> **开始前阅读：** `vfs://simulink-tuner/references/performance_metrics.md`

从寻优脚本输出中提取关键信息：
1. **汇总表** — 每轮迭代的参数值和指标
2. **达标判定** — 逐项对比目标与最终结果
3. **趋势分析** — 参数变化对指标的影响

**完成后立即写入日志：** 将结果分析章节追加到日志文件。

### Phase 5: 最终记录与模型更新

> **开始前阅读：** `vfs://simulink-tuner/references/log_template.md`

1. **补全日志** — 确保日志文件包含所有章节（环境信息、调参需求、初始状态、每轮迭代、最终结果、总结）
2. **写入总结** — 包含关键发现和后续建议
3. **询问用户** — 是否将最优参数写入模型并保存
4. **输出文件清单** — 列出所有生成的文件（日志、脚本、结果 JSON）

> 如果寻优中途失败或中断，也要记录已完成迭代和失败原因。此时日志中已有实时追加的记录，只需补全总结部分即可。

---

## 参考文件索引

| 文件 | 用途 |
|------|------|
| `vfs://simulink-tuner/references/environment_setup.md` | Phase 0 环境准备详细步骤 |
| `vfs://simulink-tuner/references/model_exploration.md` | Phase 1 模型探索与参数读取 |
| `vfs://simulink-tuner/references/tuning_strategies.md` | Phase 3 调参策略选择与 PID 经验 |
| `vfs://simulink-tuner/references/script_template_ai_guided.md` | AI 引导逐步调脚本模板 |
| `vfs://simulink-tuner/references/script_template_grid_search.md` | 网格搜索脚本模板 |
| `vfs://simulink-tuner/references/performance_metrics.md` | Phase 4 性能指标定义与分析 |
| `vfs://simulink-tuner/references/troubleshooting.md` | 常见问题排查 |
| `vfs://simulink-tuner/references/log_template.md` | 调参日志模板 |
| `vfs://simulink-tuner/references/matlab_api_reference.md` | MATLAB Engine API 参考 |
"""

# ── Reference files ─────────────────────────────────────────

_REF_ENVIRONMENT_SETUP = """\
# Phase 0: 环境准备

每次调参前确认运行环境可用。

## 1. 创建虚拟环境

使用 `uv` 创建 Python 3.10 虚拟环境：

```bash
uv venv --python 3.10
```

> Python 3.10 兼容 MATLAB R2022a–R2024a 全系列，避免版本匹配问题。

## 2. 激活虚拟环境

后续所有操作都在虚拟环境中执行：

```bash
# Git Bash
source .venv/Scripts/activate

# CMD / PowerShell
.venv\\Scripts\\activate
```

激活后确认：`python --version` 应显示 `3.10.x`。

## 3. 安装 matlab.engine

必须通过 MATLAB 自带的 setup.py 安装（`uv pip install` 不适用）：

```bash
# 找到 MATLAB 安装路径下的 engines/python 目录
cd "<MATLAB_ROOT>/extern/engines/python"
python setup.py install
```

`<MATLAB_ROOT>` 示例：`C:/Program Files/MATLAB/R2024a`

安装完成后 `cd` 回项目目录。

> 如果权限不足，尝试 `python setup.py install --user`。

## 4. 安装 numpy

仿真数据计算（指标分析）需要 numpy：

```bash
uv pip install numpy
```

## 5. 验证连接

运行最小测试脚本：

```python
import matlab.engine
import numpy as np

eng = matlab.engine.start_matlab()
print("MATLAB version:", eng.version())
print("numpy version:", np.__version__)
eng.quit()
```

运行成功则环境就绪。

## 6. 采集环境信息

验证连接成功后，立即采集以下信息写入调参日志（对应 `log_template.md` 第 1 节）：

```python
import matlab.engine, sys, os

eng = matlab.engine.start_matlab()

env_info = {
    "os":          eng.eval("computer()", nargout=1),
    "matlab_ver":  eng.version(),
    "matlab_root": eng.eval("matlabroot", nargout=1),
    "python_ver":  sys.version,
    "python_path": sys.executable,
}
print(env_info)

eng.quit()
```

> **前置条件：** 用户已安装 MATLAB 且 License 可用。如未安装或版本不兼容，立即告知用户终止。
"""

_REF_MODEL_EXPLORATION = """\
# Phase 1: 模型探索与参数读取

本阶段分两步：先自动探索模型结构并读取当前参数，再向用户确认调参目标。

## Step 1-A: 自动探索模型结构

用户通常不知道 block 的完整路径和参数名。编写 `explore_model.py` 自动扫描：

```python
import matlab.engine, os

eng = matlab.engine.start_matlab()
eng.load_system(r"<用户提供的 .slx 路径>", nargout=0)

# 1. 获取模型名（文件名去掉扩展名）
model_name = os.path.splitext(os.path.basename(r"<.slx路径>"))[0]

# 2. 列出所有 block
all_blocks = eng.find_system(model_name, nargout=1)
print("所有 block:")
for b in all_blocks:
    print(f"  {b}")

# 3. 筛选 PID / 控制器 block
pid_blocks = []
for b in all_blocks:
    try:
        mask = eng.get_param(b, "MaskType", nargout=1)
        if mask and "PID" in str(mask).upper():
            pid_blocks.append((b, mask))
    except:
        pass

print("\\n找到的 PID 控制器:")
for b, mask in pid_blocks:
    print(f"  路径: {b}  |  类型: {mask}")

# 4. 读取第一个 block 的可调参数
if pid_blocks:
    target = pid_blocks[0][0]
    dialog_params = eng.get_param(target, "DialogParameters", nargout=1)
    print(f"\\n{target} 的可调参数:")
    if dialog_params:
        for pname in dialog_params:
            val = eng.get_param(target, pname, nargout=1)
            print(f"  {pname} = {val}")

eng.close_system(model_name, 0, nargout=0)
eng.quit()
```

### 探索要点

- `find_system(model_name)` 返回所有 block 完整路径（如 `"motor_model/Subsystem/PID Controller"`）
- `get_param(block, "MaskType")` 识别 block 功能类型（PID、增益、传递函数等）
- `get_param(block, "DialogParameters")` 返回所有对话框参数名 — 这就是 `get_param` 的第二个参数来源
- 如用户不确定调哪个 block，展示扫描到的 PID block 列表让用户选择

## Step 1-B: 读取当前参数

确定目标 block 后，编写 `read_params.py` 读取当前参数值和仿真时长：

```python
import matlab.engine, json, os

eng = matlab.engine.start_matlab()
eng.load_system(r"<model_path>.slx", nargout=0)

model_name = os.path.splitext(os.path.basename(r"<model_path>.slx"))[0]
block_path = "<Step 1-A 探索到的完整路径>"
param_names = ["P", "I", "D", "N"]  # 根据实际 DialogParameters 调整

params = {}
for p in param_names:
    params[p] = float(eng.get_param(block_path, p, nargout=1))

sim_time = eng.get_param(model_name, "StopTime", nargout=1)

print(json.dumps({"params": params, "sim_time": sim_time}, indent=2))

eng.close_system(model_name, 0, nargout=0)
eng.quit()
```

## Step 1-C: 读取输入信号和输出配置

**必须**在仿真前读取以下信息，避免假设错误：

### 读取 Step 信号参数

```python
# Step block 的参数名是 Time, Before, After（不是 StepTime, InitialValue, FinalValue）
step_params = ["Time", "Before", "After"]
for p in step_params:
    val = eng.get_param("model_name/Step", p, nargout=1)
    print(f"Step/{p} = {val}")
```

> **注意：** Step block 的 `After` 参数就是设定值（setpoint），不要假设为 1。

### 读取 To Workspace 配置

```python
var_name = eng.get_param("model_name/To Workspace", "VariableName", nargout=1)
save_format = eng.get_param("model_name/To Workspace", "SaveFormat", nargout=1)
print(f"变量名: {var_name}, 格式: {save_format}")
```

### 读取被控对象参数

```python
# 传递函数
num = eng.get_param("model_name/Transfer Fcn", "Numerator", nargout=1)
den = eng.get_param("model_name/Transfer Fcn", "Denominator", nargout=1)
print(f"传递函数: {num} / {den}")
```

## Step 1-D: 仿真数据提取方式

`sim()` 函数返回 `Simulink.SimulationOutput` 对象，**不是**直接的数组。正确提取方式：

```python
# 运行仿真
sim_out = eng.sim(model_name, nargout=1)

# 提取时间数据
eng.workspace["simOut"] = sim_out
eng.eval("tout = simOut.tout;", nargout=0)
time_data = eng.eval("tout", nargout=1)

# 提取输出数据（Timeseries 格式）
eng.eval("simout = simOut.simout;", nargout=0)
eng.eval("yout = simout.Data;", nargout=0)
output_data = eng.eval("yout", nargout=1)
```

**常见格式：**
- `SaveFormat = "Timeseries"` → 用 `simOut.simout.Data` 提取
- `SaveFormat = "Structure"` → 用 `simOut.simout.signals.values` 提取
- `SaveFormat = "Array"` → 直接用 `simOut.simout` 提取

## Step 1-E: 记录模型结构

模型结构是后续调优的重要参考，**必须**在日志中完整记录。通过以下方式获取：

### 方式一：请用户上传截图（优先）

向用户请求：
> 请在 Simulink 编辑器中打开模型，截取模型窗口的截图发给我，我会将其记录到调参日志中。

截图应包含：
- 完整的信号流（从输入到输出）
- 所有 block 的名称
- 连线关系

### 方式二：请用户口头描述

如果用户不方便截图，询问：
> 请描述一下模型的信号流，比如：输入是什么 → 经过哪些环节 → 输出是什么？

### 方式三：AI 自动推断（补充）

根据 `find_system` 扫描结果和 block 类型，AI 自动推断信号流并用 ASCII 绘制：

```markdown
### 模型结构

```
Step(200) → [+] → PID Controller(P=10, I=1) → Transfer Fcn 1/(10s+1) → Scope
             ↑                                              |
             └──────────── unity feedback ──────────────────┘
```
```

### 记录内容

模型结构记录应包含：
1. **信号流图** — ASCII 或截图，展示从输入到输出的完整通路
2. **各 block 参数汇总表** — block 名称、类型、当前参数值
3. **反馈结构** — 是否有反馈、反馈类型（单位反馈/传感器模型等）
4. **被控对象特性** — 传递函数/状态空间、阶数、时间常数等物理意义（如已知）
"""

_REF_TUNING_STRATEGIES = """\
# Phase 3: 调参策略与 PID 经验

## 策略选择

根据参数空间大小和用户需求选择寻优策略：

| 模式 | 适用场景 | 说明 |
|------|----------|------|
| **AI 引导逐步调** | 参数少（≤3）、需理解系统响应 | 每轮 AI 根据上轮结果决定下轮参数 |
| **网格搜索** | 参数少、范围明确 | 遍历参数组合，找全局最优 |
| **AI + 网格混合** | 参数较多、需兼顾效率与覆盖 | AI 选区间，网格搜索区间内最优 |

> AI 引导模式使用 `script_template_ai_guided.md`，网格搜索使用 `script_template_grid_search.md`。

---

## PID 调参顺序

1. **先调 P（比例）**
   - 从小值开始逐步增大，观察系统响应
   - P 过小 → 响应慢、稳态误差大
   - P 过大 → 振荡、超调

2. **再调 I（积分）**
   - 消除稳态误差
   - I 过大 → 超调增加、振荡
   - 通常从 P/10 量级开始

3. **最后调 D（微分）**
   - 抑制超调、改善动态响应
   - D 过大 → 对噪声敏感、高频振荡
   - 通常从 P/100 量级开始

4. **滤波器系数 N**
   - 实际 PID 中 D 项通常串联一阶滤波器
   - N 典型值 10–100，越大 D 作用越强但对噪声越敏感

---

## 调整步长策略

| 阶段 | 步长范围 | 示例 |
|------|----------|------|
| 粗调（首轮） | 当前值的 50%–100% | P=1 → 尝试 0.5, 1, 2, 4 |
| 细调（二轮） | 当前值的 10%–30% | P=2 → 尝试 1.7, 2, 2.3 |
| 精调（末轮） | 当前值的 1%–5% | P=2.3 → 尝试 2.25, 2.3, 2.35 |

---

## 终止条件

满足任一即停止：
- 所有目标指标达标
- 连续 3 次调整无明显改善（ITAE 变化 < 1%）
- 达到用户设定的最大迭代次数
- 用户手动终止

## AI 引导收敛后的自动策略切换

当 AI 引导连续 3 轮无改善时，**自动切换到网格搜索**：

1. 以当前最优参数为中心
2. 在 ±30% 范围内生成网格（步长 10%）
3. 遍历所有组合，找全局最优
4. 如果网格搜索也未改善，终止寻优

**示例：** AI 引导收敛到 P=8.8, I=0.35
- 网格范围：P ∈ [6.2, 11.4]，I ∈ [0.25, 0.45]
- 步长：P=0.88，I=0.04
- 测试组合：约 25 组

---

## PID 经验速查表

| 场景 | P | I | D | 备注 |
|------|---|---|---|------|
| 快速响应，允许超调 | 大 | 中 | 小 | P 为主，D 抑制振荡 |
| 无超调要求 | 中 | 小 | 可不要 | 纯 PI 控制 |
| 高精度，抗扰动 | 中 | 中 | 中 | 经典 PID |
| 大惯量系统 | 小 | 大 | 小 | 积分主导 |
| 强噪声环境 | 中 | 中 | 小或不用 | D 放大噪声 |

---

## AI 在每轮循环中的角色

脚本循环运行结束后，AI 根据 `history` 输出分析趋势：

1. 观察各参数变化时指标的响应趋势
2. 如未达标，修改 `TUNING_CONFIG` 中的 `params` 和 `param_ranges`，生成新脚本继续寻优
3. 如已达标，进入 Phase 4 记录结果
"""

_REF_SCRIPT_TEMPLATE_AI_GUIDED = """\
# AI 引导逐步调 — 脚本模板

## 配置说明

```python
TUNING_CONFIG = {
    "model_path": r"C:/path/to/model.slx",
    "block_path": "model_name/PID Controller",
    "params": {"P": 1.0, "I": 0.1, "D": 0.01},
    "param_ranges": {          # 每个参数的搜索上下限
        "P": [0.1, 10.0],
        "I": [0.001, 1.0],
        "D": [0.0, 1.0],
    },
    "sim_time": "10",
    "setpoint": 1.0,
    "max_iterations": 20,
    "tolerance": {             # 达标阈值
        "rise_time": 0.5,      # s
        "overshoot": 5.0,      # %
        "settling_time": 2.0,  # s
        "steady_error": 0.02,
    },
}
```

AI 的操作方式：
1. 读取上轮 metrics 结果
2. 根据经验法则决定下轮参数（修改 `params` 字典）
3. 重新运行脚本

或者使用下方自动循环模式。

---

## 完整脚本

```python
import matlab.engine, json, numpy as np

# ========== 配置 ==========
TUNING_CONFIG = {
    "model_path": r"C:/path/to/model.slx",
    "block_path": "model_name/PID Controller",
    "params": {"P": 1.0, "I": 0.1, "D": 0.01},
    "param_ranges": {
        "P": [0.1, 10.0],
        "I": [0.001, 1.0],
        "D": [0.0, 1.0],
    },
    "sim_time": "10",
    "setpoint": 1.0,
    "max_iterations": 20,
    "tolerance": {
        "rise_time": 0.5,
        "overshoot": 5.0,
        "settling_time": 2.0,
        "steady_error": 0.02,
    },
}


def compute_metrics(time_arr, output_arr, setpoint):
    \"""从仿真数据计算时域指标\"""
    t = np.array(time_arr).flatten()
    y = np.array(output_arr).flatten()
    steady = y[-1]

    # 超调量
    peak = np.max(y)
    overshoot = max(0, (peak - steady) / setpoint * 100) if setpoint != 0 else 0

    # 上升时间 (10% → 90%)
    idx_10 = np.argmax(y >= 0.1 * setpoint)
    idx_90 = np.argmax(y >= 0.9 * setpoint)
    rise_time = t[idx_90] - t[idx_10] if idx_90 > idx_10 else float("inf")

    # 调节时间 (±2% 误差带)
    band = 0.02 * abs(setpoint)
    within_band = np.abs(y - setpoint) <= band
    settling_idx = len(t) - 1
    for i in range(len(t) - 1, -1, -1):
        if not within_band[i]:
            settling_idx = min(i + 1, len(t) - 1)
            break
    settling_time = t[settling_idx]

    # 稳态误差
    steady_error = abs(y[-1] - setpoint)

    # ITAE
    dt = np.diff(t)
    error = np.abs(y[:-1] - setpoint)
    itae = np.sum(t[:-1] * error * dt)

    return {
        "rise_time": round(rise_time, 4),
        "overshoot": round(overshoot, 4),
        "settling_time": round(settling_time, 4),
        "steady_error": round(steady_error, 6),
        "itae": round(itae, 6),
    }


def run_one_iteration(eng, config):
    \"""单次仿真：设参 → 仿真 → 提取数据 → 计算 metrics\"""
    bp = config["block_path"]
    for k, v in config["params"].items():
        eng.set_param(bp, k, str(v), nargout=0)

    model_name = config["block_path"].split("/")[0]
    eng.set_param(model_name, "StopTime", config["sim_time"], nargout=0)
    eng.sim(model_name)

    time_data = eng.workspace["tout"]
    output_data = eng.workspace["yout"]
    return compute_metrics(time_data, output_data, config["setpoint"])


# ========== 主循环 ==========
if __name__ == "__main__":
    cfg = TUNING_CONFIG
    eng = matlab.engine.start_matlab()
    eng.load_system(cfg["model_path"], nargout=0)

    history = []
    for i in range(cfg["max_iterations"]):
        metrics = run_one_iteration(eng, cfg)
        result = {"iteration": i + 1, "params": dict(cfg["params"]), "metrics": metrics}
        history.append(result)
        print(f"[迭代 {i+1}] {json.dumps(metrics, indent=2)}")

        tol = cfg["tolerance"]
        all_met = all([
            metrics["rise_time"] <= tol["rise_time"],
            metrics["overshoot"] <= tol["overshoot"],
            metrics["settling_time"] <= tol["settling_time"],
            metrics["steady_error"] <= tol["steady_error"],
        ])
        if all_met:
            print(f"✅ 全部指标达标，共 {i+1} 轮迭代")
            break
    else:
        print(f"⚠️ 达到最大迭代 {cfg['max_iterations']} 次，未完全达标")

    print("\\n=== 完整历史 ===")
    print(json.dumps(history, indent=2))

    model_name = cfg["block_path"].split("/")[0]
    eng.close_system(model_name, 0, nargout=0)
    eng.quit()
```

---

## 注意事项

- 模型中需配置 `To Workspace` block，变量名为 `tout`（时间）和 `yout`（输出）
- `set_param` 的 value 必须是**字符串**
- `model_name` 从 `block_path` 提取（第一个 `/` 之前的部分）
- `compute_metrics` 的详细说明参见 `performance_metrics.md`
"""

_REF_SCRIPT_TEMPLATE_GRID_SEARCH = """\
# 网格搜索 — 脚本模板

## 适用场景

参数范围已知、需全面扫描时使用。遍历参数组合，按 ITAE 排序找全局最优。

> 总组合数 = 各参数网格点数的乘积，注意控制规模。

---

## 完整脚本

```python
import matlab.engine, json, numpy as np
from itertools import product

GRID_CONFIG = {
    "model_path": r"C:/path/to/model.slx",
    "block_path": "model_name/PID Controller",
    "sim_time": "10",
    "setpoint": 1.0,
    # 每个参数的网格点
    "grid": {
        "P": np.linspace(0.5, 5.0, 10).tolist(),
        "I": np.linspace(0.01, 0.5, 8).tolist(),
        "D": [0.0, 0.01, 0.05, 0.1],
    },
}


def compute_metrics(time_arr, output_arr, setpoint):
    \"""从仿真数据计算时域指标 — 同 AI 引导模板中的实现\"""
    t = np.array(time_arr).flatten()
    y = np.array(output_arr).flatten()
    steady = y[-1]

    peak = np.max(y)
    overshoot = max(0, (peak - steady) / setpoint * 100) if setpoint != 0 else 0

    idx_10 = np.argmax(y >= 0.1 * setpoint)
    idx_90 = np.argmax(y >= 0.9 * setpoint)
    rise_time = t[idx_90] - t[idx_10] if idx_90 > idx_10 else float("inf")

    band = 0.02 * abs(setpoint)
    within_band = np.abs(y - setpoint) <= band
    settling_idx = len(t) - 1
    for i in range(len(t) - 1, -1, -1):
        if not within_band[i]:
            settling_idx = min(i + 1, len(t) - 1)
            break
    settling_time = t[settling_idx]

    steady_error = abs(y[-1] - setpoint)

    dt = np.diff(t)
    error = np.abs(y[:-1] - setpoint)
    itae = np.sum(t[:-1] * error * dt)

    return {
        "rise_time": round(rise_time, 4),
        "overshoot": round(overshoot, 4),
        "settling_time": round(settling_time, 4),
        "steady_error": round(steady_error, 6),
        "itae": round(itae, 6),
    }


if __name__ == "__main__":
    cfg = GRID_CONFIG
    eng = matlab.engine.start_matlab()
    eng.load_system(cfg["model_path"], nargout=0)

    param_names = list(cfg["grid"].keys())
    param_values = list(cfg["grid"].values())
    combinations = list(product(*param_values))

    print(f"总组合数: {len(combinations)}")

    results = []
    for idx, combo in enumerate(combinations):
        params = dict(zip(param_names, combo))
        bp = cfg["block_path"]
        for k, v in params.items():
            eng.set_param(bp, k, str(v), nargout=0)

        model_name = bp.split("/")[0]
        eng.sim(model_name)
        time_data = eng.workspace["tout"]
        output_data = eng.workspace["yout"]
        metrics = compute_metrics(time_data, output_data, cfg["setpoint"])

        results.append({"params": params, "metrics": metrics})

        if (idx + 1) % 10 == 0:
            print(f"进度: {idx+1}/{len(combinations)}")

    # 按 ITAE 排序，找最优
    results.sort(key=lambda r: r["metrics"]["itae"])
    print("\\n=== Top 5 参数组合（按 ITAE） ===")
    for r in results[:5]:
        print(json.dumps(r, indent=2))

    # 保存完整结果
    with open("grid_results.json", "w") as f:
        json.dump(results, f, indent=2, default=str)

    model_name = cfg["block_path"].split("/")[0]
    eng.close_system(model_name, 0, nargout=0)
    eng.quit()
```

---

## 注意事项

- 结果按 ITAE 排序，同时检查其他指标是否达标
- 完整结果保存为 `grid_results.json`，方便后续分析
- 模型中需配置 `To Workspace` block，变量名为 `tout` 和 `yout`
- `set_param` 的 value 必须是**字符串**
- `compute_metrics` 的详细说明参见 `performance_metrics.md`
"""

_REF_PERFORMANCE_METRICS = """\
# 性能指标定义与分析

## 时域性能指标

| 指标 | 英文名 | 说明 | 计算方法 |
|------|--------|------|----------|
| 上升时间 | Rise Time | 从 10% 到 90% 稳态值的时间 | `t_90 - t_10` |
| 超调量 | Overshoot | 峰值超出稳态值的百分比 | `(peak - steady) / steady * 100%` |
| 调节时间 | Settling Time | 进入 ±2% 误差带的时间 | 最后一次离开误差带的时刻 |
| 稳态误差 | Steady-state Error | 最终输出与目标值的偏差 | `|final_value - setpoint|` |
| ITAE | — | 时间×绝对误差积分，综合性能指标 | `∫t·|e(t)|dt` |

---

## 积分准则对比

| 准则 | 公式 | 特点 |
|------|------|------|
| IAE（绝对误差积分） | `∫|e(t)|dt` | 适合一般应用 |
| ISE（误差平方积分） | `∫e(t)²dt` | 惩罚大偏差 |
| ITAE（时间加权） | `∫t·|e(t)|dt` | 惩罚长时间偏差，综合最优 |

---

## compute_metrics 函数

两个脚本模板中共用的指标计算函数：

```python
def compute_metrics(time_arr, output_arr, setpoint):
    \"""从仿真数据计算时域指标\"""
    t = np.array(time_arr).flatten()
    y = np.array(output_arr).flatten()
    steady = y[-1]

    # 超调量
    peak = np.max(y)
    overshoot = max(0, (peak - steady) / setpoint * 100) if setpoint != 0 else 0

    # 上升时间 (10% → 90%)
    idx_10 = np.argmax(y >= 0.1 * setpoint)
    idx_90 = np.argmax(y >= 0.9 * setpoint)
    rise_time = t[idx_90] - t[idx_10] if idx_90 > idx_10 else float("inf")

    # 调节时间 (±2% 误差带)
    band = 0.02 * abs(setpoint)
    within_band = np.abs(y - setpoint) <= band
    settling_idx = len(t) - 1
    for i in range(len(t) - 1, -1, -1):
        if not within_band[i]:
            settling_idx = min(i + 1, len(t) - 1)
            break
    settling_time = t[settling_idx]

    # 稳态误差
    steady_error = abs(y[-1] - setpoint)

    # ITAE
    dt = np.diff(t)
    error = np.abs(y[:-1] - setpoint)
    itae = np.sum(t[:-1] * error * dt)

    return {
        "rise_time": round(rise_time, 4),
        "overshoot": round(overshoot, 4),
        "settling_time": round(settling_time, 4),
        "steady_error": round(steady_error, 6),
        "itae": round(itae, 6),
    }
```

---

## 结果分析要点

寻优脚本运行结束后，AI 从 `history` 输出中提取关键信息呈现给用户：

1. **汇总表** — 列出每轮迭代的参数值和对应指标，高亮最优轮次
2. **达标判定** — 逐项对比目标与最终结果，明确 ✅/❌
3. **趋势分析** — 参数变化对指标的影响趋势（如"P 从 1→3 时超调从 2%→12%，临界点在 P≈2.2"）
"""

_REF_TROUBLESHOOTING = """\
# 常见问题排查

## 仿真相关

| 问题 | 原因 | 解决方案 |
|------|------|----------|
| 仿真不收敛 | 步长过大、代数环 | 减小仿真步长，检查模型是否有代数环 |
| 参数设置无效 | 路径拼写错误、参数名大小写 | 确认 block 路径拼写正确，参数名大小写敏感 |
| 仿真结果异常 | To Workspace 未配置 | 检查模型中 To Workspace block 变量名和格式 |
| `simout.Time` 报错 "无法解析名称" | `sim()` 返回 `Simulink.SimulationOutput` 对象，不是直接的变量 | 用 `simOut = eng.sim(model, nargout=1)`，然后 `eng.workspace["simOut"] = simOut`，再 `eng.eval("simOut.tout")` 提取 |
| 工作区变量为空 | `sim()` 结果未赋值到工作区 | 仿真后需通过 `eng.eval("simOut = sim(model)")` 显式赋值 |
| numpy 导入失败 | 未安装 numpy | 运行 `uv pip install numpy` |
| Step block 参数名错误 | `StepTime`/`FinalValue` 不是实际参数名 | 用 `get_param(block, "DialogParameters")` 查询，实际参数名为 `Time`, `Before`, `After` |

## 连接相关

| 问题 | 原因 | 解决方案 |
|------|------|----------|
| MATLAB 连接超时 | MATLAB 未启动或 License 不可用 | 确认 MATLAB 已启动且 License 可用 |
| matlab.engine 导入失败 | Python/MATLAB 版本不匹配 | 参考 `environment_setup.md` 版本对照表，使用 Python 3.10 |
| 连接中断 | MATLAB 进程崩溃 | 尝试用 `connect_matlab()` 重连或重启引擎 |
| MATLAB 不在标准路径 | MATLAB 安装在非 `C:\\Program Files\\MATLAB` 路径 | 先用 `where matlab`（Windows）或 `which matlab`（Linux）查找，再用 `eng.eval("matlabroot")` 确认安装根目录 |

## 性能权衡

- **响应速度 vs 超调量**：通常互斥，需根据场景取舍
- **抗扰性 vs 稳定性**：高增益提高抗扰性但降低稳定裕度
- **积分准则选择**：不同准则对应不同优化目标（IAE/ISE/ITAE），参见 `performance_metrics.md`
"""

_REF_LOG_TEMPLATE = """\
# 调参日志模板

此文件为调参日志的标准格式。每次创建新的调参会话时，复制此模板并填充内容。

文件命名规则：`YYYY-MM-DD_<简短描述>.md`

---

# 调参日志：[模型名称] - [调参描述]

**日期：** YYYY-MM-DD
**模型路径：** `C:/path/to/model.slx`
**调参目标：** [用一句话描述本次调参的核心目标]

---

## 1. 环境信息

> 本节记录调参所用的完整运行环境，方便后续复现或在新环境中迁移。

| 项目 | 值 |
|------|-----|
| 操作系统 | [如 Windows 11 / Ubuntu 22.04] |
| Python 版本 | [如 3.10.11] |
| Python 路径 | [如 `C:/Users/xxx/.venv/Scripts/python.exe`] |
| MATLAB 版本 | [如 R2024a] |
| MATLAB 安装路径 | [如 `C:/Program Files/MATLAB/R2024a`] |
| matlab.engine 版本 | [如 2024.1] |
| 调参脚本路径 | [如 `C:/Users/xxx/Desktop/ai-tuner-demo/scripts/tune_motor.py`] |

**环境备注：** [如有特殊配置，如虚拟环境名称、PATH 设置、License 类型等]

---

## 2. 模型结构

> 本节记录被控系统的完整信号流和各 block 参数，是后续调优的核心参考。

### 信号流图

```
[输入信号] → [控制器] → [被控对象] → [输出]
    ↑                                   |
    └────────── [反馈通路] ─────────────┘
```

> 可使用 ASCII 绘制，或嵌入 Simulink 模型截图。

### Block 参数汇总

| Block 名称 | 类型 | 关键参数 |
|------------|------|----------|
| | | |
| | | |

### 被控对象特性

- **传递函数/状态空间：** [如 G(s) = 1/(10s+1)]
- **系统阶数：** [如 1 阶]
- **物理意义：** [如"一阶惯性环节，时间常数 10s"，如未知可不填]

---

## 3. 调参需求

### 目标指标

| 指标 | 目标值 | 备注 |
|------|--------|------|
| 上升时间 | < X s | |
| 超调量 | < X% | |
| 调节时间 | < X s | ±2% 误差带 |
| 稳态误差 | < X | |

### 模型信息

- **Simulink 模型：** `<model_name>.slx`（完整路径见顶部）
- **调参模块：** `<block_path>`（如 `motor_control/PID Controller`）
- **参数类型：** PID 控制器
- **测试信号：** 阶跃信号 / 斜坡信号 / 其他
- **仿真时长：** Xs
- **约束条件：** [如有]

---

## 4. 初始状态

| 参数 | 初始值 | 参数范围 |
|------|--------|----------|
| P | X.X | [min, max] |
| I | X.X | [min, max] |
| D | X.X | [min, max] |
| N | X.X | [min, max] |

### 初始仿真结果

| 指标 | 初始值 | 目标值 | 是否达标 |
|------|--------|--------|----------|
| 上升时间 | X s | < X s | ❌/✅ |
| 超调量 | X% | < X% | ❌/✅ |
| 调节时间 | X s | < X s | ❌/✅ |
| 稳态误差 | X | < X | ❌/✅ |

---

## 5. 调参过程

### 迭代 #1

**调整策略：** [如"增大 P 以加快响应"]

| 参数 | 调整前 | 调整后 | 变化量 |
|------|--------|--------|--------|
| P | X | Y | +Z% |
| I | X | Y | +Z% |
| D | X | Y | +Z% |

**仿真结果：**

| 指标 | 数值 | 目标值 | 是否达标 |
|------|------|--------|----------|
| 上升时间 | X s | < X s | ❌/✅ |
| 超调量 | X% | < X% | ❌/✅ |
| 调节时间 | X s | < X s | ❌/✅ |
| 稳态误差 | X | < X | ❌/✅ |

**小结：** [一句话总结本次调整的效果，如"P 增大后上升时间改善 30%，但超调从 5% 增加到 15%，下轮需加入 D 抑制超调"]

---

### 迭代 #2

**调整策略：** [...]

| 参数 | 调整前 | 调整后 | 变化量 |
|------|--------|--------|--------|
| P | | | |
| I | | | |
| D | | | |

**仿真结果：**

| 指标 | 数值 | 目标值 | 是否达标 |
|------|------|--------|----------|
| 上升时间 | | | |
| 超调量 | | | |
| 调节时间 | | | |
| 稳态误差 | | | |

**小结：** [...]

---

<!-- 按需追加更多迭代 -->

---

## 6. 最终结果

### 最优参数

| 参数 | 最终值 | 初始值 | 变化 |
|------|--------|--------|------|
| P | | | |
| I | | | |
| D | | | |

### 性能对比

| 指标 | 初始值 | 最终值 | 目标值 | 达标 |
|------|--------|--------|--------|------|
| 上升时间 | | | | |
| 超调量 | | | | |
| 调节时间 | | | | |
| 稳态误差 | | | | |

### 总调参轮次：X 轮

---

## 7. 总结

**最终评价：** [对调参结果的总体评价]

**关键发现：**
- [发现 1：如"系统对 P 极其敏感，步长需控制在 10% 以内"]
- [发现 2：如"D 项超过 0.5 后引入高频振荡，推测存在测量噪声"]
- [...]

**后续建议：**
- [建议 1：如"可考虑加入前馈补偿进一步减小超调"]
- [建议 2：如"建议在不同负载条件下验证当前参数的鲁棒性"]
- [...]
"""

_REF_MATLAB_API_REFERENCE = """\
# MATLAB Engine API for Python 常用参考

## 连接与生命周期

```python
import matlab.engine

# 启动 MATLAB 引擎（首次较慢，约 10-30s）
eng = matlab.engine.start_matlab()

# 连接到已运行的 MATLAB 实例（更快）
eng = matlab.engine.connect_matlab()

# 退出
eng.quit()
```

## Simulink 模型操作

```python
# 加载模型（不打开 GUI）
eng.load_system(r"C:/path/to/model.slx", nargout=0)

# 打开模型（带 GUI）
eng.open_system(r"C:/path/to/model.slx", nargout=0)

# 关闭模型（0 = 不保存）
eng.close_system("model_name", 0, nargout=0)

# 保存模型
eng.save_system("model_name", nargout=0)
```

## 参数读写

```python
# 读取参数（返回 Python 类型）
p_val = eng.get_param("model_name/PID Controller", "P")

# 设置参数（value 必须是字符串）
eng.set_param("model_name/PID Controller", "P", "2.5", nargout=0)
eng.set_param("model_name/PID Controller", "I", "0.3", nargout=0)
eng.set_param("model_name/PID Controller", "D", "0.01", nargout=0)

# 批量设置
params = {"P": "2.5", "I": "0.3", "D": "0.01"}
for k, v in params.items():
    eng.set_param(f"model_name/{block}", k, v, nargout=0)
```

**注意：** `set_param` 的 value 参数必须是**字符串**，即使传入的是数值。

## 运行仿真

```python
# 方式 1：简单仿真
result = eng.sim("model_name", "10")  # 第二个参数是仿真时长字符串

# 方式 2：带配置仿真
eng.set_param("model_name", "StopTime", "10", nargout=0)
result = eng.sim("model_name")

# 方式 3：通过 eval 调用 sim 命令（更灵活）
eng.eval("simOut = sim('model_name', 'StopTime', '10');", nargout=0)
```

## 提取仿真数据

```python
# 方式 1：通过 SimulationOutput 对象
# 模型中需配置 To Workspace block，变量名如 "tout", "yout"
time_data = eng.workspace["tout"]
output_data = eng.workspace["yout"]

# 方式 2：通过 eval
eng.eval("t = simOut.tout;", nargout=0)
eng.eval("y = simOut.yout;", nargout=0)
t = eng.workspace["t"]
y = eng.workspace["y"]

# 转换为 numpy 数组（方便 Python 计算）
import numpy as np
t_np = np.array(t).flatten()
y_np = np.array(y).flatten()
```

## 常用 PID Block 参数名

| 参数名 | 含义 | 类型 |
|--------|------|------|
| `P` | 比例增益 | float 字符串 |
| `I` | 积分增益 | float 字符串 |
| `D` | 微分增益 | float 字符串 |
| `N` | 滤波器系数 | float 字符串 |
| `Tf` | 滤波时间常数 | float 字符串 |
| `Controller` | 控制器类型 | "PID"/"PI"/"PD"/"P"/"I" |
| `Form` | PID 形式 | "Parallel"/"Ideal" |
| `TimeDomain` | 时域类型 | "continuous-time"/"discrete-time" |
| `FilterCoefficient` | 滤波器系数 N | float 字符串 |

**不同 MATLAB 版本的 PID Block 参数名可能略有差异。** 如果 `get_param` 报错参数不存在，用以下方式列出所有参数：

```python
# 列出 block 的所有参数
all_params = eng.get_param("model_name/PID Controller", "DialogParameters")
# 或查看 block 类型
block_type = eng.get_param("model_name/PID Controller", "BlockType")
```

## nargout 说明

MATLAB 函数有多个返回值，Python 调用时需通过 `nargout` 指定接收几个返回值：

```python
# 无返回值的函数（如 load_system, set_param）
eng.load_system("model", nargout=0)

# 1 个返回值
ver = eng.version(nargout=1)
# 等价于
ver = eng.version()

# 多个返回值
t, y = eng.sim("model", nargout=2)  # 如果 sim 有多个输出
```

## 常见错误处理

```python
import matlab.engine

try:
    eng = matlab.engine.start_matlab()
    eng.load_system(r"C:/path/to/model.slx", nargout=0)
except matlab.engine.MatlabExecutionError as e:
    print(f"MATLAB 执行错误: {e}")
except ConnectionAbortedError:
    print("MATLAB 连接中断，尝试重启引擎")
    eng = matlab.engine.start_matlab()
except Exception as e:
    print(f"未知错误: {e}")
finally:
    try:
        eng.quit()
    except:
        pass
```

## 性能提示

- `start_matlab()` 首次启动约 10-30 秒，尽量复用同一引擎实例
- `connect_matlab()` 连接已有实例约 1-3 秒
- 批量参数设置比逐个设置更快
- 大量数据传输时，考虑用 `eng.eval()` 在 MATLAB 端预处理，只传回结果
"""

# ── Virtual files dict ──────────────────────────────────────

_VIRTUAL_FILES: dict[str, str] = {
    "vfs://simulink-tuner/references/environment_setup.md": _REF_ENVIRONMENT_SETUP,
    "vfs://simulink-tuner/references/model_exploration.md": _REF_MODEL_EXPLORATION,
    "vfs://simulink-tuner/references/tuning_strategies.md": _REF_TUNING_STRATEGIES,
    "vfs://simulink-tuner/references/script_template_ai_guided.md": _REF_SCRIPT_TEMPLATE_AI_GUIDED,
    "vfs://simulink-tuner/references/script_template_grid_search.md": _REF_SCRIPT_TEMPLATE_GRID_SEARCH,
    "vfs://simulink-tuner/references/performance_metrics.md": _REF_PERFORMANCE_METRICS,
    "vfs://simulink-tuner/references/troubleshooting.md": _REF_TROUBLESHOOTING,
    "vfs://simulink-tuner/references/log_template.md": _REF_LOG_TEMPLATE,
    "vfs://simulink-tuner/references/matlab_api_reference.md": _REF_MATLAB_API_REFERENCE,
}


def SimulinkTunerSkill() -> Skill:
    """MATLAB Simulink PID parameter tuning via matlab.engine."""
    return Skill.builtin(
        name="simulink-tuner",
        description=(
            "MATLAB Simulink PID 参数调优技能。此技能通过 Python 脚本驱动 MATLAB Engine API 与 Simulink 模型交互，"
            "实现 AI 辅助的控制器参数整定。适用于 PID 增益调整、控制器参数优化等场景。"
        ),
        content=_SKILL_CONTENT,
        virtual_files=_VIRTUAL_FILES,
    )
