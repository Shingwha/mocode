"""NodeHandler — pluggable per-type execution strategy for workflow nodes.

Each node type (task / router / map / future: input, parallel, join, ...)
implements the :class:`NodeHandler` protocol and is registered with the
:class:`NodeHandlerRegistry`. The runner looks up the handler by
``node.type`` and delegates execution, so adding a new node type no longer
requires touching the runner, scheduler, or state.

The handler receives a :class:`NodeExecContext` bundle that carries all the
runtime dependencies (workflow, state, emit, semaphore, callbacks) without
exposing runner internals — handlers stay decoupled from the DAGRunner class.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Coroutine, Protocol, runtime_checkable

if TYPE_CHECKING:
    from typing import Callable

    from ..events import WorkflowEvent
    from ..models import Node, NodeResult, Workflow
    from ..state import RunState


@dataclass
class NodeExecContext:
    """Everything a node handler needs to execute one node.

    Passed by the runner so handlers never reach into DAGRunner internals.
    All callbacks are synchronous and safe to invoke from async code,
    except ``exec_node`` which is async.
    """

    workflow: Workflow
    state: RunState
    emit: Callable[[WorkflowEvent], None]
    semaphore: asyncio.Semaphore
    run_dir: str | None
    node_context_enabled: bool
    on_complete: Callable[["Node", NodeResult, str | None], None]
    reset_for_loop: Callable[["Node", str, int], None]
    # Single-node execution seam: handlers MUST run nodes through this rather
    # than calling their own execute_node directly, so that
    # ``patch.object(runner, "_exec_node", ...)`` in tests intercepts execution.
    exec_node: Callable[..., "Coroutine[Any, Any, NodeResult]"]


@runtime_checkable
class NodeHandler(Protocol):
    """Execution strategy for one node type.

    Attributes:
        type_name: the ``Node.type`` value this handler serves (e.g. ``"task"``).
        synchronous: if True, the runner calls ``execute`` inline (not via
            ``asyncio.gather``). Router nodes are synchronous — they only
            inspect state and activate targets, never run an AgentLoop.
    """

    type_name: str
    synchronous: bool

    async def execute(self, node: Node, ctx: NodeExecContext) -> None:
        """Run the node to completion and invoke ``ctx.on_complete`` exactly once."""
        ...


class NodeHandlerRegistry:
    """Maps node ``type`` strings to their :class:`NodeHandler`."""

    def __init__(self) -> None:
        self._handlers: dict[str, NodeHandler] = {}

    def register(self, handler: NodeHandler) -> None:
        self._handlers[handler.type_name] = handler

    def get(self, type_name: str) -> NodeHandler:
        try:
            return self._handlers[type_name]
        except KeyError:
            known = ", ".join(sorted(self._handlers)) or "<none>"
            raise KeyError(
                f"No handler registered for node type '{type_name}'. Known: {known}"
            )

    def has(self, type_name: str) -> bool:
        return type_name in self._handlers
