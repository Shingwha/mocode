"""主应用 - 异步版本（重构版）"""

import asyncio
import os
import threading
import time

from ..sdk import MocodeClient
from ..core import EventType
from ..core.config import Config
from ..core.permission import PermissionMatcher
from ..core.interrupt import InterruptToken
from ..tools import register_all_tools
from .commands import CommandContext, CommandRegistry, register_builtin_commands
from .ui.layout import SimpleLayout
from .ui.widgets import check_esc_key
from .ui.permission_handler import CLIPermissionHandler


class AsyncApp:
    """CLI 应用主类 - 异步版本（重构版）"""

    def __init__(self):
        self.client: MocodeClient | None = None
        self.commands = CommandRegistry()
        self.layout = SimpleLayout()
        self._is_running = False
        # 中断信号
        self._interrupt_token = InterruptToken()
        self._esc_monitor_thread: threading.Thread | None = None
        self._stop_esc_monitor_flag = False

    async def run(self):
        """运行应用（异步入口）"""
        # 清屏
        os.system("cls" if os.name == "nt" else "clear")

        # 初始化
        self.layout.initialize()
        register_all_tools()
        register_builtin_commands(self.commands)
        self._init_client()
        self._setup_event_handlers()
        self._check_rtk()  # 检查 RTK 安装状态

        # 启动 ESC 键监听
        self._start_esc_monitor()

        # 显示欢迎界面
        self.layout.show_welcome("mocode", self.client.config.display_name, os.getcwd())

        # 主循环
        self._is_running = True
        try:
            await self._main_loop()
        except asyncio.CancelledError:
            pass  # Ctrl+C 优雅退出
        except Exception as e:
            self.layout.add_error_message(str(e))
        finally:
            self._is_running = False
            self._stop_esc_monitor_thread()
            self.layout.cleanup()

    def _init_client(self):
        """初始化 MocodeClient"""
        # 创建 CLI 权限处理器
        permission_handler = CLIPermissionHandler(layout=self.layout)

        # 加载配置创建 PermissionMatcher
        config = Config.load()
        permission_matcher = PermissionMatcher(config.permission)

        # 创建 MocodeClient（使用封装好的 API）
        self.client = MocodeClient(
            permission_handler=permission_handler,
            permission_matcher=permission_matcher,
            interrupt_token=self._interrupt_token,
        )

    def _check_rtk(self):
        """检查 RTK 安装状态并提示"""
        if not self.client.config.rtk.enabled:
            return  # RTK 未启用，跳过检测

        from ..tools.rtk_wrapper import check_rtk_installation
        is_installed, message = check_rtk_installation()

        if not is_installed:
            self.layout.add_command_output(f"[RTK] {message}")

    def _start_esc_monitor(self):
        """启动 ESC 键监听线程"""
        def monitor_loop():
            while not self._stop_esc_monitor_flag:
                if check_esc_key():
                    self._interrupt_token.interrupt()
                time.sleep(0.05)  # 50ms 检测间隔

        self._stop_esc_monitor_flag = False
        self._esc_monitor_thread = threading.Thread(target=monitor_loop, daemon=True)
        self._esc_monitor_thread.start()

    def _stop_esc_monitor_thread(self):
        """停止 ESC 键监听线程"""
        self._stop_esc_monitor_flag = True
        if self._esc_monitor_thread and self._esc_monitor_thread.is_alive():
            self._esc_monitor_thread.join(timeout=1.0)

    def _setup_event_handlers(self):
        """设置事件处理器"""
        self.client.event_bus.on(EventType.MESSAGE_ADDED, self._on_message_added)
        self.client.event_bus.on(EventType.TEXT_COMPLETE, self._on_text_complete)
        self.client.event_bus.on(EventType.TOOL_START, self._on_tool_start)
        self.client.event_bus.on(EventType.TOOL_COMPLETE, self._on_tool_complete)
        self.client.event_bus.on(EventType.ERROR, self._on_error)
        self.client.event_bus.on(EventType.INTERRUPTED, self._on_interrupted)

    # ===== 事件处理器 =====

    def _on_message_added(self, event):
        """用户消息添加 - 显示用户消息并启动思考动画"""
        # 用户消息由 input() 回显，这里只记录到对话历史
        # 可选：如果需要显示带格式的历史，可以取消注释下面这行
        # self.layout.add_user_message(event.data.get("content", ""))
        self.layout.set_thinking(True, "Thinking")

    def _on_text_complete(self, event):
        """文本完成 - 停止思考并显示回答"""
        self.layout.set_thinking(False)
        self.layout.add_assistant_message(event.data)

    def _on_tool_start(self, event):
        """工具开始 - 停止思考并显示工具调用"""
        self.layout.set_thinking(False)

        name = event.data["name"]
        args = event.data["args"]
        preview = str(list(args.values())[0])[:50] if args else ""
        self.layout.add_tool_call(name, preview)

    def _on_tool_complete(self, event):
        """工具完成 - 显示结果"""
        result = self._preview_result(event.data["result"])
        self.layout.add_tool_result(result)

    def _on_error(self, event):
        """错误处理"""
        self.layout.set_thinking(False)
        self.layout.add_error_message(str(event.data))

    def _on_interrupted(self, event):
        """中断处理"""
        self.layout.set_thinking(False)
        self.layout.add_assistant_message("[interrupted]")

    # ===== 主循环 =====

    async def _main_loop(self):
        """异步主循环 - 清晰分离输入和输出"""
        while self._is_running:
            try:
                # 获取用户输入（简化布局会自动处理提示符）
                user_input = await self.layout.get_input()
                user_input = user_input.strip()

                if not user_input:
                    continue

                # 处理命令
                if user_input.startswith("/"):
                    ctx = CommandContext(
                        client=self.client,
                        args=user_input,
                        layout=self.layout,
                    )
                    if not await self._execute_command(ctx):
                        break
                    # 检查命令是否设置了待发送消息
                    if ctx.pending_message:
                        await self.client.chat(ctx.pending_message)
                    continue

                # 运行 Agent（用户输入已由 input() 回显）
                await self.client.chat(user_input)

            except (KeyboardInterrupt, EOFError, asyncio.CancelledError):
                break  # 优雅退出
            except Exception as e:
                self.layout.set_thinking(False)
                self.layout.add_error_message(str(e))

    async def _execute_command(self, ctx: CommandContext) -> bool:
        """执行命令（异步包装）"""
        command_name = ctx.args.split()[0] if ctx.args else ""

        # 特殊处理退出命令
        if command_name in ["/exit", "/quit"]:
            self.layout.add_exit_message("Goodbye!")
            return False

        # 将同步命令执行放入线程池
        result = await asyncio.to_thread(self.commands.execute, ctx)

        return result

    def _preview_result(self, result: str) -> str:
        """生成结果预览"""
        lines = result.split("\n")
        preview = lines[0][:60]
        if len(lines) > 1:
            preview += f" ... +{len(lines) - 1} lines"
        elif len(lines[0]) > 60:
            preview += "..."
        return preview
