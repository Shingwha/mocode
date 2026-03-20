"""终端布局管理器

使用标准输入输出，通过清晰的视觉设计实现分离：
- 固定的输入提示符
- 清晰的内容区域
- 简单的 spinner 状态
- 统一的空行管理
"""

import asyncio
import shutil
from dataclasses import dataclass
from typing import Optional

from .colors import BG_BLUE, BLUE, BOLD, CYAN, DIM, GREEN, RED, RESET, WHITE
from .textwrap import display_width


class SpacingManager:
    """空行管理器 - 前置空行模式"""

    def __init__(self):
        self._last_type: str | None = None

    def print_space_if_needed(self, current_type: str):
        """根据需要打印前置空行，并记录当前类型"""
        # 首条消息不需要空行
        if self._last_type is not None:
            # tool_call 后的 tool_result 不需要空行
            # user_input 后的消息不需要空行（input() 已带回车换行）
            if (
                not (self._last_type == "tool_call" and current_type == "tool_result")
                and self._last_type != "user_input"
            ):
                print()
        self._last_type = current_type

    def reset(self):
        """重置状态"""
        self._last_type = None


class Layout:
    """终端布局管理器

    使用标准输入输出，通过清晰的视觉设计实现分离：
    - 固定的输入提示符
    - 清晰的内容区域
    - 简单的 spinner 状态
    - 统一的空行管理
    """

    def __init__(self):
        self._spinner: Optional[asyncio.Task] = None
        self._is_thinking: bool = False
        self._spinner_frame: str = ""
        self._current_input: str = ""
        self._status_line: str = ""
        self._spacing = SpacingManager()

    def initialize(self):
        """初始化"""
        self._spacing.reset()

    def cleanup(self):
        """清理"""
        if self._spinner:
            self._spinner.cancel()

    def show_welcome(self, name: str, model: str, cwd: str):
        """显示欢迎信息"""
        print(
            f"{BOLD}{name}{RESET}{DIM}v0.1.0{RESET} │ {CYAN}{model}{RESET} │ {DIM}{cwd}{RESET}"
        )
        # 欢迎消息后不重置，第一条输入前不打印空行

    def add_user_message(self, content: str):
        """Add user message (for history display)"""
        self._spacing.print_space_if_needed("user_history")
        display_line, _ = self._highlight_line(content)
        print(display_line)

    def add_assistant_message(self, content: str):
        """添加助手消息"""
        self._spacing.print_space_if_needed("assistant")
        lines = content.split("\n")
        if len(lines) == 1:
            print(f"{CYAN}○{RESET} {content}")
        else:
            print(f"{CYAN}○{RESET} {lines[0]}")
            for line in lines[1:]:
                print(f"  {line}")

    def add_tool_call(self, name: str, args_preview: str = ""):
        """添加工具调用"""
        self._spacing.print_space_if_needed("tool_call")
        args_str = f"{DIM}({args_preview}){RESET}" if args_preview else ""
        print(f"{CYAN}◆{RESET} {GREEN}{name}{RESET}{args_str}")

    def add_tool_result(self, preview: str, max_length: int = 60):
        """添加工具结果"""
        self._spacing.print_space_if_needed("tool_result")
        text = preview
        if len(text) > max_length:
            text = text[: max_length - 3] + "..."
        print(f"  {DIM}⎿ {text}{RESET}")

    def add_tool_error(self, error: str):
        """添加工具错误"""
        self._spacing.print_space_if_needed("error")
        print(f"  {RED}✗ {error}{RESET}")

    def add_command_output(self, text: str):
        """添加命令输出"""
        self._spacing.print_space_if_needed("command")
        print(text)

    def add_error_message(self, text: str):
        """添加错误消息"""
        self._spacing.print_space_if_needed("error")
        print(f"{RED}Error: {text}{RESET}")

    def add_permission_ask_title(self, tool_name: str, preview: str = ""):
        """添加权限询问标题"""
        self._spacing.print_space_if_needed("permission")
        print(
            f"{BOLD}{CYAN}?{RESET} Permission required for {GREEN}{tool_name}{RESET}{preview}"
        )

    def add_exit_message(self, text: str):
        """添加退出消息"""
        self._spacing.print_space_if_needed("exit")
        print(f"{DIM}{text}{RESET}")

    def set_thinking(self, thinking: bool, text: str = "Thinking"):
        """设置思考状态"""
        if thinking == self._is_thinking:
            return

        self._is_thinking = thinking

        if thinking:
            # 开始 spinner
            self._start_spinner(text)
        else:
            # 停止 spinner
            self._stop_spinner()

    def _start_spinner(self, text: str):
        """启动 spinner"""
        import sys

        frames = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

        async def animate():
            import asyncio

            i = 0
            while self._is_thinking:
                frame = frames[i % len(frames)]
                # 使用 \r 回到行首，不换行
                print(f"\r{CYAN}{frame}{RESET} {DIM}{text}{RESET}", end="", flush=True)
                i += 1
                await asyncio.sleep(0.08)

        self._spinner = asyncio.create_task(animate())

    def _stop_spinner(self):
        """停止 spinner"""
        if self._spinner:
            self._spinner.cancel()
            self._spinner = None
        # 清除 spinner 行
        import shutil

        width = shutil.get_terminal_size().columns
        print(f"\r{' ' * width}\r", end="")
        self._is_thinking = False

    def reset_spacing(self):
        """重置 spacing manager（用于命令执行后）"""
        self._spacing.reset()

    def render_session_history(self, messages: list[dict]):
        """渲染 session 历史消息

        Args:
            messages: OpenAI 格式的消息列表
        """
        if not messages:
            return

        # 重置 spacing 状态
        self._spacing.reset()

        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")
            tool_calls = msg.get("tool_calls", [])

            # 跳过 system 消息
            if role == "system":
                continue

            if role == "user":
                self.add_user_message(content)

            elif role == "assistant":
                # 助手文本内容
                if content:
                    self.add_assistant_message(content)
                # 工具调用
                for tool_call in tool_calls:
                    func = tool_call.get("function", {})
                    name = func.get("name", "unknown")
                    args = func.get("arguments", "{}")
                    # 解析参数获取预览
                    try:
                        import json
                        args_dict = json.loads(args)
                        preview = str(list(args_dict.values())[0])[:50] if args_dict else ""
                    except:
                        preview = ""
                    self.add_tool_call(name, preview)

            elif role == "tool":
                # 工具结果跳过，不显示
                pass

    def print_space_if_needed(self, current_type: str):
        """打印前置空行（公共接口，供外部调用）"""
        self._spacing.print_space_if_needed(current_type)

    def _highlight_line(self, content: str, prefix: str = ">") -> tuple[str, int]:
        """Create a highlighted line with background color.

        Args:
            content: Text content to highlight
            prefix: Prefix character (default ">")

        Returns:
            Tuple of (highlighted_line, num_lines_occupied)
        """
        width = shutil.get_terminal_size().columns
        prefix_width = len(prefix) + 1  # prefix + space

        text_width = display_width(content)
        total_width = prefix_width + text_width
        num_lines = (total_width + width - 1) // width

        display_line = f"{BG_BLUE}{BOLD}{WHITE}{prefix}{RESET}{BG_BLUE}{BOLD} {content} {RESET}"
        padding_width = width * num_lines - text_width - prefix_width
        padding = " " * max(0, padding_width)

        return f"{display_line}{padding}{RESET}", num_lines

    async def get_input(self) -> str:
        """Get user input with background highlighting."""
        # Ensure spinner is stopped
        self._stop_spinner()

        # Print leading space and record type
        self._spacing.print_space_if_needed("user_input")

        # Show input prompt (input echoes user input)
        prompt = f"{BOLD}{BLUE}>{RESET} "

        try:
            # Use standard input (user input is echoed)
            user_input = await asyncio.to_thread(input, prompt)

            # After input, highlight the line with background
            if user_input.strip():
                display_line, num_lines = self._highlight_line(user_input)
                # Move cursor up, clear line, print highlighted version
                print(f"\033[{num_lines}A\r\033[K{display_line}")

            return user_input
        except EOFError:
            return ""
