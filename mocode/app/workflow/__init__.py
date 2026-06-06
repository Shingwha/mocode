"""Workflow engine — DAG-driven multi-node task execution."""

from __future__ import annotations

from .events import (
    LoopIterEvent,
    MapFanOutEvent,
    MapItemDoneEvent,
    NodeDoneEvent,
    NodeSkippedEvent,
    NodeStartEvent,
    ProgressEvent,
    RouterConditionEvent,
    WaveReadyEvent,
    WorkflowEvent,
)
from .graph import compute_waves
from .models import (
    Node,
    NodeResult,
    ParamDef,
    Route,
    Workflow,
    fill_template,
    parse_args,
    parse_items,
)
from .registry import WorkflowRegistry
from .run_store import WorkflowRunStore

# Re-export shared utilities from cli submodule for convenience
from .cli import make_registry

# Summary helpers live in the renderer (display layer)
from ..cli.workflow_renderer import detailed_summarize, summarize

__all__ = [
    "Node",
    "NodeResult",
    "ParamDef",
    "Route",
    "Workflow",
    "WorkflowRegistry",
    "WorkflowRunStore",
    "compute_waves",
    "fill_template",
    "parse_args",
    "parse_items",
    "summarize",
    "detailed_summarize",
    "make_registry",
    "WorkflowEvent",
    "WaveReadyEvent",
    "RouterConditionEvent",
    "NodeSkippedEvent",
    "NodeStartEvent",
    "NodeDoneEvent",
    "LoopIterEvent",
    "MapFanOutEvent",
    "MapItemDoneEvent",
    "ProgressEvent",
]
