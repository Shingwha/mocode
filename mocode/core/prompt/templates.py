"""Prompt 模板工具函数"""

from .builder import PromptBuilder, StaticSection
from .sections import (
    BEHAVIOR_SECTION,
    ENVIRONMENT_SECTION,
    IDENTITY_SECTION,
    SKILLS_SECTION,
    TOOLS_SECTION,
)


def default_prompt() -> PromptBuilder:
    """创建默认 prompt 构建器"""
    return (PromptBuilder()
        .add(IDENTITY_SECTION)
        .add(ENVIRONMENT_SECTION)
        .add(TOOLS_SECTION)
        .add(SKILLS_SECTION)
        .add(BEHAVIOR_SECTION))


def minimal_prompt() -> PromptBuilder:
    """最小 prompt (仅身份和环境)"""
    return (PromptBuilder()
        .add(IDENTITY_SECTION)
        .add(ENVIRONMENT_SECTION)
        .add(TOOLS_SECTION))


def custom_prompt(sections: list) -> PromptBuilder:
    """自定义 prompt"""
    builder = PromptBuilder()
    for section in sections:
        builder.add(section)
    return builder
