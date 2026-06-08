"""NodeExecutor — isolates core AgentLoop execution logic from DAGRunner.

Contains:
- _exec_node: AgentLoop instantiation and execution
- _build_node_context_header: context string builder
- _finalize_map / _finalize_empty_map: map result helpers

Higher-level orchestration (_run_task_node, _run_map_node, _run_map_child)
remain on DAGRunner as thin delegates so that test patches on
``patch.object(runner, "_exec_node", ...)`` continue to work.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Callable

    from .events import WorkflowEvent
    from .models import Node, NodeResult, Workflow
    from .state import RunState

    from ..core.agent import AgentLoop

from .hooks import _WorkflowNodeHook  # noqa: F401 — re-export for backward compat
from .models import NodeResult, parse_sections


class Executor:
    """Encapsulates core AgentLoop execution and map result helpers.

    Higher-level node orchestration (task/map/map_child runners) stays on
    DAGRunner so that ``patch.object(runner, "_exec_node")`` in tests
    continues to intercept calls correctly.
    """

    def __init__(
        self,
        parent_agent: AgentLoop,
        *,
        timeout: int,
        on_event: Callable[[WorkflowEvent], None] | None,
    ):
        self._parent_agent = parent_agent
        self.timeout = timeout
        self._on_event = on_event

    # ── Event dispatch (thin wrapper) ─────────────────────────

    def _emit(self, event: WorkflowEvent) -> None:
        if self._on_event:
            self._on_event(event)

    # ── Map result helpers ────────────────────────────────────

    def _finalize_empty_map(self, node: Node, state: RunState, record_done) -> None:
        """Handle a map node with no items — produce empty output."""
        empty_result = NodeResult(
            node_id=node.id,
            task=node.task,
            output="",
            exit_code=0,
            duration=0,
        )
        state.total_executions += 1
        record_done(node, empty_result, state)

    def _finalize_map(
        self,
        node: Node,
        items: list[str],
        child_results: list[NodeResult],
        state: RunState,
        record_done,
    ) -> None:
        """Merge child outputs, record result, activate downstream."""
        total_duration = sum(nr.duration for nr in child_results)
        merged_output = "\n---\n".join(nr.output for nr in child_results)

        for nr in child_results:
            state.total_executions += 1
            state.results.append(nr)

        map_result = NodeResult(
            node_id=node.id,
            task=node.task,
            output=merged_output,
            exit_code=0,
            duration=total_duration,
        )
        state.total_executions += 1
        record_done(
            node,
            map_result,
            state,
            progress_message=f"Map '{node.id}' done — {len(items)} items ({total_duration:.1f}s)",
        )

    # ── Node context header ───────────────────────────────────

    @staticmethod
    def _build_node_context_header(node: Node, workflow: Workflow) -> str:
        """Build context header with graph structure info for a node."""
        lines = [f'You are node "{node.id}" in workflow "{workflow.name}".']
        if node.description:
            lines.append(f"Description: {node.description}")

        if node.depends:
            lines.append("Input from:")
            for dep_id in node.depends:
                dep_node = workflow.node_map.get(dep_id)
                desc = dep_node.description if dep_node else ""
                lines.append(f"  - {dep_id}: {desc}" if desc else f"  - {dep_id}")

        dep_ids = workflow.dependents.get(node.id, [])
        if dep_ids:
            lines.append("Output to:")
            for dep_id in dep_ids:
                dep_node = workflow.node_map.get(dep_id)
                desc = dep_node.description if dep_node else ""
                lines.append(f"  - {dep_id}: {desc}" if desc else f"  - {dep_id}")

        return "\n".join(lines)

    # ── AgentLoop execution ──────────────────────────────────

    async def _exec_node(
        self,
        node_id: str,
        task: str,
        context_header: str | None = None,
    ) -> NodeResult:
        """Execute a task node using an in-process AgentLoop."""
        from ...core.builder import Agent

        full_prompt = f"{context_header}\n\n---\nTask: {task}" if context_header else task

        tools = self._parent_agent.tool_registry.derived(
            exclude={"sub_agent", "compact"}
        )
        hook = _WorkflowNodeHook(node_id, self._emit)

        temp_agent = (
            Agent()
            .provider(self._parent_agent.provider)
            .prompt(self._parent_agent.system_prompt)
            .tools(tools)
            .hooks([hook])
            .config(self._parent_agent.config)
            .build()
        )

        t0 = time.monotonic()
        try:
            result = await asyncio.wait_for(
                temp_agent.chat(full_prompt),
                timeout=self.timeout,
            )
            output = result or ""
            return NodeResult(
                node_id=node_id,
                task=task,
                output=output,
                exit_code=0,
                duration=time.monotonic() - t0,
                iteration=temp_agent.iteration,
                sections=parse_sections(output),
            )
        except asyncio.TimeoutError:
            return NodeResult(
                node_id=node_id,
                task=task,
                output="",
                exit_code=1,
                duration=time.monotonic() - t0,
                error=f"timed out after {self.timeout}s",
                iteration=getattr(temp_agent, 'iteration', 1),
            )
        except Exception as e:
            return NodeResult(
                node_id=node_id,
                task=task,
                output="",
                exit_code=1,
                duration=time.monotonic() - t0,
                error=str(e),
                iteration=getattr(temp_agent, 'iteration', 1),
            )
