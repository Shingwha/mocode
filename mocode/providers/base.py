"""Provider 协议"""

from typing import Protocol, runtime_checkable


@runtime_checkable
class BaseProvider(Protocol):
    """Provider 协议"""

    def call(self, messages: list, system: str, tools: list, max_tokens: int):
        """调用 LLM API"""
        ...
