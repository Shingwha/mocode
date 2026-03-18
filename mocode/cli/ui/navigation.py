"""层级导航组件 - 支持菜单层级跳转"""

from __future__ import annotations
from enum import Enum, auto
from dataclasses import dataclass
from typing import Callable, Any, TypeVar, ParamSpec

P = ParamSpec("P")
T = TypeVar("T")


class Action(Enum):
    """菜单操作后的行为"""
    BACK = auto()   # 返回上一层级
    STAY = auto()   # 保持当前层级（重绘）
    EXIT = auto()   # 完全退出导航


@dataclass
class MenuFrame:
    """导航栈帧"""
    fn: Callable[..., Any]
    args: tuple[Any, ...]
    kwargs: dict[str, Any]


class NavigationStack:
    """导航栈 - 每次新建实例，完全隔离
    
    使用装饰器 @navigable 自动管理生命周期
    """
    
    def __init__(self):
        self._stack: list[MenuFrame] = []
        self._result: Any = None
    
    def run(self, entry_fn: Callable[P, T], *args: P.args, **kwargs: P.kwargs) -> T | None:
        """启动导航，执行入口函数
        
        工作流程:
        1. 将入口函数压入栈
        2. 循环执行栈顶函数
        3. 根据返回值决定行为:
           - Action.BACK: 弹出当前，继续执行上一级
           - Action.STAY: 保持当前，重绘
           - Action.EXIT: 清空栈，返回结果
           - None: 默认行为，等同于 BACK
           - 其他值: 保存为结果，弹出当前，继续
        
        Returns:
            最后一个非 Action 的返回值，或 None
        """
        self._stack.append(MenuFrame(entry_fn, args, kwargs))
        
        while self._stack:
            frame = self._stack[-1]
            result = frame.fn(*frame.args, **frame.kwargs)
            
            if result is Action.BACK:
                self._stack.pop()
            elif result is Action.STAY:
                pass  # 继续循环，重绘当前
            elif result is Action.EXIT:
                self._stack.clear()
                return self._result
            elif result is None:
                self._stack.pop()  # 默认行为：返回上一级
            else:
                # 正常返回值，保存并弹出
                self._result = result
                self._stack.pop()
        
        return self._result


def navigable(f: Callable[P, T]) -> Callable[P, T | None]:
    """装饰器：让函数支持层级导航
    
    每次调用都会新建一个 NavigationStack 实例，
    确保导航状态完全隔离。
    
    Example:
        @navigable
        def my_menu(client) -> str | None:
            def handle(key: str) -> Action | str:
                if key == "back":
                    return Action.BACK
                return key
            
            menu = SelectMenu("Title", choices, on_select=handle)
            return menu.show()
    """
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> T | None:
        nav = NavigationStack()
        return nav.run(f, *args, **kwargs)
    return wrapper
