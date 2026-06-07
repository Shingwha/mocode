# MoCode 重构看板

> 来源：`REFACTOR_REPORT.md` 精选任务
> 创建时间：2025-07

---

- [x] **R-02** — 拆分 `app/cli/commands/builtin.py`（509 行）
  - Status: done
  - Workload: 3-4h
  - Description: 单文件包含 `_connect`、`_connect_edit`（93 行 / 嵌套 8 层）等十余个命令处理器
  - File: `mocode/app/cli/commands/builtin.py`
  - Plan: |
      按功能域拆分为子模块：
      - `connect.py` — /connect + /connect:edit + /connect:extra_body
      - `model.py` — /model
      - `session.py` — /session (list, show, rm, clear, export)
      - `provider.py` — /provider (list, info)
      - `misc.py` — /help, /quit, /clear, /compact
      - `_connect_edit()` 改为策略模式（EDITORS dict）
  - Approach: 按功能域将 509 行 builtin.py 拆为 4 个子模块（connect/model/session/misc），builtin.py 保留为聚合层并 re-export 所有 handler 函数以保持向后兼容
  - Changes:
    - 新建 `connect.py`（258 行）— /connect 全部逻辑
    - 新建 `model.py`（69 行）— /model 命令
    - 新建 `session.py`（123 行）— /resume + /export
    - 新建 `misc.py`（106 行）— /quit, /help, /clear, /copy, /compact
    - `builtin.py` 从 509 行缩减为 59 行聚合模块
    - 更新 `test_connect.py` 的 mock patch 路径指向 `connect` 子模块
  - Files: mocode/app/cli/commands/{builtin,connect,model,session,misc}.py, tests/test_connect.py
  - Tests: 342 passed
  - Commit: 3acdd5b

- [x] **R-03** — 拆分 `session.py` 中的 `_render_session_md()`（150 行 / 嵌套 8 层）
  - Status: done
  - Workload: 2-3h
  - Description: God Function — 在单个函数中完成全部渲染，使用 while 状态机
  - File: `mocode/app/session.py`
  - Plan: |
      拆为子函数：
      - `_render_frontmatter(session)`
      - `_render_overview(session)`
      - `_render_turn(turn_msgs, turn_num)`
      - `_render_tool_call(tc)`
      - `_render_tool_result(tc_id, content, msgs)`
      将 while 循环改为 groupby 或 walk_messages() visitor
  - Approach: 将 150 行的 _render_session_md 拆分为 7 个专用函数：_render_frontmatter、_render_overview、_render_tool_call、_render_tool_result、_render_assistant_message、_render_turn 和 _walk_messages。使用 visitor 模式替代嵌套 while 循环。
  - Changes:
    - 新增 `_render_frontmatter(session)` — 渲染 YAML frontmatter
    - 新增 `_render_overview(session)` — 渲染标题和统计信息
    - 新增 `_render_tool_call(tc)` — 渲染单个工具调用
    - 新增 `_render_tool_result(tc_id, content, msgs)` — 渲染工具结果
    - 新增 `_render_assistant_message(msg, msgs)` — 渲染助手消息
    - 新增 `_render_turn(turn_msgs, turn_num, all_msgs)` — 渲染完整对话轮次
    - 新增 `_walk_messages(msgs)` — 将消息按轮次分组
    - 重构 `_render_session_md` 使用新函数组合
  - Files: mocode/app/session.py
  - Tests: 342 passed
  - Commit: 1237b5a

---

- [x] **R-06** — 拆分 `tools/search.py`（478 行）
  - Status: done
  - Workload: 2h
  - Description: GlobTool 和 GrepTool 两个独立工具合在一个文件，grep 有重复实现
  - File: `mocode/tools/search.py`
  - Plan: |
      1. 拆为 `tools/glob.py` + `tools/grep.py`
      2. 合并 `_grep_vfs()` / `_grep_real_fs()` 为统一实现：
         `def _grep(pattern, file_iter, *, output_mode, max_results, context_lines)`
  - Approach: 将 IGNORE_DIRS/TYPE_MAP/TEXT_EXTENSIONS 等共享常量和辅助函数移入 utils.py，拆出 glob.py（95 行）和 grep.py（247 行），search.py 保留为向后兼容的 re-export 层
  - Changes:
    - `utils.py` 新增 IGNORE_DIRS、TYPE_MAP、TEXT_EXTENSIONS、_GLOB_MAX、_is_text_file、_get_type_filter、_walk_text_files、_is_vfs_path、_match_vfs_type
    - 新建 `tools/glob.py`（95 行）— GlobTool + _glob
    - 新建 `tools/grep.py`（247 行）— GrepTool + _grep + _search_files + 变体
    - `tools/search.py` 从 478 行缩减为 23 行 re-export 层
    - 更新 `tools/__init__.py` 直接从 glob/grep 模块导入
  - Files: mocode/tools/{utils,search,glob,grep}.py
  - Tests: 342 passed
  - Commit: b15ed92

---

- [x] **R-07** — 将 `SubAgent` 引擎从 `tools/` 提升到 `core/`
  - Status: done
  - Workload: 1-2h
  - Description: 子代理是核心概念，不应仅放在 tools 层
  - File: `mocode/tools/subagent.py`
  - Plan: |
      - `SubAgent`、`SubAgentConfig`、`SubAgentResult` → `core/subagent.py`
      - `tools/subagent.py` 仅保留 `SubAgentTool` 工具包装，从 core import
  - Approach: 将 SubAgent、SubAgentConfig、SubAgentResult 移至 core/subagent.py，tools/subagent.py 仅保留 SubAgentTool 工具包装，从 core 导入。更新所有导入路径。
  - Changes:
    - 新建 `core/subagent.py`（78 行）— SubAgent、SubAgentConfig、SubAgentResult
    - 修改 `core/__init__.py` — 导出新类
    - 修改 `tools/subagent.py` — 移除类定义，仅保留 SubAgentTool
    - 修改 `tools/__init__.py` — 从 core 导入 SubAgent 类
    - 修改 `tests/test_subagent.py` — 更新导入路径
  - Files: mocode/core/subagent.py, mocode/core/__init__.py, mocode/tools/subagent.py, mocode/tools/__init__.py, tests/test_subagent.py
  - Tests: 342 passed
  - Commit: 4e8cab8
