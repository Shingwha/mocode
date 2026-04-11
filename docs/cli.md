# CLI 模块

## 架构

```
                          ┌─────────────────────────────────────────┐
                          │              CLIApp (app.py)            │
                          │           薄编排层，协调所有组件           │
                          └──┬──────┬──────┬──────┬──────┬─────────┘
                             │      │      │      │      │
              ┌──────────────┘      │      │      │      └──────────────┐
              v                     v      v      v                     v
    ┌─────────────────┐  ┌──────────┐  ┌──────┐  ┌──────────┐  ┌──────────────┐
    │    Display      │  │ Registry │  │ ESC  │  │  Event   │  │  Permission  │
    │  (ui/display)   │  │ +Executor│  │Monitor│  │ Handler  │  │   Handler    │
    │  终端输出管理     │  │ 命令注册  │  │ 中断监听│  │ 事件桥接  │  │  交互式授权   │
    └─────────────────┘  └────┬─────┘  └──────┘  └────┬─────┘  └──────────────┘
                              │                       │               │
              ┌───────────────┘                       │               │
              v 8 个内置命令                            │               │
    ┌──────────────────┐                              │               │
    │ /help /provider  │                              │               │
    │ /mode  /session  │                              │               │
    │ /clear /skills   │                              │               │
    │ /plugin /exit    │                              │               │
    └──────────────────┘                              │               │
                                                      v               v
                                              ┌─────────────────────────┐
                                              │      MocodeCore         │
                                              │    (core/orchestrator)   │
                                              │  AsyncAgent · EventBus  │
                                              │  Config · Sessions      │
                                              │  Plugins · Skills       │
                                              └─────────────────────────┘
```

**核心设计**: `MocodeCore` 完全不感知终端。Core 通过 `EventBus` 发射事件，`CLIEventHandler` 将事件翻译为 `Display` 调用。CLI 通过 `MocodeCore` 公共 API 驱动业务逻辑。

## 目录结构

```
cli/
├── app.py                   # CLIApp 入口，生命周期管理
├── commands/
│   ├── base.py              # Command ABC, CommandContext, CommandRegistry, @command
│   ├── executor.py          # CommandExecutor (匹配/模糊派发/菜单回退)
│   ├── builtin.py           # QuitCommand, ClearCommand, HelpCommand
│   ├── provider.py          # ProviderCommand (/provider)
│   ├── mode.py              # ModeCommand (/mode)
│   ├── session.py           # SessionCommand (/session)
│   ├── skills.py            # SkillsCommand (/skills)
│   ├── plugin.py            # PluginCommand (/plugin)
│   └── utils.py             # resolve_selection, parse_selection_arg
├── events/
│   └── handler.py           # CLIEventHandler (EventBus → Display 桥接)
├── monitor/
│   └── esc.py               # ESCMonitor (后台线程轮询 ESC 键)
└── ui/
    ├── display.py           # Display + SpacingManager (终端输出)
    ├── components.py        # Spinner, Input, Select, MultiSelect
    ├── prompt.py            # select(), ask(), confirm(), Wizard, MenuAction
    ├── permission.py        # CLIPermissionHandler (交互式权限菜单)
    ├── keyboard.py          # getch(), esc_paused(), check_esc_key()
    ├── styles.py            # ANSI 常量, MessagePreset, 样式 dataclass
    └── textwrap.py          # display_width(), truncate_text(), wrap_text() (CJK)
```

## 应用生命周期

```
main() → _run_cli() → CLIApp.run()
  │
  ├─ _initialize()
  │    清屏 → Display 初始化 → 注册工具 → 注册命令
  │    创建 MocodeCore (含 agent/provider/plugins)
  │    创建 CLIEventHandler (订阅 EventBus)
  │    启动 ESCMonitor → 显示欢迎语
  │
  ├─ _main_loop()
  │    等待用户输入
  │    ├─ /开头 → CommandContext → CommandExecutor.execute()
  │    │         返回 False 则退出; pending_message 则发送给 chat()
  │    └─ 其他 → client.chat(input)
  │
  └─ _shutdown()
       保存 session → 停止 ESCMonitor → 清理 Display
```

## 命令系统

### 注册与派发

**注册**: `BUILTIN_COMMANDS` 列举 8 个命令类，`register_builtin_commands()` 实例化并注册到 `CommandRegistry`（单例）。每个命令通过 `@command(name, *aliases, description=...)` 声明元数据。

**派发** (`CommandExecutor.execute()`):
1. `registry.find_matches(name)` 前缀匹配
2. 零匹配 → 打印 "Unknown command"
3. 单匹配 → `asyncio.to_thread(cmd.execute(ctx))` 执行
4. 多匹配 → 弹出 `select()` 交互菜单
5. `/exit` 返回 `False` 中断主循环

### 命令基类

`Command` (ABC) 提供 `execute(ctx) -> bool` 契约及共享方法：

| 方法 | 用途 |
|------|------|
| `_info/_success/_error/_output(msg)` | 格式化输出 |
| `_select_from_list(ctx, title, items)` | 标准化列表选择 |
| `_route_subcommand(ctx, arg, handlers)` | 子命令路由（`{name: method_name}`） |
| `confirm_delete(ctx, name)` | 删除确认对话框 |

`CommandContext` dataclass: `client`, `args`, `display`, `pending_message`, `loop`，外加 `config` / `agent` 属性。

### 内置命令

| 命令 | 别名 | 文件 | 说明 |
|------|------|------|------|
| `/help` | `/`, `/h`, `/?` | builtin.py | 命令列表或交互菜单 |
| `/provider` | `/p` | provider.py | 切换 provider/model（交互式，支持管理） |
| `/mode` | | mode.py | 切换 normal/yolo 模式 |
| `/session` | `/s` | session.py | 恢复历史会话 |
| `/clear` | `/c` | builtin.py | 清空对话（自动保存 session） |
| `/skills` | | skills.py | 安装/卸载/更新/激活技能 |
| `/plugin` | | plugin.py | 安装/卸载/更新/开关插件 |
| `/exit` | `/q`, `/quit` | builtin.py | 退出 |

## UI 组件

### Display (`ui/display.py`)

集中管理所有终端输出。`SpacingManager` 控制块间空行：`tool_call → tool_result` 无空行，其余转换间加空行。

关键方法: `welcome()`, `user_message()`, `assistant_message()`, `tool_call()`, `tool_result()`, `error()`, `get_input()` (async), `render_history()`。

### 交互组件 (`ui/components.py`)

| 组件 | 用途 |
|------|------|
| `Spinner` | 异步加载动画（"Thinking"） |
| `Input` | 单行文本输入（ESC 取消，完成后蓝色高亮） |
| `Select[T]` | 单选菜单（方向键导航，分页，终端宽度截断） |
| `MultiSelect[T]` | 多选菜单（Space 切换，`a` 全选） |

### 高级 API (`ui/prompt.py`)

```python
select(title, choices)  →  T | None    # 单选
ask(message)            →  str | None   # 文本输入
confirm(title)          →  bool         # Yes/No
Wizard                  →  多步输入流（可取消）
```

`MenuAction` 枚举: `EXIT`, `BACK`, `MANAGE`, `CONFIRM`, `CANCEL`, `ADD`, `EDIT`, `DELETE`, `DONE`, `DISABLED`, `LIST`, `INFO`。`MenuItem` 工厂构建 `(MenuAction, styled_label)` 元组。

### 权限交互 (`ui/permission.py`)

`CLIPermissionHandler` 实现 core 的 `PermissionHandler` 接口，弹出交互菜单：Allow / Deny / Type something。

### 键盘 (`ui/keyboard.py`)

- `getch(with_arrows)` — 跨平台原始按键读取（Windows: msvcrt, Unix: tty/termios）
- `esc_paused()` — 上下文管理器，交互菜单期间暂停 ESC 监听
- `check_esc_key()` — 非阻塞 ESC 检测

### 文本处理 (`ui/textwrap.py`)

CJK 感知的文本工具：`display_width()` (中文 2 宽度), `truncate_text()`, `wrap_text()` (保留 ANSI), `strip_ansi()`。

## 事件桥接

`CLIEventHandler` 订阅 `MocodeCore.event_bus` 的 6 个事件并翻译为 `Display` 调用：

| EventBus 事件 | Display 方法 |
|---------------|-------------|
| `MESSAGE_ADDED` | `user_message()` 或 `assistant_message()` |
| `TEXT_COMPLETE` | `assistant_message()` |
| `TOOL_START` | `tool_call()` + Spinner 启动 |
| `TOOL_COMPLETE` | `tool_result()` + Spinner 停止 |
| `ERROR` | `error()` |
| `INTERRUPTED` | `info("Interrupted")` |

## 快捷键

| 按键 | 动作 |
|------|------|
| ESC | 中断当前操作（通过 `ESCMonitor` 后台线程 50ms 轮询） |
