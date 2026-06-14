"""TaskNodeHandler — executes a ``task`` node via a reusable in-process AgentLoop.

Absorbs the responsibilities of the former ``Executor`` class:
- caches node-independent AgentLoop state (provider, system_prompt, tools, config)
- owns the reusable ``_template_agent`` (reset between sequential nodes)
- builds per-node context headers
- runs the AgentLoop under the workflow semaphore, with timeout
- constructs ``NodeResult`` from agent state
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Callable

from ....core.agent import AgentLoop
from ....core.hook import HookRunner
from ..events import NodeStartEvent, WorkflowEvent
from ..hooks import _WorkflowNodeHook
from ..models import NodeResult, fill_template, parse_sections

if TYPE_CHECKING:
    from ..models import Node, Workflow
    from .base import NodeExecContext


class TaskNodeHandler:
    """Runs ``task`` nodes through a shared, resettable AgentLoop."""

    type_name = "task"
    synchronous = False

    def __init__(self, parent_agent: AgentLoop, *, timeout: int) -> None:
        self.timeout = timeout
        # Node-independent state derived once, reused for every task node.
        self._node_tools = parent_agent.tool_registry.derived(
            exclude={"sub_agent", "compact"}
        )
        self._provider = parent_agent.provider
        self._system_prompt = parent_agent.system_prompt
        self._config = parent_agent.config
        # Reusable AgentLoop — reset between sequential nodes.
        self._template_agent = AgentLoop(
            provider=self._provider,
            system_prompt=self._system_prompt,
            tools=self._node_tools,
            hooks=HookRunner(),
            config=self._config,
        )

    # ── NodeHandler protocol ─────────────────────────────────

    async def execute(self, node: Node, ctx: NodeExecContext) -> None:
        """Fill the task template, run the AgentLoop, and report completion."""
        task_text = fill_template(node.task, ctx.state.get_context())
        context_header = (
            self.build_context_header(node, ctx.workflow, ctx.run_dir)
            if ctx.node_context_enabled
            else None
        )

        async with ctx.semaphore:
            ctx.emit(NodeStartEvent(node_id=node.id, description=node.description))
            # Route through ctx.exec_node (the runner's _exec_node seam) so that
            # patch.object(runner, "_exec_node", ...) in tests intercepts execution.
            nr = await ctx.exec_node(node.id, task_text, context_header)

        ctx.state.increment_total()
        ctx.on_complete(
            node,
            nr,
            f"Node '{node.id}' done ({nr.duration:.1f}s)",
        )

    # ── AgentLoop execution ─────────────────────────────────

    async def execute_node(
        self,
        node_id: str,
        task: str,
        context_header: str | None = None,
        *,
        emit: Callable[[WorkflowEvent], None] | None = None,
    ) -> NodeResult:
        """Execute one task node against the reusable template AgentLoop.

        This is the canonical single-node entry point. ``DAGRunner._exec_node``
        delegates here so that ``patch.object(runner, "_exec_node", ...)``
        in tests continues to intercept node execution.

        ``emit`` is the event sink the per-node hook should report tool calls
        through; when ``None`` (e.g. during a patch in tests), tool-call events
        are dropped.
        """
        full_prompt = (
            f"{context_header}\n\n---\nTask: {task}" if context_header else task
        )
        sink: Callable[[WorkflowEvent], None] = emit or (lambda _e: None)

        # Reset template agent for this node (fresh hook, clear history/counters).
        hook = _WorkflowNodeHook(node_id, sink)
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

    # ── Result construction ──────────────────────────────────

    @staticmethod
    def _build_result(
        node_id: str,
        task: str,
        output: str,
        exit_code: int,
        t0: float,
        agent: AgentLoop,
        *,
        sections: dict | None = None,
        error: str | None = None,
    ) -> NodeResult:
        usage = agent.total_usage
        return NodeResult(
            node_id=node_id,
            task=task,
            output=output,
            exit_code=exit_code,
            duration=time.monotonic() - t0,
            error=error,
            sections=sections or {},
            tool_calls=agent._tool_call_count,
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
        )

    # ── Context header ───────────────────────────────────────

    @staticmethod
    def build_context_header(
        node: Node, workflow: Workflow, run_dir: str | None = None
    ) -> str:
        """Build a context header describing graph position for a node."""
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
