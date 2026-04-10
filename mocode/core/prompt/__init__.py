"""Prompt 构建系统"""

from .builder import (
    DynamicSection,
    PromptBuilder,
    PromptContributions,
    PromptSection,
    StaticSection,
)
from .sections import (
    ENVIRONMENT_SECTION,
    MEMORY_SECTION,
    PRIORITY_CUSTOM,
    PRIORITY_ENVIRONMENT,
    PRIORITY_MEMORY,
    PRIORITY_SKILLS,
    PRIORITY_SOUL,
    PRIORITY_TOOLS,
    PRIORITY_USER,
    SKILLS_SECTION,
    SOUL_SECTION,
    TOOLS_SECTION,
    USER_SECTION,
)
from .templates import custom_prompt, default_prompt, minimal_prompt

__all__ = [
    # Builder
    "PromptBuilder",
    "PromptContributions",
    "StaticSection",
    "DynamicSection",
    "PromptSection",
    # Sections
    "SOUL_SECTION",
    "USER_SECTION",
    "MEMORY_SECTION",
    "ENVIRONMENT_SECTION",
    "TOOLS_SECTION",
    "SKILLS_SECTION",
    # Priority constants
    "PRIORITY_SOUL",
    "PRIORITY_USER",
    "PRIORITY_MEMORY",
    "PRIORITY_ENVIRONMENT",
    "PRIORITY_TOOLS",
    "PRIORITY_SKILLS",
    "PRIORITY_CUSTOM",
    # Templates
    "default_prompt",
    "minimal_prompt",
    "custom_prompt",
]
