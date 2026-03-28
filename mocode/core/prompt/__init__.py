"""Prompt 构建系统"""

from .builder import (
    DynamicSection,
    PromptBuilder,
    PromptContributions,
    PromptSection,
    StaticSection,
)
from .sections import (
    BEHAVIOR_SECTION,
    ENVIRONMENT_SECTION,
    IDENTITY_SECTION,
    PRIORITY_BEHAVIOR,
    PRIORITY_CUSTOM,
    PRIORITY_ENVIRONMENT,
    PRIORITY_IDENTITY,
    PRIORITY_SKILLS,
    PRIORITY_TOOLS,
    SKILLS_SECTION,
    TOOLS_SECTION,
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
    "IDENTITY_SECTION",
    "ENVIRONMENT_SECTION",
    "TOOLS_SECTION",
    "SKILLS_SECTION",
    "BEHAVIOR_SECTION",
    # Priority constants
    "PRIORITY_IDENTITY",
    "PRIORITY_ENVIRONMENT",
    "PRIORITY_TOOLS",
    "PRIORITY_SKILLS",
    "PRIORITY_BEHAVIOR",
    "PRIORITY_CUSTOM",
    # Templates
    "default_prompt",
    "minimal_prompt",
    "custom_prompt",
]
