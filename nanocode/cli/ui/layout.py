"""
终端布局管理器 - 固定底部输入行设计

设计理念:
- 底部固定输入行：始终可见，不受上方内容影响
- 滚动内容区：输入行上方显示所有对话内容
- 视觉分离：用户消息、AI回复、工具调用有清晰区分
"""

import sys
import shutil
import asyncio
from typing import Optional, Callable
from dataclasses import dataclass, field
from enum import Enum, auto

from .colors import RESET, BOLD, DIM, CYAN, GREEN, BLUE, YELLOW, RED, WHITE, BG_BLUE, BG_BLACK, BG_CYAN


class SpacingManager:
    """
    空行管理器

    规则：每条消息（除了最后一条是工具调用）后面跟着空行
    """

    def __init__(self):
        self._last_type: str | None = None

    def before_message(self, current_type: str):
        """在打印消息前调用，只记录类型，不打印空行"""
        self._last_type = current_type

    def after_message(self):
        """在打印消息后调用，根据类型决定是否打印空行"""
        # 工具调用后不打印空行（因为后面跟着工具结果）
        if self._last_type == "tool_call":
            return
        # 其他情况打印空行
        print()

    def reset(self):
        """重置状态"""
        self._last_type = None


class MessageRole(Enum):
    """消息角色类型"""
    USER = auto()
    ASSISTANT = auto()
    SYSTEM = auto()
    TOOL = auto()


@dataclass
class Message:
    """消息对象"""
    role: MessageRole
    content: str
    metadata: dict = field(default_factory=dict)


class TerminalLayout:
    """
    终端布局管理器
    
    负责:
    1. 固定底部输入行
    2. 管理内容区滚动
    3. 处理终端大小变化
    4. 协调输入和输出
    """

    # ANSI 控制序列
    SAVE_CURSOR = "\033[s"
    RESTORE_CURSOR = "\033[u"
    CLEAR_LINE = "\033[K"
    CLEAR_SCREEN = "\033[2J"
    MOVE_UP = "\033[{}A"
    MOVE_DOWN = "\033[{}B"
    MOVE_TO_COLUMN = "\033[{}G"
    HIDE_CURSOR = "\033[?25l"
    SHOW_CURSOR = "\033[?25h"
    ENABLE_ALT_BUFFER = "\033[?1049h"
    DISABLE_ALT_BUFFER = "\033[?1049l"

    def __init__(self):
        self._width: int = shutil.get_terminal_size().columns
        self._height: int = shutil.get_terminal_size().lines
        self._input_buffer: str = ""
        self._cursor_pos: int = 0
        self._is_input_active: bool = False
        self._content_lines: list[str] = []  # 内容区所有行
        self._max_content_lines: int = self._height - 3  # 预留输入行和状态行
        self._input_prompt: str = "> "
        self._status_text: str = ""
        self._spinner_frame: str = ""
        self._spinner_text: str = ""
        self._is_thinking: bool = False
        self._last_output_lines: int = 0  # 上次输出的行数

    # ========== 初始化 ==========

    def initialize(self):
        """初始化终端布局"""
        self._update_size()
        self._setup_terminal()
        self._draw_initial_layout()

    def cleanup(self):
        """清理终端设置"""
        print(self.SHOW_CURSOR, end="")
        print(f"\n{DIM}Goodbye!{RESET}\n")

    def _setup_terminal(self):
        """设置终端"""
        # 不进入备用缓冲区，保持主缓冲区
        # 隐藏光标将在需要时控制
        pass

    def _update_size(self):
        """更新终端尺寸"""
        self._width = shutil.get_terminal_size().columns
        self._height = shutil.get_terminal_size().lines
        self._max_content_lines = max(1, self._height - 3)

    # ========== 布局绘制 ==========

    def _draw_initial_layout(self):
        """绘制初始布局"""
        # 清屏
        print(self.CLEAR_SCREEN, end="")
        # 移动光标到顶部
        print("\033[H", end="")
        # 绘制状态栏
        self._draw_status_bar()
        # 绘制输入行
        self._draw_input_line()

    def _draw_status_bar(self):
        """绘制顶部状态栏"""
        # 移动到第一行
        print("\033[1;1H", end="")
        status = self._status_text.ljust(self._width)[:self._width]
        print(f"{DIM}{status}{RESET}", end="")

    def _draw_input_line(self, clear_first: bool = True):
        """绘制底部输入行"""
        # 移动到倒数第二行（留给状态行）
        input_row = self._height - 1
        print(f"\033[{input_row};1H", end="")
        
        if clear_first:
            print(self.CLEAR_LINE, end="")
        
        # 显示 spinner 或提示符
        if self._is_thinking and self._spinner_frame:
            prompt = f"{CYAN}{self._spinner_frame}{RESET} {DIM}{self._spinner_text}{RESET}"
        else:
            prompt = f"{BOLD}{BLUE}>{RESET} "
        
        # 显示输入内容
        input_display = self._input_buffer[-(self._width - 10):]  # 截断以适应屏幕
        line = f"{prompt}{input_display}"
        print(line, end="")
        
        # 确保光标在行尾
        print(f"\033[{input_row};{len(line) + 1}H", end="", flush=True)

    def _draw_content_area(self):
        """重绘内容区"""
        # 保存当前位置
        print(self.SAVE_CURSOR, end="")
        
        # 移动到内容区开始（第2行，第1行是状态栏）
        print("\033[2;1H", end="")
        
        # 清屏内容区
        for _ in range(self._max_content_lines):
            print(self.CLEAR_LINE)
        
        # 重新移动到内容区开始
        print("\033[2;1H", end="")
        
        # 显示最近的内容（适应屏幕）
        visible_lines = self._content_lines[-self._max_content_lines:]
        for line in visible_lines:
            # 确保每行不超出宽度
            if len(line) > self._width:
                print(line[:self._width])
            else:
                print(line)
        
        # 恢复光标位置到输入行
        self._draw_input_line()

    # ========== 内容管理 ==========

    def add_content(self, text: str, scroll: bool = True):
        """添加内容到内容区"""
        # 分割多行文本
        lines = text.split('\n')
        self._content_lines.extend(lines)
        
        # 限制历史记录（防止内存无限增长）
        if len(self._content_lines) > 1000:
            self._content_lines = self._content_lines[-500:]
        
        # 更新显示
        if scroll:
            self._refresh_display()

    def add_message(self, role: MessageRole, content: str, **metadata):
        """添加格式化的消息"""
        msg = Message(role=role, content=content, metadata=metadata)
        formatted = self._format_message(msg)
        self.add_content(formatted)

    def _format_message(self, msg: Message) -> str:
        """格式化消息为显示文本"""
        if msg.role == MessageRole.USER:
            # 用户消息：简洁，带提示符
            lines = msg.content.split('\n')
            if len(lines) == 1:
                return f"{BOLD}{BLUE}>{RESET} {msg.content}"
            else:
                result = [f"{BOLD}{BLUE}>{RESET}"]
                for line in lines:
                    result.append(f"  {line}")
                return '\n'.join(result)
        
        elif msg.role == MessageRole.ASSISTANT:
            # AI回复：普通文本，可选前缀
            return msg.content
        
        elif msg.role == MessageRole.TOOL:
            # 工具调用：特殊格式
            tool_name = msg.metadata.get('tool_name', 'tool')
            icon = msg.metadata.get('icon', '▶')
            result_icon = msg.metadata.get('result_icon', '⎿')
            is_result = msg.metadata.get('is_result', False)
            
            if is_result:
                return f"  {DIM}{result_icon} {msg.content}{RESET}"
            else:
                return f"{CYAN}{icon}{RESET} {GREEN}{tool_name}{RESET}{DIM}({msg.content}){RESET}"
        
        elif msg.role == MessageRole.SYSTEM:
            # 系统消息：淡化显示
            return f"{DIM}{msg.content}{RESET}"
        
        return msg.content

    # ========== 输入管理 ==========

    def start_input(self) -> str:
        """开始读取用户输入（阻塞）"""
        self._is_input_active = True
        self._input_buffer = ""
        self._cursor_pos = 0
        self._draw_input_line()
        
        try:
            while True:
                char = self._get_char()
                
                if char == '\r' or char == '\n':  # Enter
                    break
                elif char == '\x03':  # Ctrl+C
                    raise KeyboardInterrupt()
                elif char == '\x04':  # Ctrl+D
                    raise EOFError()
                elif char == '\x7f':  # Backspace
                    if self._cursor_pos > 0:
                        self._input_buffer = self._input_buffer[:-1]
                        self._cursor_pos -= 1
                        self._draw_input_line()
                elif char == '\x1b':  # ESC sequences
                    # 处理方向键等
                    next_char = self._get_char()
                    if next_char == '[':
                        self._get_char()  # 消耗第三个字符
                    continue
                elif char.isprintable():
                    self._input_buffer += char
                    self._cursor_pos += 1
                    self._draw_input_line()
                
                # 刷新显示
                sys.stdout.flush()
        
        finally:
            self._is_input_active = False
            # 在输入完成后添加空行
            print()
        
        return self._input_buffer.strip()

    async def start_input_async(self) -> str:
        """异步读取用户输入"""
        return await asyncio.to_thread(self.start_input)

    def _get_char(self) -> str:
        """获取单个字符输入（跨平台）"""
        import msvcrt
        return msvcrt.getch().decode('utf-8', errors='ignore')

    # ========== Spinner 管理 ==========

    def set_thinking(self, thinking: bool, text: str = "Thinking"):
        """设置思考状态"""
        self._is_thinking = thinking
        self._spinner_text = text
        if thinking:
            self._start_spinner_task()
        self._draw_input_line()

    def _start_spinner_task(self):
        """启动 spinner 动画任务"""
        frames = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
        
        async def animate():
            i = 0
            while self._is_thinking:
                self._spinner_frame = frames[i % len(frames)]
                if self._is_input_active:
                    self._draw_input_line()
                i += 1
                await asyncio.sleep(0.08)
        
        # 创建后台任务
        asyncio.create_task(animate())

    def set_spinner_frame(self, frame: str):
        """设置当前 spinner 帧"""
        self._spinner_frame = frame
        if self._is_input_active:
            self._draw_input_line()

    # ========== 状态管理 ==========

    def set_status(self, text: str):
        """设置状态栏文本"""
        self._status_text = text
        self._draw_status_bar()
        if self._is_input_active:
            self._draw_input_line()

    # ========== 刷新 ==========

    def _refresh_display(self):
        """刷新整个显示"""
        # 保存当前输入状态
        was_input_active = self._is_input_active
        
        # 重绘内容区
        self._draw_content_area()
        
        # 恢复输入状态
        if was_input_active:
            self._draw_input_line()

    def handle_resize(self):
        """处理终端大小变化"""
        self._update_size()
        self._refresh_display()

    # ========== 工具方法 ==========

    def clear_content(self):
        """清空内容区"""
        self._content_lines.clear()
        self._refresh_display()

    def show_welcome(self, name: str, model: str, cwd: str):
        """显示欢迎信息"""
        welcome = f"{BOLD}{name}{RESET} {DIM}v0.1.0{RESET}  │  {CYAN}{model}{RESET}  │  {DIM}{cwd}{RESET}"
        self.set_status(welcome)


# ========== 简化版布局（使用标准输入输出） ==========

class SimpleLayout:
    """
    简化版布局管理器

    使用标准输入输出，但通过清晰的视觉设计实现分离：
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
        print(f"{BOLD}{name}{RESET} {DIM}v0.1.0{RESET}  │  {CYAN}{model}{RESET}  │  {DIM}{cwd}{RESET}")
        # 欢迎消息后不重置，第一条输入前不打印空行

    def add_user_message(self, content: str):
        """添加用户消息（用于历史记录显示）"""
        pass  # 用户输入已由 input() 回显

    def add_assistant_message(self, content: str):
        """添加助手消息"""
        self._spacing.before_message("assistant")
        lines = content.split('\n')
        if len(lines) == 1:
            print(f"{CYAN}*{RESET} {content}")
        else:
            print(f"{CYAN}*{RESET} {lines[0]}")
            for line in lines[1:]:
                print(f"  {line}")
        self._spacing.after_message()

    def add_tool_call(self, name: str, args_preview: str = ""):
        """添加工具调用"""
        self._spacing.before_message("tool_call")
        args_str = f"{DIM}({args_preview}){RESET}" if args_preview else ""
        print(f"{CYAN}◆{RESET} {GREEN}{name}{RESET}{args_str}")
        # 工具调用后不打印空行，因为后面紧跟工具结果

    def add_tool_result(self, preview: str, max_length: int = 60):
        """添加工具结果"""
        self._spacing.before_message("tool_result")
        text = preview
        if len(text) > max_length:
            text = text[:max_length - 3] + "..."
        print(f"  {DIM}⎿ {text}{RESET}")
        self._spacing.after_message()

    def add_tool_error(self, error: str):
        """添加工具错误"""
        self._spacing.before_message("error")
        print(f"  {RED}✗ {error}{RESET}")
        self._spacing.after_message()

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

    async def get_input(self) -> str:
        """获取用户输入（带背景色显示）"""
        # 确保 spinner 已停止
        self._stop_spinner()

        # 记录用户输入类型（空行由上一次消息的 after_message 处理，或由命令执行后的 print() 处理）
        self._spacing.before_message("user_input")

        # 显示输入提示（input 会回显用户输入）
        prompt = f"{BOLD}{BLUE}>{RESET} "

        try:
            # 使用标准输入（用户输入会被回显）
            user_input = await asyncio.to_thread(input, prompt)

            # 输入完成后，用背景色覆盖原行
            if user_input.strip():
                import shutil
                width = shutil.get_terminal_size().columns

                # 构造带背景色的显示行（前面带 >，用白色确保可见）
                display_line = f"{BG_BLUE}{BOLD}{WHITE}>{RESET}{BG_BLUE}{BOLD} {user_input} {RESET}"
                # 填充到行尾
                padding = " " * max(0, width - len(user_input) - 3)

                # 光标上移一行，回到行首，清空行，打印背景色版本
                print(f"\033[1A\r\033[K{display_line}{padding}{RESET}")
            # 注意：input() 本身已经有一个换行，所以这里不加 print()

            return user_input
        except EOFError:
            return ""


# 默认使用简化版
Layout = SimpleLayout
