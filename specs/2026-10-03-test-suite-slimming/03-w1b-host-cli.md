# Spec HOST-CLI · 宿主与终端测试重构（波次 W1-B，并行组 B）

> 分支 `test/slim-w1b-host-cli`；前置：W0 已合并进 main，本分支从该时刻 main 切出
> 深读：`specs/2026-10-03-test-suite-slimming/00-overview.md`、`01-w0-time-control.md`、
> `tests/conftest.py`、`docs/embedding.md`

## 目标

最高约束放在第一句：**宿主层不变量（会话隔离、请求面一次性物化、prompt 冻结、session
可移植性、插件发现加载契约）一条都不许少**；测试净减少来自参数化合并与去文案断言。
目标 ≥150 测试、组耗时 <4s、组内裸睡清零、提示词/文案字符串断言清零（display/lines 豁免除外）。

## 你的文件（独占，组外零 diff）

`tests/test_conversations.py`、`tests/test_runtime.py`、`tests/test_session.py`、
`tests/test_config.py`、`tests/test_plugins.py`、`tests/test_plugin_message.py`、
`tests/test_commands.py`、`tests/test_cli_plugin.py`、`tests/test_provider.py`、
`tests/test_display.py`、`tests/test_lines.py`、`tests/test_plugin_env.py`、
`tests/test_plugin_install.py`、`tests/test_cache_protect.py`。

## 工单（按序执行，每项一个 commit，每 commit 独立过门禁）

### T1 参数化合并机械重复

- `test_config.py` `TestModelEntry`：~10 个打同一入口的微小测试合并 parametrize
  （defaults/roundtrip-omit/garbage-limits/numeric-strings/id-name-types/efforts 变体），
  失败定位靠参数 id。
- `test_session.py` `TestPluginMessagesField` + `TestFrozenRequestFields`：6 个 round-trip/
  坏值丢弃测试合并为参数化矩阵。
- `test_plugins.py` 等文件中同形的小测试同样处理；`test_provider.py` 的 wire payload 逐字段
  断言（`sent[0]["stream_options"] == …`）保留——那是 provider 请求契约，但可参数化。

### T2 文案断言结构性改写（重灾区）

- `test_cache_protect.py`：逐字 diff（`-callable tools: bash, bash_output, …`、JSON diff 行）
  → 结构断言：解析出工具名集合等于注册表集合；diff 行可被 JSON 解析；新增/删除工具的
  集合差正确；不动"改了哪个工具"的行为契约。
- `test_builtin_plugins.py` 不在你范围（它归 W1-C）；你范围内 `test_conversations.py:562`
  "Always use tabs."、`:755-756` "today:"/"os:" → 断言 section 名与"用户级/项目级规则
  均注入"的事实，不测文案；`test_commands.py:248-250` skill 注入逐字文案 → skill 名 /
  `[Skill:<name>` 形态 / 调用顺序存在性。
- `test_runtime.py:314` `/help` 输出 → 命令名集合断言（从文本提取 token 与注册表对账）。
- `test_session.py` Markdown 导出：标题逐字（`## System Prompt` 等）→ 结构断言：导出的
  section 顺序、消息条数、tool call 区块数与 call 数一致；保留"可再导入"往返契约。

### T3 私有属性与内部 patch 清理

- `test_conversations.py:665` `mc._plugins.clear()` / `_loaded_for`：改走公开 API
  （`load_plugins` 或新建 runtime）；若产品确无公开入口，保留 patch 并 docstring 声明接缝。
- monkeypatch 内部函数（`runtime_module.load_plugins`、`conversation.runtime.config.save`、
  `default_prompts.datetime`、`mocode.cli.display.terminal_height`、`dialogs.select`）：
  能改公开 seam 就改；不能改的保留但在 docstring 标注。datetime 类改为显式注入假时间
  （构造参数或 conftest FakeClock 精神），禁止断言"today: 2026-09-20 (Sunday)"这类
  会随真实日期腐烂的值——注入固定日期后断言格式。
- `test_cli_plugin.py:220` 身份+类构造白盒 → 公开行为（加载/命名/命令注册结果）。

### T4 时序清零（你的文件里很少，逐处过）

- `test_conversations.py:136-199` 双会话并发 Event 同步、`test_display.py:295-343` 的
  Event 门控：**保留**（这是范本），只确认每个有界（wait_for 或 pytest-timeout 兜底）。
- 裸睡命中逐个改 `wait_until` / 事件；`conftest.settle` 仅用于"等待即被测行为"。

## 验收（完成前自测，报告给真实结论）

1. `uv run pytest tests/test_conversations.py tests/test_runtime.py tests/test_session.py tests/test_config.py tests/test_plugins.py tests/test_plugin_message.py tests/test_commands.py tests/test_cli_plugin.py tests/test_provider.py tests/test_display.py tests/test_lines.py tests/test_plugin_env.py tests/test_plugin_install.py tests/test_cache_protect.py -q; echo "EXIT=$?"` → 全绿
2. 测试数：上述 14 文件 collect ≥ 150
3. `grep -rn 'sleep(' <14 文件>` → 仅 settle/real_time/事件门控语境
4. `grep -rn 'in agent.system_prompt\|in system_prompt\|"today:\|Always use' <14 文件>` → 零命中（除 display/lines 豁免）
5. `git status --short` → 只有你的 14 个文件

## 禁触清单

你的 14 个文件之外的一切（含 conftest.py、其他测试文件、mocode/**、docs/**、
pyproject.toml、specs/**）。

## 诚实出口

若某私有属性确无公开替代（如插件加载缓存）：保留 patch + docstring 标注接缝，报告清单，
由 lead 裁决是否接受。不得改产品代码。

## 最终报告格式

工单状态表 / commit 清单（hash+message）/ 自测真实结论（命令+输出+退出码）/ 测试数前后
对比 / 删除登记（测试名+理由）/ 偏差与取舍 / 未决问题
