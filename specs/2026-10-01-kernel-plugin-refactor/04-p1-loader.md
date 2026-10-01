# Spec 04 · K16 多文件插件：包入口 + 消灭静默失败（波次 W2-C）

> 分支 `spec/p1-loader`；worktree `C:\Users\shifu\.worktrees\mocode\spec-p1-loader`；
> 前置：W1 已合并；深读：总纲 + `ref/kernel-plugin-api.md` 的 K16。
> 你只读本 worktree 内的文件；绝不操作主检出，绝不 merge/push/tag。

## 现状事实

- 宿主侧入口硬编码单文件：`mocode/host/plugin/loader.py` 的 `_spec_from_entry`（:210-224）只认 `<插件目录>/mocode/plugin.py`（常量 HOST_NAMESPACE/CODE_MODULE，:39-44）；作者建成 `mocode/plugin/` 包目录时 `code.is_file()` 为 False → `spec.module = None` → 被当纯 skills 插件，**无提示**。单文件 `<name>.py` 顶层插件另支持（:221-222）。
- CLI 侧同样：`mocode/cli/plugin.py` 的 `load_cli_plugins`（:63-88）只认 `<source>/mocode.cli/plugin.py`（NAMESPACE/ENTRY :34-37），不存在就 `continue`，静默。
- `import_module_file`（loader.py:235-256）：裸 `spec_from_file_location(module_name, path)`，无 `submodule_search_locations`——即使入口指向 `__init__.py`，包内相对导入也失败。sys.modules 先注册再 exec、异常隔离、ModuleNotFoundError 给 `fix:` 提示（:254-255）。
- `report()`（loader.py:67-69）：`print(f"[plugin] {message}", file=sys.stderr)`——加载问题的唯一上报通道，永不致命。
- `resolve_plugin(module)` 的实例约定：模块级 `plugin = MyPlugin()`。
- `PluginVenv`（env.py）：第三方依赖走独立 venv（`attach()` 追加 sys.path）。
- 测试惯例：`tests/test_plugins.py` 的 `_write_plugin(root, name, code)` 写 `plugin.json` + `mocode/plugin.py`；`tests/test_cli_plugin.py`、`tests/test_plugin_install.py` 覆盖两个 loader 与安装。

## 目标

插件代码支持**包形态**（`mocode/plugin/__init__.py` + 子模块），宿主与 CLI 两个 loader 用同一份入口判定；多文件组织的常见错误从静默变成有指引的 report；给出官方多文件范例。

## 工单（每项一个 commit）

### T1 共用入口判定 `code_entry()`
- `host/plugin/loader.py` 新增模块级函数：
  ```python
  def code_entry(namespace_dir: Path) -> Path | None:
      """<ns>/plugin.py（单文件）优先，其次 <ns>/plugin/__init__.py（包）。"""
  ```
  `_spec_from_entry` 改用它；语义保持：两者皆无 → 纯 skills 插件（`module=None`）。
- 发现 `mocode/plugin/` 目录存在但缺 `__init__.py`（或反之有散 `.py` 无入口）：`report("multi-file plugins need mocode/plugin/__init__.py")` 类提示，**不再静默**。

### T2 导入器支持包
- `import_module_file` 检测 `path.name == "__init__.py"` 时 `spec_from_file_location(module_name, path, submodule_search_locations=[parent_dir])`；现有语义全部保留（先注册 sys.modules、异常隔离 pop、ModuleNotFoundError 的 fix 提示）。子模块经包内相对导入加载（`from . import helper` / `from .helper import x`），模块名形如 `mocode_plugin_<slug>.<sub>`，插件间无碰撞。
- **禁止 sys.path 方案**（把 `<插件>/mocode/` 加 sys.path）——两个插件的 `helpers.py` 会在 sys.modules 互相覆盖。

### T3 CLI loader 复用
- `mocode/cli/plugin.py` 的 `load_cli_plugins` 改用 `code_entry()`（host → cli 方向的 import 合法）；同样对"目录在、`__init__.py` 缺"给 report。

### T4 多文件范例
- 新建 `examples/plugins/multi-file/`：`plugin.json`（name `multi-file`）、`README.md`、`mocode/plugin/__init__.py`（暴露 `plugin = MotdPlugin()`）+ 两个子模块（如 `sections.py` 贡献一个 prompt section、`commands.py` 注册一个 `/motd` 命令，`__init__` 从子模块相对导入组装）。
- **范例不注册任何工具**（工具构造 API 正在被并行波次 W2-A 更换，此处解耦）；README 说明何时该用包形态、`resolve_plugin` 实例约定、第三方依赖仍走 PluginVenv。

### T5 文档
- `docs/plugins.md` 新增"Single file vs package"一节（**只新增这一节，不改其他节**——其他节归 W3 收口）：≤几百行用 `plugin.py`；更大建包；入口判定顺序；sys.path 禁令及原因；PluginVenv 边界。

### T6 测试
- `tests/test_plugins.py`：包插件加载成功（fixture 写包布局）、包内相对导入可用、单文件与包并存时单文件优先、`plugin/` 无 `__init__.py` 时 report 有提示（可捕获 stderr 或将 report 做成可注入 sink——按既有测试风格选）。
- `tests/test_cli_plugin.py`：`mocode.cli` 命名空间的包插件同样加载。
- `tests/test_plugin_install.py`：如涉及入口判定则补一例包形态安装。

## 验收

1. `uv run pytest -q` 全绿，独立确认退出码 0。
2. 范例插件被 loader 实际加载的测试证据（如 `load_plugins(plugin_dirs=[examples/plugins/multi-file 的父目录])` 后 `plugin.name == "multi-file"`）。
3. `git diff <merge-base> --stat` ⊆ 写入范围。

## 禁触清单

- `mocode/core/**`、`mocode/host/config.py`、`mocode/host/runtime.py`、`mocode/host/command.py`、`mocode/host/conversation.py`、`mocode/host/plugin/host.py`、`mocode/host/plugin/context.py`、`mocode/host/plugin/base.py`、`mocode/host/plugin/builtin/**`（W2-A 及后续波领地；loader.py / env.py / install.py 归你）。
- `mocode/cli/**` 中除 `mocode/cli/plugin.py` 外的一切。
- `mocode/providers/**`、`tests/test_retry.py`、`tests/test_provider.py`（W2-B）。
- `tests/test_agent_loop.py`、`tests/test_tools.py`、`tests/test_config.py`、`tests/test_runtime.py`、`tests/test_builtin_plugins.py`、`tests/test_conversations.py`（W2-A）。
- `mocode/plugins/__init__.py`、`docs/ARCHITECTURE.md`、`docs/providers.md`、`TODO.md`、`AGENTS.md`、spec 文件。

## 最终报告格式

工单状态表｜commit 清单（hash+message）｜自测真实结论（命令+退出码）｜偏差与取舍｜未决问题。遇阻塞：报告后停止，不越界自救。
