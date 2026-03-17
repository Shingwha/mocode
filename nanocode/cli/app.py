"""主应用 - 异步版本（重构版）"""

import asyncio
import os
import threading
import time

from ..core import AsyncAgent, Config, EventType, get_event_bus, get_system_prompt
from ..core.permission import PermissionMatcher
from ..core.interrupt import InterruptToken
from ..providers import AsyncOpenAIProvider
from ..skills import SkillManager
from ..tools import register_all_tools
from .commands import CommandContext, CommandRegistry, register_builtin_commands
from .ui.colors import BLUE, BOLD, DIM, GREEN, RESET
from .ui.layout import SimpleLayout
from .ui.widgets import SelectMenu, check_esc_key


class AsyncApp:
    """CLI 应用主类 - 异步版本（重构版）"""

    def __init__(self):
        self.config: Config = Config.load()
        self.agent: AsyncAgent | None = None
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
        self._init_agent()
        self._setup_event_handlers()

        # 启动 ESC 键监听
        self._start_esc_monitor()

        # 显示欢迎界面
        self.layout.show_welcome("nanocode", self.config.display_name, os.getcwd())

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

    def _init_agent(self):
        """初始化 Agent"""
        provider = AsyncOpenAIProvider(
            api_key=self.config.api_key,
            model=self.config.model,
            base_url=self.config.base_url or None,
        )

        # 创建权限匹配器
        permission_matcher = PermissionMatcher(self.config.permission)

        # 初始化 SkillManager 并获取系统提示
        skill_manager = SkillManager.get_instance()
        system_prompt = get_system_prompt(skill_manager)

        self.agent = AsyncAgent(
            provider=provider,
            system_prompt=system_prompt,
            max_tokens=self.config.max_tokens,
            permission_matcher=permission_matcher,
            interrupt_token=self._interrupt_token,
        )

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
        event_bus = get_event_bus()
        event_bus.on(EventType.MESSAGE_ADDED, self._on_message_added)
        event_bus.on(EventType.TEXT_COMPLETE, self._on_text_complete)
        event_bus.on(EventType.TOOL_START, self._on_tool_start)
        event_bus.on(EventType.TOOL_COMPLETE, self._on_tool_complete)
        event_bus.on(EventType.ERROR, self._on_error)
        event_bus.on(EventType.PERMISSION_ASK, self._on_permission_ask)
        event_bus.on(EventType.INTERRUPTED, self._on_interrupted)

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

    def _on_permission_ask(self, event):
        """权限询问处理"""
        self.layout.set_thinking(False)

        tool_name = event.data["tool_name"]
        tool_args = event.data["tool_args"]
        response_future = event.data["response_future"]

        # 提取目标用于显示
        target = (
            tool_args.get("cmd")
            or tool_args.get("command")
            or tool_args.get("path")
            or ""
        )

        # 生成标题
        if target:
            preview = target[:60] + "..." if len(target) > 60 else target
            title = f"Permission required for {GREEN}{tool_name}{RESET} ({DIM}{preview}{RESET})"
        else:
            title = f"Permission required for {GREEN}{tool_name}{RESET}"

        # 打印前置空行
        self.layout._spacing.print_space_if_needed("permission")

        # 显示选择菜单
        menu = SelectMenu(
            title=title,
            choices=[
                ("allow", "Allow (execute the tool)"),
                ("deny", "Deny (cancel the operation)"),
                ("input", "Type something (provide custom response)"),
            ],
        )

        result = menu.show()

        if result == "allow":
            response_future.set_result("allow")
        elif result == "deny" or result is None:
            response_future.set_result("deny")
        elif result == "input":
            # 获取用户输入
            try:
                print(f"\n{BOLD}{BLUE}>{RESET} ", end="", flush=True)
                user_input = input()
                response_future.set_result(user_input)
            except (KeyboardInterrupt, EOFError):
                response_future.set_result("deny")

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
                        config=self.config,
                        agent=self.agent,
                        args=user_input,
                        layout=self.layout,
                    )
                    if not await self._execute_command(ctx):
                        break
                    # 检查命令是否设置了待发送消息
                    if ctx.pending_message:
                        await self.agent.chat(ctx.pending_message)
                    continue

                # 运行 Agent（用户输入已由 input() 回显）
                await self.agent.chat(user_input)

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
