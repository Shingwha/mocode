# 孤儿模块门禁登记（W2 T4，lead 采集 2026-10-03）

门禁口径：产品模块（`mocode/**/*.py`，排除 `__init__.py`）的点路径必须在 `tests/` 出现
（import 语句或 importlib 模块加载），否则补一条最小契约测试或在此登记豁免理由。
第一轮按文件名搜索的产出是全仓误报（测试按主题命名，不含产品文件名），已改为点路径。

```bash
for f in $(find mocode -name '*.py' ! -name '__init__.py'); do
  mod=$(echo "$f" | sed 's/\.py$//' | tr '/' '.')
  grep -rlq "$mod" tests/ || echo "ORPHAN: $mod"
done
```

## 登记（17 项，全部为豁免，无新补测试——总量已压线 500，且每项均有既有行为覆盖）

| 模块 | 覆盖形态 | 豁免理由 |
|---|---|---|
| `mocode.cli.app` | `mocode.cli` 包级 re-export（`from mocode.cli import CLIApp`，test_cli_plugin.py:55） | 终端装配框架，CLI 插件契约由 test_cli_plugin / test_runtime 通过公开 build 路径断言 |
| `mocode.cli.input` | 无（已知豁免） | 终端交互层（键盘输入），测试成本高，spec 基线即登记 |
| `mocode.cli.text` | 无（已知豁免） | 终端文本helper 层，同上 |
| `mocode.core.turn` | `AgentLoop.start()` 内部路径 | Turn 生命周期由 test_agent_loop 45 条测试逐面覆盖（取消、预算、并发），无第二组装点 |
| `mocode.host.plugin.base` | `mocode.plugins` SDK 面 | Plugin 基类是插件作者契约，加载/构造/发现由 test_plugins 覆盖 |
| `mocode.host.plugin.builtin.default_prompts` | `builtin_plugins()` 装配 + build_system_prompt 输出 | prompt 内容由 test_conversations/test_plugins 的结构断言（section 集合、用户/项目级注入、date 形状）覆盖 |
| `mocode.host.plugin.builtin.effort` | `builtin_plugins()` 装配 + 命令注册 | effort 切换由 test_conversations（set_effort）与 test_builtin_plugins 覆盖 |
| `mocode.host.plugin.builtin.help` | `builtin_plugins()` 装配 + /help 命令 | 命令名集合对账由 test_runtime 断言 |
| `mocode.host.plugin.builtin.mcp.plugin` | `builtin_plugins()` 装配 + in-proc 会话 | MCPPlugin 的装配/注册/暴露由 test_builtin_mcp 覆盖（in-proc 路径即该插件生命周期） |
| `mocode.host.plugin.builtin.session` | `builtin_plugins()` 装配 + 会话命令 | /clear 等会话命令由 test_builtin_plugins 覆盖 |
| `mocode.host.plugin.builtin.shell.plugin` | `builtin_plugins()` 装配 + 工具面 | bash/bash_output/kill_shell 注册与调用由 test_shell_bg/test_tools 覆盖 |
| `mocode.host.plugin.builtin.shell.ring` | 包级 import `from …builtin.shell import _start_order`（test_shell_bg.py:472）+ ring 边界测试 | ring 有界窗口由 test_shell_bg 的 ring 测试直接断言 |
| `mocode.host.plugin.builtin.shell.session` | 包级 import（test_tools.py:10、test_shell_bg.py:41,77）+ 会话行为矩阵 | BashSession 的 cd/env/超时/重启由 test_tools 覆盖 |
| `mocode.host.plugin.builtin.shell.tool` | 包级 import `bash_tool`（test_tools.py:10） | bash 工具回包由 test_tools 断言 |
| `mocode.host.prompt` | 经 agent.system_prompt 断言 | build_system_prompt 的产出由 prompt 结构断言（section 名、注入事实、日期形状）覆盖；spec 00 覆盖空洞段列出，W2 总量压线无法再补，登记在此 |
| `mocode.main` | CLI 入口（argv 解析 + main） | 进程入口层，真实 argv 路径由 test_plugin_install 的 CLI 子命令测试覆盖（exit code/stderr） |
| `mocode.testing.providers` | `mocode.testing` SDK re-export | MockProvider/SlowProvider/say 被 conftest 与 4 个测试文件广泛使用，行为由 test_testing.py 断言 |

## 门禁命令留档

（见上方 bash 段。）已知豁免即上表除"无"标注三项外全部；终端交互三项
（`cli/input.py`、`cli/text.py`、`cli/dialogs.py`）为 spec 基线即登记的顶层豁免。
