"""Prompt 构建器核心逻辑"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Protocol, runtime_checkable


@runtime_checkable
class PromptSection(Protocol):
    """Prompt 片段协议"""

    name: str
    priority: int
    enabled: bool

    def render(self, context: dict[str, Any]) -> str: ...

    def should_render(self, context: dict[str, Any]) -> bool: ...


@dataclass(slots=True)
class StaticSection:
    """静态 prompt 片段"""

    name: str
    priority: int
    content: str
    enabled: bool = True
    condition: Callable[[dict[str, Any]], bool] | None = None

    def should_render(self, context: dict[str, Any]) -> bool:
        if not self.enabled:
            return False
        if self.condition and not self.condition(context):
            return False
        return True

    def render(self, context: dict[str, Any]) -> str:
        return self.content if self.should_render(context) else ""


@dataclass(slots=True)
class DynamicSection:
    """动态 prompt 片段 (带缓存)"""

    name: str
    priority: int
    renderer: Callable[[dict[str, Any]], str]
    enabled: bool = True
    condition: Callable[[dict[str, Any]], bool] | None = None
    _cache: str | None = field(default=None, init=False, repr=False)
    _cache_key: tuple | None = field(default=None, init=False, repr=False)

    def should_render(self, context: dict[str, Any]) -> bool:
        if not self.enabled:
            return False
        if self.condition and not self.condition(context):
            return False
        return True

    def render(self, context: dict[str, Any]) -> str:
        if not self.should_render(context):
            return ""

        # 缓存检查
        cache_key = tuple(sorted(context.items(), key=lambda x: x[0]))
        if self._cache_key == cache_key and self._cache is not None:
            return self._cache

        result = self.renderer(context)
        self._cache = result
        self._cache_key = cache_key
        return result

    def clear_cache(self) -> None:
        """清除缓存"""
        self._cache = None
        self._cache_key = None


class PromptBuilder:
    """Prompt 构建器"""

    def __init__(self):
        self._sections: list[PromptSection] = []
        self._context: dict[str, Any] = {}
        self._pre_build_hooks: list[Callable[[list[PromptSection]], None]] = []
        self._post_build_hooks: list[Callable[[str], str]] = []

    def add(self, section: PromptSection) -> PromptBuilder:
        """添加片段 (链式调用)"""
        self._sections.append(section)
        return self

    def remove(self, name: str) -> PromptBuilder:
        """移除片段"""
        self._sections = [s for s in self._sections if s.name != name]
        return self

    def context(self, **kwargs: Any) -> PromptBuilder:
        """设置上下文变量"""
        self._context.update(kwargs)
        return self

    def clear_context(self) -> PromptBuilder:
        """清除上下文"""
        self._context.clear()
        return self

    def on_pre_build(self, hook: Callable[[list[PromptSection]], None]) -> PromptBuilder:
        """添加构建前钩子"""
        self._pre_build_hooks.append(hook)
        return self

    def on_post_build(self, hook: Callable[[str], str]) -> PromptBuilder:
        """添加构建后钩子"""
        self._post_build_hooks.append(hook)
        return self

    def build(self) -> str:
        """构建最终 prompt"""
        # 执行 pre-build 钩子
        for hook in self._pre_build_hooks:
            hook(self._sections)

        # 稳定排序: priority + name
        sorted_sections = sorted(
            self._sections,
            key=lambda s: (s.priority, s.name)
        )

        parts = [s.render(self._context) for s in sorted_sections]
        result = "\n\n".join(p for p in parts if p)

        # 执行 post-build 钩子
        for hook in self._post_build_hooks:
            result = hook(result)

        return result

    def enable(self, name: str) -> PromptBuilder:
        """启用指定片段"""
        for s in self._sections:
            if isinstance(s, PromptSection) and s.name == name:
                s.enabled = True
        return self

    def disable(self, name: str) -> PromptBuilder:
        """禁用指定片段"""
        for s in self._sections:
            if isinstance(s, PromptSection) and s.name == name:
                s.enabled = False
        return self

    def get_section(self, name: str) -> PromptSection | None:
        """获取指定片段"""
        for s in self._sections:
            if isinstance(s, PromptSection) and s.name == name:
                return s
        return None

    def clear_caches(self) -> PromptBuilder:
        """清除所有动态片段的缓存"""
        for s in self._sections:
            if isinstance(s, DynamicSection):
                s.clear_cache()
        return self

    @classmethod
    def from_string(cls, content: str, name: str = "custom") -> PromptBuilder:
        """从字符串创建"""
        return cls().add(StaticSection(name, 100, content))

    def merge(self, other: PromptBuilder) -> PromptBuilder:
        """合并另一个 builder"""
        for section in other._sections:
            self.add(section)
        return self
