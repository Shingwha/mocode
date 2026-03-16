"""主应用 - 异步版本（重构版）"""

import asyncio
import os
import sys

from ..core import Config, AsyncAgent, events, EventType, get_system_prompt
from ..core.permission import PermissionMatcher
from ..providers import AsyncOpenAIProvider
from ..tools import register_all_tools
from .commands import CommandContext, CommandRegistry, register_builtin_commands
from .ui.layout import SimpleLayout
from .ui.widgets import SelectMenu
from .ui.colors import BOLD, BLUE, CYAN, RESET, DIM, RED, GREEN


class AsyncApp:
    """CLI 应用主类 - 异步版本（重构版）"""

    def __init__(self):
        self.config: Config = Config.load()
        self.agent: AsyncAgent | None = None
        self.commands = CommandRegistry()
        self.layout = SimpleLayout()
        self._is_running = False

    async def run(self):
        """运行应用（异步入口）"""
        # 清屏
        os.system('cls' if os.name == 'nt' else 'clear')

        # 初始化
        self.layout.initialize()
        register_all_tools()
        register_builtin_commands(self.commands)
        self._init_agent()
        self._setup_event_handlers()

        # 显示欢迎界面
        self.layout.show_welcome("nanocode", self.config.display_name, os.getcwd())

        # 主循环
        self._is_running = True
        try:
            await self._main_loop()
        except Exception as e:
            print(f"\n{RED}Error: {e}{RESET}\n")
        finally:
            self._is_running = False
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

        self.agent = AsyncAgent(
            provider=provider,
            system_prompt=get_system_prompt(),
            max_tokens=self.config.max_tokens,
            permission_matcher=permission_matcher,
        )

    def _setup_event_handlers(self):
        """设置事件处理器"""
        events.on(EventType.MESSAGE_ADDED, self._on_message_added)
        events.on(EventType.TEXT_COMPLETE, self._on_text_complete)
        events.on(EventType.TOOL_START, self._on_tool_start)
        events.on(EventType.TOOL_COMPLETE, self._on_tool_complete)
        events.on(EventType.ERROR, self._on_error)
        events.on(EventType.PERMISSION_ASK, self._on_permission_ask)

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
        print(f"\n{RED}Error: {event.data}{RESET}\n")

    def _on_permission_ask(self, event):
        """权限询问处理"""
        self.layout.set_thinking(False)

        tool_name = event.data["tool_name"]
        tool_args = event.data["tool_args"]
        response_future = event.data["response_future"]

        # 提取目标用于显示
        target = tool_args.get("cmd") or tool_args.get("command") or tool_args.get("path") or ""

        # 显示权限询问
        print(f"\n{BOLD}{CYAN}?{RESET} Permission required for {GREEN}{tool_name}{RESET}")
        if target:
            preview = target[:60] + "..." if len(target) > 60 else target
            print(f"  {DIM}{preview}{RESET}")

        # 显示选择菜单
        menu = SelectMenu(
            title="Choose action",
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
                    )
                    if not await self._execute_command(ctx):
                        break
                    continue

                # 运行 Agent（用户输入已由 input() 回显）
                await self.agent.chat(user_input)

            except (KeyboardInterrupt, EOFError):
                break
            except Exception as e:
                self.layout.set_thinking(False)
                print(f"\n{RED}Error: {e}{RESET}\n")

    async def _execute_command(self, ctx: CommandContext) -> bool:
        """执行命令（异步包装）"""
        command_name = ctx.args.split()[0] if ctx.args else ""

        # 特殊处理退出命令
        if command_name in ["/exit", "/quit"]:
            print(f"\n{DIM}Goodbye!{RESET}\n")
            return False

        # 将同步命令执行放入线程池
        result = await asyncio.to_thread(self.commands.execute, ctx)

        # 命令执行后打印空行并重置 spacing（命令输出不经过 layout 的 spacing）
        print()
        self.layout.reset_spacing()

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