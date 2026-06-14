"""Workflow engine — DAG-driven multi-node task execution.

Node execution is pluggable via :class:`NodeHandler`: built-in types are
``task``, ``map``, and ``router``, and new types can be registered without
modifying the runner, scheduler, or state.
"""

from __future__ import annotations

from .events import (
    LoopIterEvent,
    MapFanOutEvent,
    MapItemDoneEvent,
    NodeDoneEvent,
    NodeSkippedEvent,
    NodeStartEvent,
    NodeToolBatchDoneEvent,
    NodeToolCallEvent,
    ProgressEvent,
    RouterConditionEvent,
    WaveReadyEvent,
    WorkflowEvent,
)
from .graph import compute_waves
from .handlers import (
    MapNodeHandler,
    NodeExecContext,
    NodeHandler,
    NodeHandlerRegistry,
    RouterNodeHandler,
    TaskNodeHandler,
    default_registry,
)
from .models import (
    Node,
    NodeResult,
    ParamDef,
    Route,
    Workflow,
    fill_template,
    parse_args,
    parse_items,
    parse_sections,
    resolve_list_expr,
)
from .registry import WorkflowRegistry
from .run_store import WorkflowRunStore, derive_runs_dir
from .state import RunState

# Re-export shared utilities from cli submodule for convenience
from .cli import make_registry

__all__ = [
    # Models
    "Node",
    "NodeResult",
    "ParamDef",
    "Route",
    "Workflow",
    # State
    "RunState",
    # Handlers (extensibility API)
    "NodeExecContext",
    "NodeHandler",
    "NodeHandlerRegistry",
    "TaskNodeHandler",
    "MapNodeHandler",
    "RouterNodeHandler",
    "default_registry",
    # Registry / persistence
    "WorkflowRegistry",
    "WorkflowRunStore",
    "derive_runs_dir",
    "make_registry",
    # Graph
    "compute_waves",
    # Model helpers
    "fill_template",
    "parse_args",
    "parse_items",
    "parse_sections",
    "resolve_list_expr",
    # Events
    "WorkflowEvent",
    "WaveReadyEvent",
    "RouterConditionEvent",
    "NodeSkippedEvent",
    "NodeStartEvent",
    "NodeDoneEvent",
    "NodeToolCallEvent",
    "NodeToolBatchDoneEvent",
    "LoopIterEvent",
    "MapFanOutEvent",
    "MapItemDoneEvent",
    "ProgressEvent",
]
