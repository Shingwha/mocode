# Spec 02 · W2 — host：config.py 重写 + set_effort

> ✅ 2026-10-02 完成 @ config-refactor-w2-host（已合并 master）
> 分支 `config-refactor-w2-host`；前置：**W1 已合并进 master**（分支从
> 合并后的 master 创建）；深读：
> `specs/2026-10-02-config-refactor/00-overview.md`（总纲，必读）。
> W1 已交付：`ModelSpec(name, context_window, max_tokens, efforts=EFFORTS,
> effort=None)`、`Effort`/`EFFORTS` 已从 `mocode.core` 与 `mocode.plugins`
> 导出、`OpenAIProvider` 已无 `extra_body` 参数。

## 目标

按总纲的目标态重写 host 配置层：models 改 pi 式数组（`id`/`name`），
ModelEntry 换用 `max_tokens`/`efforts`/`effort` 并删除 `extra_body`；
顶层 `active_provider`/`active_model` 改名 `provider`/`model`；
`Conversation` 增加 `set_effort` 运行时切换；host 侧全部测试适配。
config.json 的新旧 schema 以总纲的「目标态 config.json」为权威。

## 写入范围（仅此清单内的文件）

- `mocode/host/config.py`（整体重写）
- `mocode/host/runtime.py`
- `mocode/host/conversation.py`
- `tests/conftest.py`
- `tests/test_config.py`（整体重写）
- `tests/test_dispatch.py`
- `tests/test_plugin_message.py`
- `tests/test_plugins.py`
- `tests/test_conversations.py`
- `tests/test_runtime.py`

## 工单（按序执行，每项一个 commit）

### T1 mocode/host/config.py — 重写
顶层 docstring 中的示例改为总纲目标态。类型设计：

- `ModelEntry`：`id: str = ""`、`name: str = ""`、
  `context_window: int | None`、`max_tokens: int | None`、
  `efforts: tuple[str, ...] | None = None`（**None = 文件未声明**，解析时
  落缺省）、`effort: str | None = None`、`retry: dict | None`。
  - `from_dict`：`efforts` 只接受字符串列表（逐项 str，非 str 项丢弃；
    空列表视为未声明 → None）；`effort` 只接受 str；其余字段沿用现有
    `_opt_int` 语义。`retry` 沿用现有过滤逻辑（`_RETRY_KEYS` 不变）。
  - `to_dict`：未设置的键一律省略；`efforts` 非 None 时序列化为列表；
    `id` 恒写出（空 id 也可写出，load 侧对缺 id 项跳过——见下）。
  - `retry_policy()` 不变。
- `ProviderEntry`：`models: list[ModelEntry]`。
  - `from_dict`：`models` 必须是列表；逐项 `ModelEntry.from_dict`，
    **缺 `id` 的项跳过**；非 dict 项跳过。
  - 方法：`label(key)` 不变；`model_ids() -> list[str]`（替代
    `model_names()`，按声明顺序）；`model(id) -> ModelEntry | None`。
  - `to_dict`：`models` 序列化为列表。
  - `api_key_for`/`env_var_for` 语义不变。
- `Config`：`provider: str = ""`、`model: str = ""`（替代
  `active_provider`/`active_model`）；`_OWNED_KEYS` 相应更新；
  `current` 属性改读 `self.provider`；`model_spec()` 按模型 id 在
  entry 列表中查找，产出 `ModelSpec(name=<模型 id>,
  context_window=…, max_tokens=…, efforts=<entry.efforts or EFFORTS>,
  effort=<entry.effort>)`；未知 provider/model 仍解析为 bare spec
  （无发明值）。`foreign` 保留机制与 `path` 语义不变。
  - `to_dict`/`from_dict` 顶层键改 `provider`/`model`。
  - `load`/`save` 不变。

### T2 mocode/host/runtime.py
- `new_conversation` 中 `self.config.active_provider`/`active_model` →
  `self.config.provider`/`.model`；`provider_for` 报错文案
  "point active_provider at an existing one" → "point provider at an
  existing one"。
- `_openai_factory`：删除 `extra_body` 实参；`retry_policy` 改经
  `entry.model(model)` 取（`model_entry = entry.model(model)`，
  `retry_policy=model_entry.retry_policy() if model_entry else None`）。
- `set_default_model`：写 `config.provider`/`config.model`（仍是唯一写
  config.json 的地方）。

### T3 mocode/host/conversation.py — set_effort
- 新增 `set_effort(level: str) -> None`：docstring 说明「会话内决策，不写
  config.json，与 set_model 同语义；下一次 provider 请求起生效」。
  实现：`self.agent.model = dataclasses.replace(self.agent.model,
  effort=level)`，并 `self.ctx.model = self.agent.model` 同步
  （跟随 `set_model` 的模式）。

### T4 测试适配
- `tests/conftest.py` `make_config`：`Config(provider="test",
  model="test-model", …)`，`models=[ModelEntry(id="test-model"),
  ModelEntry(id="other-model")]` 等数组形式。
- `tests/test_config.py` 整体重写，覆盖：
  - `env_var_for`（原用例保留）；
  - ModelEntry 新字段 roundtrip/缺省/垃圾值（efforts 接受字符串列表、
    丢弃非字符串项、空列表→None、非列表→None；effort 非 str→None）；
  - ProviderEntry：数组解析、缺 id 项跳过、非 dict 项跳过、
    `model_ids()` 保序、`model(id)` 命中/未命中、`label` 回退、
    api_key 显式/环境回退（原用例语义保留）；
  - `model_spec`：active 对解析、显式对解析、未知模型/ provider 无发明
    值、**efforts 缺省落 EFFORTS、自定义 efforts 透传、effort 透传**；
  - 序列化：provider/model 顶层键、agent 块语义不变（原用例保留）、
    foreign 保留、owned 覆盖 foreign、未设置键省略、roundtrip；
  - 持久化：save/load、load 记住 path（原用例保留）。
- `tests/test_dispatch.py`、`tests/test_plugin_message.py`、
  `tests/test_plugins.py`：`Config(active_provider=…, active_model=…)` →
  `Config(provider=…, model=…)`。
- `tests/test_runtime.py`：4 处 `ProviderEntry(…,
  models={"llama": ModelEntry()})` → 数组形式。
- `tests/test_conversations.py`：`active_provider`/`active_model` 断言改
  新字段名；**新增**一个 set_effort 用例：`conversation.set_effort("max")`
  后 `conversation.agent.model.effort == "max"` 且
  `conversation.ctx.model.effort == "max"`，且 `mc.config.model` 不变
  （不写文件）。

## 验收（完成前自测，报告真实结论）

1. `uv run pytest` 全绿 —— 独立 `echo $?` 确认退出码 0。
2. `grep -rn "active_provider\|active_model\|extra_body" mocode/ tests/` 零命中。
3. `grep -rn "model_names\|max_output" mocode/host tests/test_config.py` 零命中。
4. 手工冒烟：`uv run python -c` 构造总纲目标态 config 字典 →
   `Config.from_dict(...).to_dict()` roundtrip 后 `provider`/`model`/
   `providers` 段与输入一致（把脚本与输出贴进报告）。

## 禁触清单

- `mocode/core/**`、`mocode/providers/**`、`mocode/testing/**`（W1 已交付，
  发现 W1 问题先报告，不手改）
- `mocode/cli/**`、`docs/**`、`README.md`、`AGENTS.md`、`examples/**`
  （W3）
- `tests/test_provider.py`、`tests/test_core.py`、`tests/test_retry.py`、
  `tests/test_agent_loop.py`、`tests/test_commands.py`
- `host/__init__.py` 导出名单不变（`Config`/`ModelEntry`/`ProviderEntry`
  名字都保留）。

## 环境事实

- Windows + Git Bash；worktree `C:\Users\shifu\.worktrees\mocode\
  config-refactor-w2-host`，**绝不操作主检出、绝不 merge/push/tag**。
- 开工第一步 `uv sync`。本波从 **W1 合并后的 master** 拉分支 —— lead
  已保证；若发现 master 与预期不符，停下来报告。

## 最终报告格式

工单状态表（T1–T4）/ commit 清单 / 自测真实结论（命令+输出+退出码，
含 T4 冒烟脚本输出）/ 偏差与取舍 / 未决问题。阻塞即停。
