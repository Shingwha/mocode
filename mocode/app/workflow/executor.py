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

from ...core.agent import AgentLoop
from ...core.hook import HookRunner
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
        # Cache node-independent state (derived once, reused for all nodes)
        self._node_tools = parent_agent.tool_registry.derived(
            exclude={"sub_agent", "compact"}
        )
        self._provider = parent_agent.provider
        self._system_prompt = parent_agent.system_prompt
        self._config = parent_agent.config
        # Reusable AgentLoop — reset between sequential nodes
        self._template_agent = AgentLoop(
            provider=self._provider,
            system_prompt=self._system_prompt,
            tools=self._node_tools,
            hooks=HookRunner(),
            config=self._config,
        )

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
            tool_calls=sum(nr.tool_calls for nr in child_results),
            prompt_tokens=sum(nr.prompt_tokens for nr in child_results),
            completion_tokens=sum(nr.completion_tokens for nr in child_results),
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
    def _build_node_context_header(
        node: Node, workflow: Workflow, run_dir: str | None = None
    ) -> str:
        """Build context header with graph structure info for a node."""
        lines = [f'You are node "{node.id}" in workflow "{workflow.name}".']
        if node.description:
            lines.append(f"Description: {node.description}")

        if run_dir:
            lines.append(f"Run directory: {run_dir}")

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

    def _build_result(
        self,
        node_id: str,
        task: str,
        output: str,
        exit_code: int,
        t0: float,
        temp_agent: AgentLoop,
        *,
        sections: dict | None = None,
        error: str | None = None,
    ) -> NodeResult:
        """Build a NodeResult from agent state after execution."""
        usage = temp_agent.total_usage
        return NodeResult(
            node_id=node_id,
            task=task,
            output=output,
            exit_code=exit_code,
            duration=time.monotonic() - t0,
            error=error,
            sections=sections or {},
            tool_calls=temp_agent._tool_call_count,
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
        )

    async def _exec_node(
        self,
        node_id: str,
        task: str,
        context_header: str | None = None,
    ) -> NodeResult:
        """Execute a task node using an in-process AgentLoop."""
        full_prompt = f"{context_header}\n\n---\nTask: {task}" if context_header else task

        # Reset template agent for this node (new hook, clear messages/counters)
        hook = _WorkflowNodeHook(node_id, self._emit)
        self._template_agent.reset(hooks=HookRunner([hook]))

        t0 = time.monotonic()
        try:
            result = await asyncio.wait_for(
                self._template_agent.chat(full_prompt),
                timeout=self.timeout,
            )
            output = result or ""
            return self._build_result(
                node_id, task, output, 0, t0, self._template_agent,
                sections=parse_sections(output),
            )
        except asyncio.TimeoutError:
            return self._build_result(
                node_id, task, "", 1, t0, self._template_agent,
                error=f"timed out after {self.timeout}s",
            )
        except Exception as e:
            return self._build_result(
                node_id, task, "", 1, t0, self._template_agent,
                error=str(e),
            )

    async def _exec_map_child(
        self,
        child_id: str,
        task: str,
        context_header: str | None = None,
    ) -> NodeResult:
        """Execute a map child node — needs its own AgentLoop (concurrent)."""
        full_prompt = f"{context_header}\n\n---\nTask: {task}" if context_header else task

        hook = _WorkflowNodeHook(child_id, self._emit)
        agent = AgentLoop(
            provider=self._provider,
            system_prompt=self._system_prompt,
            tools=self._node_tools,
            hooks=HookRunner([hook]),
            config=self._config,
        )

        t0 = time.monotonic()
        try:
            result = await asyncio.wait_for(
                agent.chat(full_prompt),
                timeout=self.timeout,
            )
            output = result or ""
            return self._build_result(
                child_id, task, output, 0, t0, agent,
                sections=parse_sections(output),
            )
        except asyncio.TimeoutError:
            return self._build_result(
                child_id, task, "", 1, t0, agent,
                error=f"timed out after {self.timeout}s",
            )
        except Exception as e:
            return self._build_result(
                child_id, task, "", 1, t0, agent,
                error=str(e),
            )
