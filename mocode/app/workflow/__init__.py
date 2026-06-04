"""Workflow engine — DAG-driven multi-node task execution."""

from __future__ import annotations

from .events import (
    LoopIterEvent,
    NodeDoneEvent,
    NodeSkippedEvent,
    NodeStartEvent,
    ProgressEvent,
    RouterConditionEvent,
    WaveReadyEvent,
    WorkflowEvent,
)
from .graph import compute_waves
from .models import Node, NodeResult, Route, Workflow, fill_template, summarize, detailed_summarize
from .registry import WorkflowRegistry
from .run_store import WorkflowRunStore

# Re-export shared utilities from cli submodule for convenience
from .cli import make_registry, parse_kv_args

__all__ = [
    "Node",
    "NodeResult",
    "Route",
    "Workflow",
    "WorkflowRegistry",
    "WorkflowRunStore",
    "compute_waves",
    "fill_template",
    "summarize",
    "detailed_summarize",
    "make_registry",
    "parse_kv_args",
    "WorkflowEvent",
    "WaveReadyEvent",
    "RouterConditionEvent",
    "NodeSkippedEvent",
    "NodeStartEvent",
    "NodeDoneEvent",
    "LoopIterEvent",
    "ProgressEvent",
]
