"""Built-in tools for MoCode.

Usage:
    from mocode.tools import ReadTool, EditTool, BashTool, FetchTool

    agent = (Agent()
        .provider(my_provider)
        .prompt("...")
        .tools([ReadTool, EditTool, BashTool(), FetchTool()])
        .build())
"""

from .file import ReadTool, WriteTool, AppendTool, EditTool
from .search import GlobTool, GrepTool
from .bash import BashTool
from .fetch import FetchTool
from .skill import SkillTool
from .compact import CompactTool
from .subagent import SubAgent, SubAgentConfig, SubAgentResult, SubAgentTool
from .image import ImageTool
from .goal import GoalTool

__all__ = [
    "ReadTool",
    "WriteTool",
    "AppendTool",
    "EditTool",
    "GlobTool",
    "GrepTool",
    "BashTool",
    "FetchTool",
    "SkillTool",
    "CompactTool",
    "SubAgent",
    "SubAgentConfig",
    "SubAgentResult",
    "SubAgentTool",
    "ImageTool",
    "GoalTool",
]
