"""Built-in skill: Amesim Tuner — Simcenter Amesim parameter tuning via ame_apy API.

Source: ai-tuner-amesim/.claude/skills/amesim-tuner/
"""

from __future__ import annotations

from mocode.core.skill import Skill

# ── Skill body ──────────────────────────────────────────────

_SKILL_CONTENT = """\
# Amesim AI 调参技能

## 概述

通过编写 Python 脚本调用 `ame_apy` 模块连接 Simcenter Amesim，实现 AI 驱动的参数整定闭环：
**设定目标 → 读取参数 → AI 建议调整 → 执行仿真 → 分析结果 → 记录文档**。

所有调参过程统一用 Markdown 文档管理，保存在**用户项目根目录**的 `tuning_logs/` 目录下。

> **重要：** `tuning_logs/` 必须创建在用户的项目根目录（即用户的工作目录），**不是**本 skill 所在的 `.claude/skills/amesim-tuner/` 目录下。执行前确认当前工作目录是否为用户项目根目录。

---

## 调参脚本规范

所有 Python 脚本遵循以下规范：

1. **文件命名**：`tune_<model_name>_<timestamp>.py` 或功能脚本如 `explore_model.py`、`read_params.py`
2. **参数硬编码在字典中**，方便 AI 修改：
   ```python
   TUNING_CONFIG = {
       "model_path": r"C:/path/to/model.ame",
       "circuit_name": "model_name",
       "components": {
           "ORIFICE1": {
               "submodel": "area of orifice",
               "param": "area at 0",
               "value": "2e-5",
           },
       },
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
   - 环境信息（Phase 0）→ 如果已有且 Amesim 版本/路径一致，**跳过 Phase 0**
   - 模型探索结果（Phase 1）→ 如果已有且模型路径一致，**跳过 Phase 1**
   - 调参需求（Phase 2）→ 如果已有，向用户确认是否沿用或修改
   - 迭代记录（Phase 3）→ 如果已有，展示历史结果，询问是继续优化还是重新开始

4. **检查虚拟环境是否已就绪**：
   - `.venv/` 目录是否存在
   - `ame_apy` 是否可导入（通过 `sys.path` 配置）
   - `numpy` 是否已安装
   - 如果全部就绪，**跳过 Phase 0 的环境安装步骤**，仅采集环境信息

**跳过规则：**
- 已完成的 Phase 直接跳过，不重复执行
- 日志中记录了失败原因的 Phase 需要重新执行
- 用户明确要求重新开始时，不跳过任何 Phase

---

### Phase 0: 环境准备

> **开始前阅读：** `vfs://amesim-tuner/references/environment_setup.md`

确认 Python 虚拟环境已创建并激活，配置 Amesim API 路径，安装 `numpy`，验证 `ame_apy` 连接可用，采集环境信息。

**前置条件：** 用户已安装 Simcenter Amesim 且具备有效的 Runtime 许可证。如未安装或版本不兼容，立即告知用户终止。

**安装步骤（按顺序）：**
1. `uv venv` — 创建虚拟环境（Python 版本必须与 Amesim 匹配）
2. 激活虚拟环境
3. 配置 `sys.path` 和 `os.add_dll_directory`（指向 Amesim 安装目录）
4. `uv pip install numpy scipy matplotlib` — 数据处理与可视化必需
5. 验证 `ame_apy` 连接
6. 采集环境信息

**完成后立即写入日志：** 将环境信息追加到日志文件的"环境信息"章节。

### Phase 1: 模型探索

> **开始前阅读：** `vfs://amesim-tuner/references/model_exploration.md`

1. **加载模型并探索结构** — 运行 `explore_model.py` 加载 `.ame` 模型，列出全局参数列表
2. **读取当前参数** — 运行 `read_params.py` 读取目标组件的当前参数值
3. **读取仿真运行参数** — 读取仿真时长（Final time）、输出间隔（Print interval）等
4. **读取输出信号** — 确认可观测的输出信号路径（`component_name.signal_name`）
5. **记录模型结构** — 通过以下方式获取模型信息，写入日志：
   - **优先：** 请用户上传 Amesim 模型截图（在 Amesim 草图模式截取模型窗口）
   - **备选：** 请用户口头描述模型结构（如"油箱 → 泵 → 阀 → 缸 → 负载"）
   - **补充：** 根据 `amereadgp` 扫描结果，AI 自动推断并用 ASCII 绘制简化模型结构图

   记录格式示例：
   ```markdown
   ### 模型结构

   ```
   Tank → Pump(Flow) → Orifice(A=2e-5) → Cylinder → Load
                                                ↓
                                           Pressure Sensor
   ```

   **截图：** （如有，嵌入图片链接或文字说明）
   ```

**关键注意事项：**
- `AMELoadModel()` 加载前确保模型**未在 Amesim GUI 中打开**
- `AMEGetVariableValue()` 返回标量或列表，取决于信号类型
- 参数值始终以**字符串**传递，使用 SI 单位（Pa, m, m³, K 等）
- `AMERunSimulation()` 是同步阻塞调用

**完成后立即写入日志：** 将模型结构、当前参数、全局参数列表追加到日志文件。

### Phase 2: 确认调参目标

向用户确认以下信息：

- **调参目标**：根据模型类型确认（如压力 < X Pa、流量 > X m³/s、温度 < X K、超调量 < X% 等）
- **调参组件**：从 Phase 1 扫描结果中选择
- **调参参数**：选择要调整的参数（组件参数 / 全局参数）
- **参数范围**：每个参数的搜索上下限
- **输入信号/工况**：测试工况描述（如入口压力、负载力等）
- **仿真时长**：默认根据系统特性建议
- **约束条件**：物理约束、安全约束等
- **最大迭代次数**：默认 20

**完成后立即写入日志：** 创建日志文件 `tuning_logs/YYYY-MM-DD_<描述>.md`，写入环境信息（Phase 0）和调参需求。

### Phase 3: 循环参数寻优

> **开始前阅读：** `vfs://amesim-tuner/references/tuning_strategies.md`，然后根据选定策略阅读 `vfs://amesim-tuner/references/script_template_ai_guided.md` 或 `vfs://amesim-tuner/references/script_template_grid_search.md`

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

| 参数 | 组件 | 参数路径 | 值 |
|------|------|----------|-----|
| area at 0 | ORIFICE1 | area of orifice | X.Xe-X |
| pressure | PSRC1 | pressure at port 1 | XXXXX |

| 指标 | 数值 | 目标值 | 达标 |
|------|------|--------|------|
| 最大压力 | X Pa | < X Pa | ✅/❌ |
| 稳态流量 | X m³/s | > X m³/s | ✅/❌ |
| 响应时间 | X s | < X s | ✅/❌ |

**小结：** [一句话总结本轮效果]
```

**AI 引导收敛后自动切换策略：** 如果 AI 引导连续 3 轮无改善（目标指标变化 < 1%），自动切换到网格搜索模式，在当前最优参数附近进行细粒度搜索。

### Phase 4: 结果分析

> **开始前阅读：** `vfs://amesim-tuner/references/performance_metrics.md`

从寻优脚本输出中提取关键信息：
1. **汇总表** — 每轮迭代的参数值和指标
2. **达标判定** — 逐项对比目标与最终结果
3. **趋势分析** — 参数变化对指标的影响

**完成后立即写入日志：** 将结果分析章节追加到日志文件。

### Phase 5: 最终记录与模型更新

> **开始前阅读：** `vfs://amesim-tuner/references/log_template.md`

1. **补全日志** — 确保日志文件包含所有章节（环境信息、调参需求、初始状态、每轮迭代、最终结果、总结）
2. **写入总结** — 包含关键发现和后续建议
3. **询问用户** — 是否将最优参数写入模型并保存（调用 `AMESaveModel()`）
4. **输出文件清单** — 列出所有生成的文件（日志、脚本、结果 JSON）

> 如果寻优中途失败或中断，也要记录已完成迭代和失败原因。此时日志中已有实时追加的记录，只需补全总结部分即可。

---

## 参考文件索引

| 文件 | 用途 |
|------|------|
| `vfs://amesim-tuner/references/environment_setup.md` | Phase 0 环境准备详细步骤 |
| `vfs://amesim-tuner/references/model_exploration.md` | Phase 1 模型探索与参数读取 |
| `vfs://amesim-tuner/references/tuning_strategies.md` | Phase 3 调参策略选择与通用经验 |
| `vfs://amesim-tuner/references/script_template_ai_guided.md` | AI 引导逐步调脚本模板 |
| `vfs://amesim-tuner/references/script_template_grid_search.md` | 网格搜索脚本模板 |
| `vfs://amesim-tuner/references/performance_metrics.md` | Phase 4 性能指标定义与分析 |
| `vfs://amesim-tuner/references/troubleshooting.md` | 常见问题排查 |
| `vfs://amesim-tuner/references/log_template.md` | 调参日志模板 |
| `vfs://amesim-tuner/references/amesim_api_reference.md` | ame_apy API 参考 |
"""

# ── Reference files ─────────────────────────────────────────

_REF_ENVIRONMENT_SETUP = """\
# Phase 0: 环境准备

每次调参前确认运行环境可用。

## 1. 创建虚拟环境

使用 `uv` 创建虚拟环境，Python 版本必须与所安装的 Amesim 匹配：

```bash
uv init
uv sync
```

> 查阅 Amesim 官方发行说明确定兼容的 Python 版本。通常 `AME_ROOT/sys/python/` 下存在版本号子目录，以此为准。Amesim 与 Python 必须同为 64 位。

## 2. 激活虚拟环境

后续所有操作都在虚拟环境中执行：

```bash
# Git Bash
source .venv/Scripts/activate

# CMD / PowerShell
.venv\\Scripts\\activate
```

激活后确认：`python --version` 应显示与 Amesim 匹配的版本。

## 3. 安装第三方包

仿真数据处理与可视化需要以下包：

```bash
uv pip install numpy scipy matplotlib
```

> **注意：** `ame_apy` 本身不能通过 pip 安装，必须通过手动路径配置导入。

## 4. 配置 Amesim API 路径

在脚本开头完成路径设置（**必须在导入 `ame_apy` 之前**）：

```python
import os
import sys

# ==================================================
# 1. 定义 Amesim 安装根目录（务必根据实际情况修改）
# ==================================================
AME_ROOT = r"C:\\Program Files\\Simcenter\\2310\\Amesim"

# 2. 将 API 路径加入 sys.path
API_PYTHON_DIR = os.path.join(AME_ROOT, "scripting", "python")
sys.path.append(API_PYTHON_DIR)

# 3. 将 Amesim 可执行文件与 DLL 所在目录添加到 DLL 搜索路径 (Windows)
os.add_dll_directory(os.path.join(AME_ROOT, "win64"))

# 4. 某些版本还需要额外的 DLL 目录
os.add_dll_directory(os.path.join(AME_ROOT, "sys", "python", "win64"))
```

## 5. 验证连接

运行最小测试脚本：

```python
import os, sys

AME_ROOT = r"C:\\Program Files\\Simcenter\\2310\\Amesim"
sys.path.append(os.path.join(AME_ROOT, "scripting", "python"))
os.add_dll_directory(os.path.join(AME_ROOT, "win64"))
os.add_dll_directory(os.path.join(AME_ROOT, "sys", "python", "win64"))

import ame_apy

ame_apy.AMEInitAPI()
print(f"API 版本: {ame_apy.AMEGetAPIVersion()}")
ame_apy.AMECloseAPI()
```

运行成功则环境就绪。

## 6. 采集环境信息

验证连接成功后，立即采集以下信息写入调参日志（对应 `log_template.md` 第 1 节）：

```python
import os, sys, platform

env_info = {
    "os":          platform.platform(),
    "python_ver":  sys.version,
    "python_path": sys.executable,
    "ame_root":    AME_ROOT,
    "api_version": ame_apy.AMEGetAPIVersion(),
}
print(env_info)
```

> **前置条件：** 用户已安装 Simcenter Amesim 且具备有效的 Runtime 许可证。如未安装或版本不兼容，立即告知用户终止。
"""

_REF_MODEL_EXPLORATION = """\
# Phase 1: 模型探索与参数读取

本阶段分两步：先加载模型探索结构并读取当前参数，再向用户确认调参目标。

## Step 1-A: 加载模型并探索结构

编写 `explore_model.py` 加载 `.ame` 模型，读取全局参数列表：

```python
import os, sys, json

# ---------- 路径配置 ----------
AME_ROOT = r"C:\\Program Files\\Simcenter\\2310\\Amesim"
sys.path.append(os.path.join(AME_ROOT, "scripting", "python"))
os.add_dll_directory(os.path.join(AME_ROOT, "win64"))
os.add_dll_directory(os.path.join(AME_ROOT, "sys", "python", "win64"))

import ame_apy

def explore_model(model_path):
    \"""加载模型并探索结构\"""
    ame_apy.AMEInitAPI()

    try:
        # 加载模型
        ame_apy.AMELoadModel(model_path)
        model_name = os.path.splitext(os.path.basename(model_path))[0]
        print(f"模型名称: {model_name}")

        # 读取全局参数列表
        gp_list, status = ame_apy.amereadgp(model_name)
        print(f"\\n全局参数数量: {len(gp_list)}")
        print("\\n全局参数列表:")
        for gp in gp_list:
            print(f"  name={gp.get('name')}, value={gp.get('value')}, unit={gp.get('unit', '')}")

        # 保存为 JSON 方便后续处理
        with open("global_params.json", "w", encoding="utf-8") as f:
            json.dump(gp_list, f, indent=2, ensure_ascii=False)
        print(f"\\n全局参数已保存到 global_params.json")

        return model_name, gp_list

    finally:
        ame_apy.AMECloseModel()
        ame_apy.AMECloseAPI()


if __name__ == "__main__":
    model_path = r"C:/path/to/model.ame"
    explore_model(model_path)
```

### 探索要点

- `amereadgp(model_name)` 返回全局参数字典列表，每个字典包含 `name`、`value`、`unit` 等字段
- 全局参数是 Amesim 模型中所有可调参数的汇总，是最主要的调参入口
- 组件级参数通过 `AMESetParameterValue(circuit, component, submodel, param, value)` 设置
- 如用户不确定调哪个参数，展示全局参数列表让用户选择

## Step 1-B: 读取当前参数

确定目标参数后，编写 `read_params.py` 读取当前值：

```python
import os, sys, json

AME_ROOT = r"C:\\Program Files\\Simcenter\\2310\\Amesim"
sys.path.append(os.path.join(AME_ROOT, "scripting", "python"))
os.add_dll_directory(os.path.join(AME_ROOT, "win64"))
os.add_dll_directory(os.path.join(AME_ROOT, "sys", "python", "win64"))

import ame_apy

def read_params(model_path):
    \"""读取模型当前参数\"""
    ame_apy.AMEInitAPI()

    try:
        ame_apy.AMELoadModel(model_path)
        model_name = os.path.splitext(os.path.basename(model_path))[0]

        # 读取全局参数
        gp_list, status = ame_apy.amereadgp(model_name)

        # 过滤出用户关心的参数（根据实际需要调整关键字）
        target_keywords = ["pressure", "flow", "area", "diameter", "gain", "Kp", "Ki", "Kd"]
        target_params = []
        for gp in gp_list:
            name_lower = gp.get("name", "").lower()
            if any(kw.lower() in name_lower for kw in target_keywords):
                target_params.append(gp)

        print("目标参数:")
        for p in target_params:
            print(f"  {p['name']} = {p['value']} {p.get('unit', '')}")

        print(json.dumps(target_params, indent=2, ensure_ascii=False))
        return target_params

    finally:
        ame_apy.AMECloseModel()
        ame_apy.AMECloseAPI()


if __name__ == "__main__":
    model_path = r"C:/path/to/model.ame"
    read_params(model_path)
```

## Step 1-C: 读取仿真运行参数

**必须**在仿真前读取以下信息，避免假设错误：

```python
# 仿真运行参数通过 AMESetRunParameter 设置，常用参数名：
# - "Final time"    : 仿真结束时间
# - "Print interval" : 输出间隔
```

> **注意：** Amesim 的仿真运行参数在模型文件中已预设，通常不需要额外读取。如需修改，在脚本中通过 `AMESetRunParameter` 设置。

## Step 1-D: 结果获取方式

`AMEGetVariableValue()` 返回指定信号的值。信号路径格式为 `"component_name.signal_name"`：

```python
# 运行仿真
ame_apy.AMERunSimulation()

# 获取结果（标量或列表）
pressure = ame_apy.AMEGetVariableValue("ORIFICE1.pressure at port 1")
flow = ame_apy.AMEGetVariableValue("ORIFICE1.flow rate")
```

**注意：**
- `AMEGetVariableValue` 返回的可能是标量（最终值）或列表（时间序列），取决于 API 版本和信号类型
- 如需完整时间序列，可能需要使用 `AMEGetVariableValues`（具体取决于 API 版本）
- 所有参数值和结果单位均为 SI 基本单位（Pa, m, m³, K, kg/s 等）

## Step 1-E: 记录模型结构

模型结构是后续调优的重要参考，**必须**在日志中完整记录。通过以下方式获取：

### 方式一：请用户上传截图（优先）

向用户请求：
> 请在 Amesim 草图模式中打开模型，截取模型窗口的截图发给我，我会将其记录到调参日志中。

截图应包含：
- 完整的物理系统结构（组件连接关系）
- 所有组件的名称
- 端口连接关系

### 方式二：请用户口头描述

如果用户不方便截图，询问：
> 请描述一下模型的物理系统结构，比如：流体从哪里出发 → 经过哪些组件 → 输出到哪里？

### 方式三：AI 自动推断（补充）

根据全局参数列表中的组件名和参数名，AI 自动推断系统结构并用 ASCII 绘制：

```markdown
### 模型结构

```
Tank → Pump(Flow=X) → Orifice(A=Xe-X) → Cylinder → Load
                                              ↓
                                         Pressure Sensor
```
```

### 记录内容

模型结构记录应包含：
1. **系统结构图** — ASCII 或截图，展示物理系统的完整通路
2. **各组件参数汇总表** — 组件名、子模型、当前参数值
3. **物理约束** — 如已知的系统约束条件
4. **系统特性** — 液压/热/机械等领域的系统特征（如已知）
"""

_REF_TUNING_STRATEGIES = """\
# Phase 3: 调参策略与通用经验

## 策略选择

根据参数空间大小和用户需求选择寻优策略：

| 模式 | 适用场景 | 说明 |
|------|----------|------|
| **AI 引导逐步调** | 参数少（≤3）、需理解系统响应 | 每轮 AI 根据上轮结果决定下轮参数 |
| **网格搜索** | 参数少、范围明确 | 遍历参数组合，找全局最优 |
| **AI + 网格混合** | 参数较多、需兼顾效率与覆盖 | AI 选区间，网格搜索区间内最优 |

> AI 引导模式使用 `script_template_ai_guided.md`，网格搜索使用 `script_template_grid_search.md`。

---

## 通用调参顺序

1. **先调主导参数**（对系统响应影响最大的参数）
   - 如液压系统中的节流面积、泵排量
   - 如控制系统中的比例增益
   - 从小值开始逐步增大，观察系统响应

2. **再调辅助参数**（改善稳态或动态特性）
   - 如泄漏系数、摩擦系数
   - 如积分时间常数

3. **最后调微调参数**（精细优化）
   - 如滤波系数、死区参数

---

## 调整步长策略

| 阶段 | 步长范围 | 示例 |
|------|----------|------|
| 粗调（首轮） | 当前值的 50%–100% | A=2e-5 → 尝试 1e-5, 2e-5, 4e-5 |
| 细调（二轮） | 当前值的 10%–30% | A=4e-5 → 尝试 3.5e-5, 4e-5, 4.5e-5 |
| 精调（末轮） | 当前值的 1%–5% | A=4.2e-5 → 尝试 4.15e-5, 4.2e-5, 4.25e-5 |

---

## 终止条件

满足任一即停止：
- 所有目标指标达标
- 连续 3 次调整无明显改善（目标指标变化 < 1%）
- 达到用户设定的最大迭代次数
- 用户手动终止

## AI 引导收敛后的自动策略切换

当 AI 引导连续 3 轮无改善时，**自动切换到网格搜索**：

1. 以当前最优参数为中心
2. 在 ±30% 范围内生成网格（步长 10%）
3. 遍历所有组合，找全局最优
4. 如果网格搜索也未改善，终止寻优

**示例：** AI 引导收敛到 area=4.2e-5, pressure=200000
- 网格范围：area ∈ [2.94e-5, 5.46e-5]，pressure ∈ [140000, 260000]
- 步长：area=4.2e-6，pressure=20000
- 测试组合：约 25 组

---

## 领域经验速查表

### 液压系统

| 场景 | 关键参数 | 调整方向 | 备注 |
|------|----------|----------|------|
| 流量不足 | 节流面积 / 泵排量 | 增大 | 注意最大压力限制 |
| 压力过高 | 泄漏系数 / 安全阀压力 | 增大泄漏 / 降低阀值 | 安全第一 |
| 响应慢 | 管路直径 / 阀芯行程 | 增大 | 注意动态稳定性 |
| 振荡/不稳定 | 阻尼系数 / 摩擦系数 | 增大 | 可能存在代数环 |

### 热管理系统

| 场景 | 关键参数 | 调整方向 | 备注 |
|------|----------|----------|------|
| 温度过高 | 换热面积 / 流量 | 增大 | 受物理空间限制 |
| 温度波动 | 热容 / 控制增益 | 增大热容 / 减小增益 | 稳定性优先 |
| 响应慢 | 流量 / 换热系数 | 增大 | 注意压降 |

### 控制系统（PID 等）

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
2. 如未达标，修改 `TUNING_CONFIG` 中的参数和范围，生成新脚本继续寻优
3. 如已达标，进入 Phase 4 记录结果
"""

_REF_SCRIPT_TEMPLATE_AI_GUIDED = """\
# AI 引导逐步调 — 脚本模板

## 配置说明

```python
TUNING_CONFIG = {
    "model_path": r"C:/path/to/model.ame",
    "circuit_name": "model_name",           # 模型名（不带扩展名）
    "components": {                          # 要调的组件参数
        "ORIFICE1": {
            "submodel": "area of orifice",
            "param": "area at 0",
            "value": "2e-5",
        },
        "PSRC1": {
            "submodel": "pressure at port 1",
            "param": "pressure",
            "value": "200000",
        },
    },
    "param_ranges": {                        # 每个参数的搜索上下限
        "ORIFICE1.area at 0": [1e-6, 1e-4],
        "PSRC1.pressure": [100000, 500000],
    },
    "sim_time": "10.0",                      # 仿真结束时间
    "output_signals": {                      # 输出信号路径
        "pressure": "ORIFICE1.pressure at port 1",
        "flow": "ORIFICE1.flow rate",
    },
    "target": {                              # 目标指标
        "pressure_max": 300000,              # 最大压力 < 300000 Pa
        "flow_min": 0.001,                   # 最小流量 > 0.001 m³/s
    },
    "max_iterations": 20,
}
```

AI 的操作方式：
1. 读取上轮 metrics 结果
2. 根据经验法则决定下轮参数（修改 `components` 字典中的 `value`）
3. 重新运行脚本

或者使用下方自动循环模式。

---

## 完整脚本

```python
import os, sys, json, numpy as np

# ========== 路径配置 ==========
AME_ROOT = r"C:\\Program Files\\Simcenter\\2310\\Amesim"
sys.path.append(os.path.join(AME_ROOT, "scripting", "python"))
os.add_dll_directory(os.path.join(AME_ROOT, "win64"))
os.add_dll_directory(os.path.join(AME_ROOT, "sys", "python", "win64"))

import ame_apy

# ========== 配置 ==========
TUNING_CONFIG = {
    "model_path": r"C:/path/to/model.ame",
    "circuit_name": "model_name",
    "components": {
        "ORIFICE1": {
            "submodel": "area of orifice",
            "param": "area at 0",
            "value": "2e-5",
        },
        "PSRC1": {
            "submodel": "pressure at port 1",
            "param": "pressure",
            "value": "200000",
        },
    },
    "param_ranges": {
        "ORIFICE1.area at 0": [1e-6, 1e-4],
        "PSRC1.pressure": [100000, 500000],
    },
    "sim_time": "10.0",
    "output_signals": {
        "pressure": "ORIFICE1.pressure at port 1",
        "flow": "ORIFICE1.flow rate",
    },
    "target": {
        "pressure_max": 300000,
        "flow_min": 0.001,
    },
    "max_iterations": 20,
}


def set_params(config):
    \"""根据配置设置模型参数\"""
    circuit = config["circuit_name"]
    for comp_name, comp_info in config["components"].items():
        ame_apy.AMESetParameterValue(
            circuit,
            comp_name,
            comp_info["submodel"],
            comp_info["param"],
            str(comp_info["value"]),
        )


def compute_metrics(config):
    \"""运行仿真并计算指标\"""
    # 运行仿真
    ame_apy.AMERunSimulation()

    # 获取输出信号
    results = {}
    for sig_name, sig_path in config["output_signals"].items():
        val = ame_apy.AMEGetVariableValue(sig_path)
        results[sig_name] = val

    # 计算达标情况
    metrics = {}
    target = config["target"]

    if "pressure_max" in target:
        # 获取压力时间序列中的最大值
        p = results.get("pressure", 0)
        if isinstance(p, list):
            p_max = max(p)
        else:
            p_max = p
        metrics["pressure_max"] = round(p_max, 2)
        metrics["pressure_target_met"] = p_max <= target["pressure_max"]

    if "flow_min" in target:
        # 获取流量时间序列中的最小值（稳态）
        q = results.get("flow", 0)
        if isinstance(q, list):
            q_steady = q[-1]
        else:
            q_steady = q
        metrics["flow_steady"] = round(q_steady, 6)
        metrics["flow_target_met"] = q_steady >= target["flow_min"]

    return metrics


def run_one_iteration(config):
    \"""单次仿真：设参 → 仿真 → 计算 metrics\"""
    set_params(config)
    ame_apy.AMESetRunParameter("Final time", config["sim_time"])
    return compute_metrics(config)


# ========== 主循环 ==========
if __name__ == "__main__":
    cfg = TUNING_CONFIG
    ame_apy.AMEInitAPI()

    try:
        ame_apy.AMELoadModel(cfg["model_path"])

        history = []
        for i in range(cfg["max_iterations"]):
            metrics = run_one_iteration(cfg)
            result = {
                "iteration": i + 1,
                "params": {k: v["value"] for k, v in cfg["components"].items()},
                "metrics": metrics,
            }
            history.append(result)
            print(f"[迭代 {i+1}] {json.dumps(metrics, indent=2)}")

            # 检查是否全部达标
            all_met = all(
                v for k, v in metrics.items() if k.endswith("_target_met")
            )
            if all_met:
                print(f"✅ 全部指标达标，共 {i+1} 轮迭代")
                break
        else:
            print(f"⚠️ 达到最大迭代 {cfg['max_iterations']} 次，未完全达标")

        print("\\n=== 完整历史 ===")
        print(json.dumps(history, indent=2))

    except Exception as e:
        print(f"仿真出错: {e}")
    finally:
        ame_apy.AMECloseModel()
        ame_apy.AMECloseAPI()
```

---

## 注意事项

- 参数值以**字符串**传递，使用 SI 单位（Pa, m, m³, K 等）
- `circuit_name` 是模型名称（不带 `.ame` 扩展名）
- `component_name` 是组件实例名称（如 `"ORIFICE1"`、`"pressure source1"`）
- `submodel` 是子模型参数路径（如 `"area of orifice"`、`"pressure at port 1"`）
- `param` 是参数内部名称（如 `"area at 0"`、`"pressure"`）
- `AMEGetVariableValue` 返回标量或列表，需根据实际情况处理
- `compute_metrics` 的详细说明参见 `performance_metrics.md`
"""

_REF_SCRIPT_TEMPLATE_GRID_SEARCH = """\
# 网格搜索 — 脚本模板

## 适用场景

参数范围已知、需全面扫描时使用。遍历参数组合，按目标指标排序找全局最优。

> 总组合数 = 各参数网格点数的乘积，注意控制规模。

---

## 完整脚本

```python
import os, sys, json, numpy as np
from itertools import product

# ========== 路径配置 ==========
AME_ROOT = r"C:\\Program Files\\Simcenter\\2310\\Amesim"
sys.path.append(os.path.join(AME_ROOT, "scripting", "python"))
os.add_dll_directory(os.path.join(AME_ROOT, "win64"))
os.add_dll_directory(os.path.join(AME_ROOT, "sys", "python", "win64"))

import ame_apy

GRID_CONFIG = {
    "model_path": r"C:/path/to/model.ame",
    "circuit_name": "model_name",
    "sim_time": "10.0",
    # 每个参数的网格点（组件名.参数名 → 网格值列表）
    "grid": {
        "ORIFICE1.area of orifice.area at 0": np.linspace(1e-6, 1e-4, 10).tolist(),
        "PSRC1.pressure at port 1.pressure": np.linspace(100000, 500000, 8).tolist(),
    },
    "output_signals": {
        "pressure": "ORIFICE1.pressure at port 1",
        "flow": "ORIFICE1.flow rate",
    },
    "target": {
        "pressure_max": 300000,
        "flow_min": 0.001,
    },
    # 排序准则：选择要优化的目标
    "sort_by": "pressure_max",  # 按压力最小排序
}


def set_params_from_combo(config, combo):
    \"""根据参数组合设置模型参数\"""
    circuit = config["circuit_name"]
    param_keys = list(config["grid"].keys())
    for key, value in zip(param_keys, combo):
        # key 格式: "COMPONENT.submodel.param"
        parts = key.split(".")
        component = parts[0]
        submodel = ".".join(parts[1:-1])
        param = parts[-1]
        ame_apy.AMESetParameterValue(circuit, component, submodel, param, str(value))


def compute_metrics(config):
    \"""运行仿真并计算指标\"""
    ame_apy.AMERunSimulation()

    results = {}
    for sig_name, sig_path in config["output_signals"].items():
        val = ame_apy.AMEGetVariableValue(sig_path)
        results[sig_name] = val

    metrics = {}
    target = config["target"]

    if "pressure_max" in target:
        p = results.get("pressure", 0)
        p_max = max(p) if isinstance(p, list) else p
        metrics["pressure_max"] = round(p_max, 2)

    if "flow_min" in target:
        q = results.get("flow", 0)
        q_steady = q[-1] if isinstance(q, list) else q
        metrics["flow_steady"] = round(q_steady, 6)

    return metrics


if __name__ == "__main__":
    cfg = GRID_CONFIG
    ame_apy.AMEInitAPI()

    try:
        ame_apy.AMELoadModel(cfg["model_path"])
        ame_apy.AMESetRunParameter("Final time", cfg["sim_time"])

        param_names = list(cfg["grid"].keys())
        param_values = list(cfg["grid"].values())
        combinations = list(product(*param_values))

        print(f"总组合数: {len(combinations)}")

        results = []
        for idx, combo in enumerate(combinations):
            set_params_from_combo(cfg, combo)
            metrics = compute_metrics(cfg)

            combo_dict = dict(zip(param_names, combo))
            results.append({"params": combo_dict, "metrics": metrics})

            if (idx + 1) % 10 == 0:
                print(f"进度: {idx+1}/{len(combinations)}")

        # 按排序准则排序
        sort_key = cfg["sort_by"]
        results.sort(key=lambda r: r["metrics"].get(sort_key, float("inf")))
        print(f"\\n=== Top 5 参数组合（按 {sort_key}） ===")
        for r in results[:5]:
            print(json.dumps(r, indent=2, default=str))

        # 保存完整结果
        with open("grid_results.json", "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2, default=str, ensure_ascii=False)

    except Exception as e:
        print(f"仿真出错: {e}")
    finally:
        ame_apy.AMECloseModel()
        ame_apy.AMECloseAPI()
```

---

## 注意事项

- 结果按指定目标排序，同时检查其他指标是否达标
- 完整结果保存为 `grid_results.json`，方便后续分析
- 参数值以**字符串**传递，使用 SI 单位
- `AMEGetVariableValue` 返回标量或列表，需根据实际情况处理
- 网格点数 = 各参数网格点数的乘积，控制在合理范围内（建议 < 1000 组合）
- `compute_metrics` 的详细说明参见 `performance_metrics.md`
"""

_REF_PERFORMANCE_METRICS = """\
# 性能指标定义与分析

## 通用时域性能指标

| 指标 | 英文名 | 说明 | 计算方法 |
|------|--------|------|----------|
| 最大值 | Peak Value | 输出信号的最大值 | `max(y)` |
| 最小值 | Min Value | 输出信号的最小值 | `min(y)` |
| 稳态值 | Steady-state Value | 输出信号的最终值 | `y[-1]` |
| 上升时间 | Rise Time | 从 10% 到 90% 稳态值的时间 | `t_90 - t_10` |
| 超调量 | Overshoot | 峰值超出稳态值的百分比 | `(peak - steady) / steady * 100%` |
| 调节时间 | Settling Time | 进入 ±2% 误差带的时间 | 最后一次离开误差带的时刻 |
| 稳态误差 | Steady-state Error | 最终输出与目标值的偏差 | `|final_value - setpoint|` |
| ITAE | — | 时间×绝对误差积分，综合性能指标 | `∫t·|e(t)|dt` |

---

## 领域特定指标

### 液压系统

| 指标 | 说明 | 计算方法 |
|------|------|----------|
| 最大压力 | 系统峰值压力 | `max(pressure)` |
| 稳态流量 | 系统稳定后的流量 | `flow[-1]` |
| 压力脉动率 | 压力波动幅度 | `(max(p) - min(p)) / mean(p) * 100%` |
| 响应时间 | 从阶跃输入到输出达到 90% 的时间 | `t_90` |

### 热管理系统

| 指标 | 说明 | 计算方法 |
|------|------|----------|
| 最高温度 | 系统峰值温度 | `max(temp)` |
| 稳态温度 | 系统稳定后的温度 | `temp[-1]` |
| 温度超调 | 温度超过目标值的幅度 | `max(temp) - target` |
| 热平衡时间 | 温度进入 ±2°C 误差带的时间 | 最后一次离开误差带的时刻 |

---

## compute_metrics 函数

### 通用版本（带 setpoint）

```python
def compute_metrics(time_arr, output_arr, setpoint):
    \"""从仿真数据计算时域指标（适用于有明确目标值的场景）\"""
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

### 通用版本（无 setpoint，极值型）

```python
def compute_metrics_basic(time_arr, output_arr):
    \"""从仿真数据计算基本指标（适用于无明确目标值的场景，如压力/温度极值）\"""
    t = np.array(time_arr).flatten()
    y = np.array(output_arr).flatten()

    return {
        "max_value": round(float(np.max(y)), 4),
        "min_value": round(float(np.min(y)), 4),
        "steady_value": round(float(y[-1]), 4),
        "mean_value": round(float(np.mean(y)), 4),
        "std_value": round(float(np.std(y)), 4),
    }
```

---

## 积分准则对比

| 准则 | 公式 | 特点 |
|------|------|------|
| IAE（绝对误差积分） | `∫|e(t)|dt` | 适合一般应用 |
| ISE（误差平方积分） | `∫e(t)²dt` | 惩罚大偏差 |
| ITAE（时间加权） | `∫t·|e(t)|dt` | 惩罚长时间偏差，综合最优 |

---

## 结果分析要点

寻优脚本运行结束后，AI 从 `history` 输出中提取关键信息呈现给用户：

1. **汇总表** — 列出每轮迭代的参数值和对应指标，高亮最优轮次
2. **达标判定** — 逐项对比目标与最终结果，明确 ✅/❌
3. **趋势分析** — 参数变化对指标的影响趋势（如"面积从 2e-5→4e-5 时压力降低 30%，流量增加 50%"）
"""

_REF_TROUBLESHOOTING = """\
# 常见问题排查

## 仿真相关

| 问题 | 原因 | 解决方案 |
|------|------|----------|
| `AMELoadModel` 失败 | 模型已在 Amesim GUI 中打开 | 关闭 GUI 中的模型，再执行脚本 |
| 仿真不收敛 | 步长过大、代数环 | 减小仿真步长，检查模型是否有代数环 |
| 参数设置无效 | 组件名/参数路径拼写错误 | 确认组件名、子模型路径、参数名拼写正确（大小写敏感） |
| 仿真结果异常 | 输出信号路径错误 | 检查 `AMEGetVariableValue` 的信号路径格式 |
| `AMEGetVariableValue` 返回空 | 信号路径不存在或未配置输出 | 确认模型中已配置输出信号，路径格式为 `"component.signal"` |
| 参数值格式错误 | 值不是字符串或含单位 | 值必须为**字符串**，使用 SI 单位，不带单位后缀 |

## 连接相关

| 问题 | 原因 | 解决方案 |
|------|------|----------|
| `ImportError: DLL load failed` | 缺少 `os.add_dll_directory` 配置 | 补充所有包含 `.dll` 的子路径 |
| `ame_apy` 导入失败 | `sys.path` 未配置或路径错误 | 确认 `AME_ROOT/scripting/python` 已加入 `sys.path` |
| `AMEInitAPI` 失败 | 许可证不可用 | 确认 Runtime 许可证可用 |
| 路径含中文导致错误 | `AME_ROOT` 路径含中文或特殊字符 | 使用纯英文路径安装 Amesim |
| Python 版本不匹配 | Python 与 Amesim 版本不兼容 | 查阅 Amesim 发行说明，使用匹配的 Python 版本 |

## 许可证相关

| 问题 | 原因 | 解决方案 |
|------|------|----------|
| 许可证不足 | 多进程同时占用 | 确保脚本退出前调用 `AMECloseAPI()` 释放 |
| 脚本异常退出未释放 | 未使用 `try...finally` | 始终用 `try...finally` 包裹，确保 `AMECloseAPI` 被调用 |
| 需要 GUI 许可证 | 使用了草图操作 | Simulation Scripting 仅需 Runtime 许可证 |

## 性能权衡

- **响应速度 vs 稳定性**：通常互斥，需根据场景取舍
- **精度 vs 计算时间**：网格点越多越精确，但耗时成倍增长
- **单目标 vs 多目标**：多个目标可能冲突，需权衡优先级

## 仿真等待机制

`AMERunSimulation()` 是同步阻塞调用，会阻塞 Python 进程直到仿真结束。对于简单串行任务，直接依赖该阻塞特性即可。

如需并行仿真，可结合 `multiprocessing` 或 `concurrent.futures`，但必须注意：
- 每个子进程需独立调用 `AMEInitAPI()` 并持有独立许可证
- 子进程间不共享模型状态
"""

_REF_LOG_TEMPLATE = """\
# 调参日志模板

此文件为调参日志的标准格式。每次创建新的调参会话时，复制此模板并填充内容。

文件命名规则：`YYYY-MM-DD_<简短描述>.md`

---

# 调参日志：[模型名称] - [调参描述]

**日期：** YYYY-MM-DD
**模型路径：** `C:/path/to/model.ame`
**调参目标：** [用一句话描述本次调参的核心目标]

---

## 1. 环境信息

> 本节记录调参所用的完整运行环境，方便后续复现或在新环境中迁移。

| 项目 | 值 |
|------|-----|
| 操作系统 | [如 Windows 11 / Ubuntu 22.04] |
| Python 版本 | [如 3.8.10] |
| Python 路径 | [如 `C:/Users/xxx/.venv/Scripts/python.exe`] |
| Amesim 版本 | [如 Simcenter 2310] |
| Amesim 安装路径 | [如 `C:/Program Files/Simcenter/2310/Amesim`] |
| API 版本 | [如 ame_apy x.x] |
| 调参脚本路径 | [如 `C:/Users/xxx/Desktop/project/scripts/tune_model.py`] |

**环境备注：** [如有特殊配置，如虚拟环境名称、License 类型等]

---

## 2. 模型结构

> 本节记录被控系统的完整物理结构和各组件参数，是后续调优的核心参考。

### 系统结构图

```
[输入源] → [组件1] → [组件2] → [输出]
    ↑                            |
    └──────── [反馈/连接] ───────┘
```

> 可使用 ASCII 绘制，或嵌入 Amesim 模型截图。

### 组件参数汇总

| 组件名 | 子模型 | 参数名 | 当前值 | 单位 |
|--------|--------|--------|--------|------|
| | | | | |
| | | | | |

### 全局参数列表

| 参数名 | 值 | 单位 |
|--------|-----|------|
| | | |

---

## 3. 调参需求

### 目标指标

| 指标 | 目标值 | 备注 |
|------|--------|------|
| 最大压力 | < X Pa | |
| 稳态流量 | > X m³/s | |
| 响应时间 | < X s | |
| 超调量 | < X% | |

### 模型信息

- **Amesim 模型：** `<model_name>.ame`（完整路径见顶部）
- **调参组件：** `<component_name>`（如 `ORIFICE1`、`PSRC1`）
- **参数类型：** [组件参数 / 全局参数 / PID 控制器]
- **测试工况：** [描述输入条件，如入口压力、负载等]
- **仿真时长：** X s
- **约束条件：** [如有]

---

## 4. 初始状态

| 参数 | 组件 | 参数路径 | 初始值 | 参数范围 |
|------|------|----------|--------|----------|
| area at 0 | ORIFICE1 | area of orifice | X.Xe-X | [min, max] |
| pressure | PSRC1 | pressure at port 1 | XXXXX | [min, max] |

### 初始仿真结果

| 指标 | 初始值 | 目标值 | 是否达标 |
|------|--------|--------|----------|
| 最大压力 | X Pa | < X Pa | ❌/✅ |
| 稳态流量 | X m³/s | > X m³/s | ❌/✅ |
| 响应时间 | X s | < X s | ❌/✅ |

---

## 5. 调参过程

### 迭代 #1

**调整策略：** [如"增大节流面积以降低压降"]

| 参数 | 组件 | 调整前 | 调整后 | 变化量 |
|------|------|--------|--------|--------|
| area at 0 | ORIFICE1 | X | Y | +Z% |
| pressure | PSRC1 | X | Y | +Z% |

**仿真结果：**

| 指标 | 数值 | 目标值 | 是否达标 |
|------|------|--------|----------|
| 最大压力 | X Pa | < X Pa | ❌/✅ |
| 稳态流量 | X m³/s | > X m³/s | ❌/✅ |
| 响应时间 | X s | < X s | ❌/✅ |

**小结：** [一句话总结本次调整的效果]

---

### 迭代 #2

**调整策略：** [...]

| 参数 | 组件 | 调整前 | 调整后 | 变化量 |
|------|------|--------|--------|--------|
| | | | | |

**仿真结果：**

| 指标 | 数值 | 目标值 | 是否达标 |
|------|------|--------|----------|
| | | | |

**小结：** [...]

---

<!-- 按需追加更多迭代 -->

---

## 6. 最终结果

### 最优参数

| 参数 | 组件 | 最终值 | 初始值 | 变化 |
|------|------|--------|--------|------|
| area at 0 | ORIFICE1 | | | |
| pressure | PSRC1 | | | |

### 性能对比

| 指标 | 初始值 | 最终值 | 目标值 | 达标 |
|------|--------|--------|--------|------|
| 最大压力 | | | | |
| 稳态流量 | | | | |
| 响应时间 | | | | |

### 总调参轮次：X 轮

---

## 7. 总结

**最终评价：** [对调参结果的总体评价]

**关键发现：**
- [发现 1：如"节流面积对压降影响显著，面积翻倍后压降降低 60%"]
- [发现 2：如"压力超过 400kPa 后系统出现振荡，推测存在非线性效应"]
- [...]

**后续建议：**
- [建议 1：如"可考虑增加蓄能器进一步平滑压力脉动"]
- [建议 2：如"建议在不同工况下验证当前参数的鲁棒性"]
- [...]
"""

_REF_AMESIM_API_REFERENCE = """\
# ame_apy API 常用参考

## 导入与路径配置

```python
import os
import sys

AME_ROOT = r"C:\\Program Files\\Simcenter\\2310\\Amesim"
sys.path.append(os.path.join(AME_ROOT, "scripting", "python"))
os.add_dll_directory(os.path.join(AME_ROOT, "win64"))
os.add_dll_directory(os.path.join(AME_ROOT, "sys", "python", "win64"))

import ame_apy
```

## 生命周期管理

```python
# 初始化 API（占用许可证）
ame_apy.AMEInitAPI()

# 获取 API 版本
ver = ame_apy.AMEGetAPIVersion()

# 关闭 API（释放许可证）— 必须在脚本结束时调用
ame_apy.AMECloseAPI()
```

## 模型操作

```python
# 加载模型（模型不能在 GUI 中打开）
ame_apy.AMELoadModel(r"C:/path/to/model.ame")

# 保存当前模型
ame_apy.AMESaveModel()

# 关闭当前模型
ame_apy.AMECloseModel()
```

## 参数操作

### 组件参数

```python
# 设置组件参数
# signature: AMESetParameterValue(circuit, component, submodel, param, value)
ame_apy.AMESetParameterValue(
    "model_name",           # 模型名（不带扩展名）
    "ORIFICE1",             # 组件实例名
    "area of orifice",      # 子模型参数路径
    "area at 0",            # 参数内部名
    "2e-5",                 # 新值（字符串，SI 单位）
)

# 示例：修改压力源
ame_apy.AMESetParameterValue(
    "Changeparameter",
    "pressure source1",
    "pressure at port 1",
    "pressure",
    "200000",               # 200000 Pa
)
```

### 全局参数

```python
# 读取全局参数，返回 (list_of_dict, status)
gp_list, status = ame_apy.amereadgp("model_name")

# 每个字典包含 name, value, unit 等字段
for gp in gp_list:
    print(f"{gp['name']} = {gp['value']} {gp.get('unit', '')}")

# 修改后写回
ame_apy.amewritegp("model_name", gp_list)
```

### 全局参数批量导入导出（CSV）

```python
import csv

# 导出
sname = "model_name"
gp_dict_list, ret_stat = ame_apy.amereadgp(sname)

with open("params.csv", "w", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=gp_dict_list[0].keys())
    writer.writeheader()
    writer.writerows(gp_dict_list)

# 导入
with open("params_edited.csv", "r") as f:
    gp_dict_list = list(csv.DictReader(f))

ame_apy.amewritegp(sname, gp_dict_list)
```

## 仿真控制

```python
# 设置仿真运行参数
ame_apy.AMESetRunParameter("Final time", "10.0")
ame_apy.AMESetRunParameter("Print interval", "0.01")

# 运行仿真（同步阻塞，直到仿真完成）
ame_apy.AMERunSimulation()
```

## 结果获取

```python
# 获取信号值（标量或列表）
# signal_path 格式: "component_name.signal_name"
pressure = ame_apy.AMEGetVariableValue("ORIFICE1.pressure at port 1")
flow = ame_apy.AMEGetVariableValue("ORIFICE1.flow rate")

# 如需完整时间序列（取决于 API 版本）
# time_series = ame_apy.AMEGetVariableValues("signal_path")
```

## 完整仿真流程

```python
import os, sys

AME_ROOT = r"C:\\Program Files\\Simcenter\\2310\\Amesim"
sys.path.append(os.path.join(AME_ROOT, "scripting", "python"))
os.add_dll_directory(os.path.join(AME_ROOT, "win64"))
os.add_dll_directory(os.path.join(AME_ROOT, "sys", "python", "win64"))

import ame_apy

def main():
    ame_apy.AMEInitAPI()

    try:
        # 加载模型
        ame_apy.AMELoadModel("model.ame")

        # 设置仿真时间
        ame_apy.AMESetRunParameter("Final time", "10.0")

        # 修改参数
        ame_apy.AMESetParameterValue(
            "model", "ORIFICE1",
            "area of orifice", "area at 0", "2e-5"
        )

        # 运行仿真
        ame_apy.AMERunSimulation()

        # 获取结果
        p = ame_apy.AMEGetVariableValue("ORIFICE1.pressure at port 1")
        print(f"压力 = {p} Pa")

        # 保存模型（可选）
        ame_apy.AMESaveModel()

    except Exception as e:
        print(f"仿真出错: {e}")
    finally:
        ame_apy.AMECloseModel()
        ame_apy.AMECloseAPI()

if __name__ == "__main__":
    main()
```

## 参数值规范

| 规则 | 说明 | 示例 |
|------|------|------|
| 字符串类型 | 所有参数值必须为字符串 | `"2e-5"` 而非 `2e-5` |
| SI 单位 | 使用国际单位制，不带单位后缀 | `"200000"` 而非 `"200 kPa"` |
| 小数点 | 使用英文句点 | `"0.001"` 而非 `"0,001"` |
| 科学计数法 | 支持标准格式 | `"2e-5"`、`"1.5E+3"` |

## 常见组件参数路径

| 组件类型 | submodel 路径 | 参数名 | 说明 |
|----------|---------------|--------|------|
| 节流口 | `area of orifice` | `area at 0` | 节流面积 |
| 压力源 | `pressure at port 1` | `pressure` | 压力值 |
| 流量源 | `flow rate at port 1` | `flow rate` | 流量值 |
| PID 控制器 | `controller` | `Kp`, `Ki`, `Kd` | PID 增益 |
| 泵 | `flow rate` | `displacement` | 排量 |

> **注意：** 具体的 submodel 路径和参数名取决于模型中的组件类型。使用 `amereadgp` 读取全局参数列表是最可靠的方式。

## 错误处理

```python
import ame_apy

try:
    ame_apy.AMEInitAPI()
    ame_apy.AMELoadModel(r"C:/path/to/model.ame")
    # ... 核心操作
except Exception as e:
    print(f"发生异常: {e}")
finally:
    try:
        ame_apy.AMECloseModel()
    except:
        pass
    ame_apy.AMECloseAPI()
```

## 性能提示

- `AMEInitAPI()` 首次初始化约 5-10 秒，尽量复用同一会话
- 批量参数设置比逐个设置更快
- `AMERunSimulation()` 是阻塞调用，无需额外等待
- 大量数据传输时，考虑在 Amesim 端预处理，只传回结果
- 并行仿真需每个子进程独立初始化 API 并持有独立许可证
"""

# ── Virtual files dict ──────────────────────────────────────

_VIRTUAL_FILES: dict[str, str] = {
    "vfs://amesim-tuner/references/environment_setup.md": _REF_ENVIRONMENT_SETUP,
    "vfs://amesim-tuner/references/model_exploration.md": _REF_MODEL_EXPLORATION,
    "vfs://amesim-tuner/references/tuning_strategies.md": _REF_TUNING_STRATEGIES,
    "vfs://amesim-tuner/references/script_template_ai_guided.md": _REF_SCRIPT_TEMPLATE_AI_GUIDED,
    "vfs://amesim-tuner/references/script_template_grid_search.md": _REF_SCRIPT_TEMPLATE_GRID_SEARCH,
    "vfs://amesim-tuner/references/performance_metrics.md": _REF_PERFORMANCE_METRICS,
    "vfs://amesim-tuner/references/troubleshooting.md": _REF_TROUBLESHOOTING,
    "vfs://amesim-tuner/references/log_template.md": _REF_LOG_TEMPLATE,
    "vfs://amesim-tuner/references/amesim_api_reference.md": _REF_AMESIM_API_REFERENCE,
}


def AmesimTunerSkill() -> Skill:
    """Simcenter Amesim parameter tuning via ame_apy API."""
    return Skill.builtin(
        name="amesim-tuner",
        description=(
            "Simcenter Amesim 参数调优技能。此技能通过 Python 脚本驱动 ame_apy API 与 Amesim 模型交互，"
            "实现 AI 辅助的系统参数整定。适用于液压系统、热管理系统、机械传动、PID 控制器等各类 "
            "Amesim 模型的参数扫描、标定与优化。"
        ),
        content=_SKILL_CONTENT,
        virtual_files=_VIRTUAL_FILES,
    )
