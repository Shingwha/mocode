"""Built-in node handlers and registry factory.

To add a new node type, implement :class:`NodeHandler` in a new module and
register it via ``registry.register(MyHandler(...))`` — no changes needed to
the runner, scheduler, or state.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .base import NodeExecContext, NodeHandler, NodeHandlerRegistry
from .map import MapNodeHandler
from .router import RouterNodeHandler
from .task import TaskNodeHandler

if TYPE_CHECKING:
    from ....core.agent import AgentLoop


def default_registry(
    parent_agent: AgentLoop, *, timeout: int
) -> NodeHandlerRegistry:
    """Build a registry populated with the three built-in node handlers.

    ``parent_agent`` supplies the provider/system_prompt/tools/config that the
    task handler reuses for its AgentLoop. The map handler delegates child
    execution through the runner's ``exec_node`` seam, and the router never
    runs an AgentLoop, so neither needs ``parent_agent``.
    """
    registry = NodeHandlerRegistry()
    registry.register(TaskNodeHandler(parent_agent, timeout=timeout))
    registry.register(MapNodeHandler(timeout=timeout))
    registry.register(RouterNodeHandler())
    return registry


__all__ = [
    "NodeExecContext",
    "NodeHandler",
    "NodeHandlerRegistry",
    "TaskNodeHandler",
    "MapNodeHandler",
    "RouterNodeHandler",
    "default_registry",
]
