"""Built-in tools for MoCode.

Usage:
    from mocode.tools import ReadTool, EditTool, BashTool, FetchTool

    agent = (Agent()
        .provider(my_provider)
        .prompt("...")
        .tools([ReadTool, EditTool, BashTool(), FetchTool()])
        .build())
"""

from .file import ReadTool, WriteTool, EditTool
from .glob import GlobTool
from .grep import GrepTool
from .bash import BashTool
from .fetch import FetchTool
from .skill import SkillTool
from .subagent import SubAgentTool
from ..core import SubAgent, SubAgentConfig, SubAgentResult
from .goal import GoalTool
from .plan import PlanTool

__all__ = [
    "ReadTool",
    "WriteTool",
    "EditTool",
    "GlobTool",
    "GrepTool",
    "BashTool",
    "FetchTool",
    "SkillTool",
    "SubAgent",
    "SubAgentConfig",
    "SubAgentResult",
    "SubAgentTool",
    "GoalTool",
    "PlanTool",
]
