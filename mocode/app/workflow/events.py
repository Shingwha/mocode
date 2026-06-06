"""Workflow event types — emitted by DAGRunner, consumed by Display.

Events carry full visual context (especially ``wave_idx``) so the consumer
can render them in the correct visual wave context without buffering.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models import NodeResult


@dataclass
class WorkflowEvent:
    """Base class for all workflow events."""


@dataclass
class WaveReadyEvent(WorkflowEvent):
    """Emitted when a wave's dependencies are met and it's announced."""

    wave_idx: int
    total_waves: int
    node_ids: list[str]


@dataclass
class RouterConditionEvent(WorkflowEvent):
    """Emitted when a router evaluates its routes."""

    router_id: str
    matched: bool
    branch: str
    targets: list[str]
    wave_idx: int  # the router's wave


@dataclass
class NodeSkippedEvent(WorkflowEvent):
    """Emitted when a node is skipped (deferred until its wave is announced)."""

    node_id: str
    reason: str
    wave_idx: int  # the node's wave (for correct display ordering)


@dataclass
class NodeStartEvent(WorkflowEvent):
    """Emitted when a task node starts executing."""

    node_id: str
    description: str


@dataclass
class NodeDoneEvent(WorkflowEvent):
    """Emitted when a task node completes."""

    node_id: str
    description: str
    result: NodeResult
    wave_idx: int


@dataclass
class LoopIterEvent(WorkflowEvent):
    """Emitted on a back-edge loop iteration."""

    node_id: str
    description: str
    iteration: int
    max_iter: int
    result: NodeResult


@dataclass
class MapFanOutEvent(WorkflowEvent):
    """Emitted when a map node expands items into child tasks."""

    map_id: str
    item_count: int
    wave_idx: int


@dataclass
class MapItemDoneEvent(WorkflowEvent):
    """Emitted when a single map child task completes."""

    map_id: str
    item_index: int
    total_count: int
    item_value: str
    duration: float


@dataclass
class ProgressEvent(WorkflowEvent):
    """Emitted for progress updates during execution."""

    message: str
    node_id: str
    detail: str


@dataclass
class NodeToolCallEvent(WorkflowEvent):
    """A single tool call completed within a node."""

    node_id: str
    tool_name: str
    tool_args: dict
    error: str | None = None
    elapsed: float = 0.0


@dataclass
class NodeToolBatchDoneEvent(WorkflowEvent):
    """All tool calls from one LLM response completed for a node."""

    node_id: str
    groups: list[tuple[str, list[str]]]  # [(tool_name, [summaries])]
    errors: dict[str, str]               # tool_name → error message
    elapsed: dict[str, float]            # tool_name → max elapsed
