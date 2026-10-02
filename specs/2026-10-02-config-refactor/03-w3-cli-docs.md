# Spec 03 · W3 — cli：/model 适配 + /effort 新命令 + 文档

> ✅ 2026-10-02 完成 @ config-refactor-w3-cli-docs（已合并 master）
> 分支 `config-refactor-w3-cli-docs`；前置：**W2 已合并进 master**；
> 深读：`specs/2026-10-02-config-refactor/00-overview.md`（总纲，必读）。
> W1/W2 已交付：`ModelSpec.efforts`/`effort`；`ProviderEntry` 的 models
> 为 `list[ModelEntry]`（`id`/`name`），方法 `model_ids()`/`model(id)`；
> `Config.provider`/`model`；`Conversation.set_effort(level)`。

## 目标

CLI `/model` 适配数组式 models（显示名回退 id）；新增 `/effort` 命令
（选择器列出当前模型档位表并标记当前档，选择后 `set_effort`，不写
config.json）；全仓文档（README、docs、AGENTS.md）与新 schema 对齐，
旧键名零残留。

## 写入范围（仅此清单内的文件）

- `mocode/cli/commands.py`
- `mocode/host/config.py` —— **仅允许**：T1 完成、cli 不再调用后，删除
  `ProviderEntry.model_names()` 桥接方法（lead 已批准的 variance，
  W2 为保门禁暂留）
- `tests/test_commands.py`
- `README.md`
- `docs/providers.md`
- `docs/plugins.md`
- `docs/testing.md`
- `docs/ARCHITECTURE.md`（仅当事实性过时才动）
- `AGENTS.md`

## 工单（按序执行，每项一个 commit）

### T1 /model 适配 — mocode/cli/commands.py
- provider 选择器的 description：`", ".join(entry.model_ids())`。
- 模型选择器：选项 title 用显示名回退 id —— 对 `entry.models` 中每项
  `m.title()` 不存在则内联 `m.name or m.id`；value 用 `m.id`；
  "current" 标记比较 `conversation.model_name == m.id`；default 逻辑同
  现有（model_name 在 id 列表中则选它，否则第一项）。
- 文案 `f"Switched to {entry.label(chosen_key)} / {chosen_model}"` 不变
  （chosen_model 现在是 id）。
- T1 收尾：确认 `mocode/cli` 与 `tests/` 无任何 `model_names` 调用后，
  删除 `host/config.py` 中的 `model_names()` 桥接方法（含其注释）。

### T2 /effort 新命令 — mocode/cli/commands.py
- 实现 `_effort(ctx)`：
  - `spec = conversation.model`（ModelSpec）；档位表
    `levels = list(spec.efforts)`；若 `spec.effort` 非 None 且不在
    levels 中，追加到末尾（自定义档位的当前值也要可选）。
  - 用 `dialogs.select` 出选择器，title
    `f"Reasoning effort for {conversation.model_name}:"`，每项
    description="current" 标记当前档；default 为当前档（不在表中则第一项）。
  - 选中后 `conversation.set_effort(level)` 并 notify
    `f"Reasoning effort: {level}"`；未选中（None）直接 CONTINUE。
- 注册：`COMMANDS` 中 `/model` 之后加
  `Command("/effort", "Set reasoning effort for this conversation",
  handler=_effort)`。
- docstring 说明：会话内决策，不写 config.json；配置里模型条目的
  `effort` 是新会话默认值。

### T3 tests/test_commands.py
- 适配 /model 既有用例（结构变化处）；**新增** /effort 用例，跟随本文件
  现有对 `dialogs.select` 的 monkeypatch 模式：
  - 选择某档 → `conversation.agent.model.effort` 变为该档，且出现
    confirm 的 Notice；
  - 取消（select 返回 None）→ effort 不变。

### T4 文档对齐
- `README.md`「Configuration」一节：换成总纲目标态示例；「API keys」段
  不变（env_var_for 语义未动）；「Output caps」段 `max_output` 改
  `max_tokens`；「Model facts」段重写：context_window/max_tokens 语义 +
  efforts/effort 语义（缺省三档、自定义档位表、verbatim 上电线、
  缺席=不发）；删除 extra_body 段落，替换为「需要 provider 专属请求字段
  的端点：注册自定义 provider type」一句指引（指向 docs/providers.md）。
- `docs/providers.md`：`OpenAIProvider(...)` 签名去 extra_body；请求步骤
  中说明 `reasoning_effort` 发送条件；删除 stream_options 可覆盖段
  （现硬编码 `{"include_usage": True}`）；Configuration 一节 factory
  示例改读 `entry.model(model)`、删 extra_body 段、config 示例换新
  schema（含 efforts/effort）；`models.<m>.max_output` 字样改
  `max_tokens`。
- `docs/plugins.md`：Model facts 表行改为 `ctx.model` — name /
  `context_window` / `max_tokens` / `efforts` / `effort`。
- `docs/testing.md`：config 示例换 `Config(provider=…, model=…)` +
  数组 models。
- `AGENTS.md`：「Where config values belong」表 —— `context_window`、
  `max_tokens`、`efforts`、`effort` 行（model entry）；`provider`、`model`
  行（config 文件默认值，唯一写文件处 `MoCode.set_default_model()`）；
  删 `extra_body` 与 `active_provider`/`active_model` 字样。
- `docs/ARCHITECTURE.md`：仅在事实过时时更新（模块图那行
  `config.py Config, ProviderEntry, ModelEntry` 无需动）。

## 验收（完成前自测，报告真实结论）

1. `uv run pytest` 全绿 —— 独立 `echo $?` 确认退出码 0。
2. `grep -rni "extra_body\|active_provider\|active_model" mocode/ tests/
   examples/ docs/ README.md AGENTS.md` 零命中（`specs/` 历史目录除外）。
3. `grep -rn "max_output" mocode/ docs/ README.md AGENTS.md` 零命中。
4. 文档中目标态示例与 `00-overview.md` 权威示例一致（键名、结构）。

## 禁触清单

- `mocode/core/**`、`mocode/host/**`、`mocode/providers/**`、
  `mocode/testing/**`、`examples/**`
- `tests/` 下除 `test_commands.py` 外的任何文件
- 不改命令注册机制本身（`BuiltinCommands` 装配方式不动）。

## 环境事实

- Windows + Git Bash；worktree `C:\Users\shifu\.worktrees\mocode\
  config-refactor-w3-cli-docs`，**绝不操作主检出、绝不 merge/push/tag**。
- 开工第一步 `uv sync`；dialogs.select 的既有测试替身模式先读懂再仿写。

## 最终报告格式

工单状态表（T1–T4）/ commit 清单 / 自测真实结论（命令+输出+退出码）/
偏差与取舍 / 未决问题。阻塞即停。
