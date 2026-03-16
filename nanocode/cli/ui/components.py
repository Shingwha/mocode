"""UI 组件 - 模块化、可复用的界面元素"""

import re
import shutil
from typing import Protocol, Callable
from dataclasses import dataclass

from .colors import RESET, BOLD, DIM, RED, GREEN, CYAN, BLUE, YELLOW


# ==================== 协议定义 ====================

class Renderable(Protocol):
    """可渲染对象协议"""
    def render(self) -> str: ...


class Component(Protocol):
    """UI 组件协议"""
    def display(self) -> None: ...


# ==================== 布局组件 ====================

@dataclass
class Separator:
    """分隔线组件"""
    char: str = "─"
    max_width: int = 80
    color: str = DIM

    def render(self) -> str:
        width = min(shutil.get_terminal_size().columns, self.max_width)
        return f"{self.color}{self.char * width}{RESET}"

    def display(self):
        print(self.render())


@dataclass
class Header:
    """标题组件"""
    title: str
    subtitle: str = ""
    icon: str = ""

    def render(self) -> str:
        icon_str = f"{self.icon} " if self.icon else ""
        lines = [f"\n{BOLD}{icon_str}{self.title}{RESET}"]
        if self.subtitle:
            lines.append(f"{DIM}{self.subtitle}{RESET}")
        return "\n".join(lines)

    def display(self):
        print(self.render())


@dataclass
class Block:
    """内容块组件"""
    content: str
    prefix: str = ""
    color: str = ""

    def render(self) -> str:
        color_start = self.color if self.color else ""
        color_end = RESET if self.color else ""
        prefix_str = f"{self.prefix} " if self.prefix else ""
        return f"{prefix_str}{color_start}{self.content}{color_end}"

    def display(self):
        print(self.render())


# ==================== 消息组件 ====================

class MessageType:
    """消息类型定义"""
    ERROR = ("✗", RED)
    SUCCESS = ("✓", GREEN)
    INFO = ("ℹ", CYAN)
    WARNING = ("⚠", YELLOW)
    TEXT = (">", CYAN)


@dataclass
class Message:
    """消息组件 - 统一的消息显示"""
    text: str
    type: str = "text"
    icon: str = ""
    color: str = ""
    prefix: str = ""

    def __post_init__(self):
        # 根据类型自动设置图标和颜色
        if hasattr(MessageType, self.type.upper()):
            icon, color = getattr(MessageType, self.type.upper())
            if not self.icon:
                self.icon = icon
            if not self.color:
                self.color = color

    def render(self) -> str:
        icon_str = f"{self.color}{self.icon}{RESET} " if self.icon else ""
        prefix_str = f"{self.prefix} " if self.prefix else ""
        return f"{icon_str}{prefix_str}{self.color}{self.text}{RESET}"

    def display(self):
        print(self.render())


# ==================== 工具调用组件 ====================

@dataclass
class ToolCall:
    """工具调用显示组件"""
    name: str
    args_preview: str = ""
    icon: str = "▶"

    def render(self) -> str:
        args_str = f"({DIM}{self.args_preview}{RESET})" if self.args_preview else ""
        return f"\n{CYAN}{self.icon}{RESET} {GREEN}{self.name}{RESET}{args_str}"

    def display(self):
        print(self.render())


@dataclass
class ToolResult:
    """工具结果显示组件"""
    preview: str
    max_length: int = 60
    icon: str = "⎿"

    def render(self) -> str:
        # 截断长文本
        text = self.preview
        if len(text) > self.max_length:
            text = text[:self.max_length - 3] + "..."
        return f"  {DIM}{self.icon}  {text}{RESET}"

    def display(self):
        print(self.render())


@dataclass
class ToolError:
    """工具错误显示组件"""
    error: str
    icon: str = "✗"

    def render(self) -> str:
        return f"  {RED}{self.icon}  {self.error}{RESET}"

    def display(self):
        print(self.render())


# ==================== 输入组件 ====================

@dataclass
class InputPrompt:
    """输入提示组件"""
    prompt: str = ">"
    color: str = BLUE
    bold: bool = True

    def render(self) -> str:
        bold_code = BOLD if self.bold else ""
        return f"{bold_code}{self.color}{self.prompt}{RESET} "

    def ask(self) -> str:
        """显示提示并获取输入"""
        return input(self.render()).strip()


# ==================== Markdown 渲染 ====================

def render_markdown(text: str) -> str:
    """简单 Markdown 渲染（加粗、行内代码）"""
    # 加粗 **text**
    text = re.sub(r"\*\*(.+?)\*\*", f"{BOLD}\\1{RESET}", text)
    # 行内代码 `code`
    text = re.sub(r"`([^`]+)`", f"{DIM}\\1{RESET}", text)
    return text


# ==================== 工厂函数（便捷方法） ====================

def error(text: str):
    """显示错误消息"""
    Message(text, type="error").display()


def success(text: str):
    """显示成功消息"""
    Message(text, type="success").display()


def info(text: str):
    """显示信息消息"""
    Message(text, type="info").display()


def warning(text: str):
    """显示警告消息"""
    Message(text, type="warning").display()


def text(text: str):
    """显示普通文本（带 Markdown 渲染）"""
    print(render_markdown(text))


def tool_call(name: str, preview: str = ""):
    """显示工具调用"""
    args_str = f" {DIM}({preview}){RESET}" if preview else ""
    print(f"{CYAN}▶{RESET} {GREEN}{name}{RESET}{args_str}")


def tool_result(preview: str, max_length: int = 60):
    """显示工具结果"""
    # 截断长文本
    if len(preview) > max_length:
        preview = preview[:max_length - 3] + "..."
    print(f"  {DIM}⎿ {preview}{RESET}")


def separator_line(char: str = "─"):
    """显示分隔线"""
    Separator(char).display()


# ==================== UI 管理器 ====================

class UIManager:
    """UI 管理器 - 统一管理界面状态和输出"""

    def __init__(self):
        self._spinner = None
        self._pending_line = False

    def print(self, *args, **kwargs):
        """安全打印（处理未完成的行）"""
        if self._pending_line:
            print()  # 完成未完成的行
            self._pending_line = False
        print(*args, **kwargs)

    def print_inline(self, text: str):
        """行内打印（不自动换行）"""
        print(f"\r{text}", end="", flush=True)
        self._pending_line = True

    def clear_line(self):
        """清除当前行"""
        width = shutil.get_terminal_size().columns
        print(f"\r{' ' * width}\r", end="")
        self._pending_line = False

    def welcome(self, name: str, model: str, cwd: str):
        """显示欢迎界面 - 简洁单行版本"""
        print(f"{BOLD}{name}{RESET} {DIM}v0.1.0{RESET}  │  {CYAN}{model}{RESET}  │  {DIM}{cwd}{RESET}")
        print()  # 空行分隔

    def assistant_response(self, text: str):
        """显示助手响应"""
        if self._pending_line:
            print()
            self._pending_line = False
        print(f"\n{BOLD}Assistant:{RESET}")
        print(render_markdown(text))


# 全局 UI 管理器实例
ui = UIManager()