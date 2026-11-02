"""
Conventional Supervisor Manager — baseline centralized MAS coordinator.

Design intent
-------------
This manager represents a typical centralized LLM-based MAS coordinator
operating without any POSDCORB organizational principles.

It follows a simple inspect-assign-evaluate loop:

    1. Receive global task graph
    2. Inspect available workers (fixed initial pool only)
    3. Identify tasks whose dependencies are all satisfied (status='ready')
    4. Select the best capable agent for each ready task
    5. Delegate the task to the worker (via the rule-based executor)
    6. Receive the worker result
    7. Evaluate the result (success → mark completed; failure → retry)
    8. Retry / reassign failed tasks up to MAX_RETRIES times
    9. Continue until all tasks are completed or exhausted

Constraints (mirrors what a Conventional Manager CANNOT do)
-----------------------------------------------------------
- The worker pool is FIXED (only origin='initial' agents).
- Cannot create new organizational levels.
- Cannot dynamically expand the pool.
- Cannot create new agent roles.
- Cannot change the organizational structure.
- Cannot activate origin='dynamic' agents.

Decision latency model
-----------------------
Each manager decision cycle has a simulated latency drawn from a seeded
normal distribution (mean=50 ms, std=10 ms). This represents the overhead
of an LLM-based coordinator making a routing decision.
"""
from __future__ import annotations

import copy
import random
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from app.agents.worker import execute_task
from app.managers.base import BaseManager, DecisionRecord, ExecutionTrace
from app.models.agent import Agent
from app.models.workload import Task, TaskGraph

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_RETRIES: int = 3
"""Maximum times a task will be retried before being marked permanently failed."""

DECISION_LATENCY_MEAN_MS: float = 50.0
"""Simulated mean manager think-time per decision (ms)."""

DECISION_LATENCY_STD_MS: float = 10.0
"""Standard deviation of manager think-time (ms)."""

PRIORITY_ORDER: dict[str, int] = {
    "critical": 0,
    "high":     1,
    "medium":   2,
    "low":      3,
}
"""Lower = higher priority → tasks processed in this order."""


# ---------------------------------------------------------------------------
# Internal task state (mutable copy, not mutating the original TaskGraph)
# ---------------------------------------------------------------------------


class _TaskState:
    """Lightweight mutable wrapper around a Task used during execution."""

    __slots__ = (
        "task", "status", "assigned_agent_id", "retries",
        "start_ms", "completion_ms",
    )

    def __init__(self, task: Task) -> None:
        self.task: Task = task
        self.status: str = task.status          # starts as "ready" or "blocked"
        self.assigned_agent_id: str | None = None
        self.retries: int = 0
        self.start_ms: float = 0.0
        self.completion_ms: float = 0.0


# ---------------------------------------------------------------------------
# ConventionalManager
# ---------------------------------------------------------------------------


class ConventionalManager(BaseManager):
    """
    Centralized, fixed-pool Conventional Supervisor Manager.

    This is the BASELINE manager for comparison experiments.
    """

    manager_type: str = "conventional"   # type: ignore[override]

    def __init__(
        self,
        rng_seed: int = 42,
        max_wall_ms: float | None = None,
        max_manager_decisions: int | None = None,
    ) -> None:
        self._rng_seed = rng_seed
        self._rng = random.Random(rng_seed)
        # Engine-supplied simulated wall-clock cap (None preserves legacy default).
        self._max_wall_ms: float = (
            float(max_wall_ms) if max_wall_ms is not None else 300_000.0
        )
        # Shared manager-decision budget.  None preserves legacy behaviour
        # (no explicit cap).  The engine forwards the same value to every
        # manager so a budget-constrained experiment is fair.
        self._max_manager_decisions: int | None = (
            int(max_manager_decisions)
            if max_manager_decisions is not None
            else None
        )

    # ------------------------------------------------------------------
    # Public API (satisfies ManagerProtocol)
    # ------------------------------------------------------------------

    def run(
        self,
        graph: TaskGraph,
        agents: list[Agent],
    ) -> ExecutionTrace:
        """
        Execute the full task graph using the fixed initial-pool agents.

        Parameters
        ----------
        graph:
            The TaskGraph produced by the workload generator.  Not mutated.
        agents:
            All agents in the pool.  Only origin='initial' agents are used.
        """
        trace = ExecutionTrace(
            manager_type=self.manager_type,
            graph_id=graph.graph_id,
            seed=graph.seed,
        )

        # Work on internal copies of tasks and agents
        task_states: dict[str, _TaskState] = {
            t.id: _TaskState(t) for t in graph.tasks
        }
        # Deep-copy agents so we can mutate workload counts without side-effects
        pool: list[Agent] = [a.model_copy(deep=True) for a in agents if a.origin == "initial"]

        if not pool:
            # No initial agents → everything fails immediately
            for ts in task_states.values():
                ts.status = "failed"
            trace.budget_decisions_used = 0
            self._finalise_trace(trace, task_states, elapsed_ms=0.0)
            return trace

        # Initialize dependency tracking
        remaining_deps: dict[str, set[str]] = {
            tid: set(ts.task.dependency_ids)
            for tid, ts in task_states.items()
        }
        # Mark initial ready tasks (no deps)
        for tid, deps in remaining_deps.items():
            if not deps:
                task_states[tid].status = "ready"

        wall_ms: float = 0.0
        max_wall_ms: float = self._max_wall_ms
        decision_cycles: int = 0
        budget_exhausted: bool = False

        # Main scheduling loop
        while not self._all_terminal(task_states):
            if wall_ms > max_wall_ms:
                # Safety valve: abort after simulated 5 minutes
                break

            ready = self._get_ready_tasks(task_states)

            if not ready:
                # Nothing runnable — advance simulated clock by 10 ms
                wall_ms += 10.0
                continue

            dispatched_any = False

            for task_state in ready:
                if (
                    self._max_manager_decisions is not None
                    and decision_cycles >= self._max_manager_decisions
                ):
                    budget_exhausted = True
                    break
                decision_cycles += 1
                task = task_state.task
                eligible = self._eligible_agents(
                    pool,
                    required_capability=task.required_capability,
                    origin_filter="initial",
                )
                best = self._best_agent(eligible)

                decision_latency = self._sample_latency()
                wall_ms += decision_latency

                if best is None:
                    # No capable / available agent → wait
                    rec = DecisionRecord(
                        manager_decision="wait",
                        task_id=task.id,
                        selected_agent_id=None,
                        selected_agent_name=None,
                        decision_latency_ms=decision_latency,
                        reason=(
                            f"No agent with capability '{task.required_capability}' "
                            f"is currently available (all at capacity or pool lacks skill)."
                        ),
                        result="pending",
                        success=None,
                    )
                    trace.record(rec)
                    continue

                # Assign
                task_state.status = "running"
                task_state.assigned_agent_id = best.id
                task_state.start_ms = wall_ms
                best.current_workload += 1
                if best.current_workload >= best.max_concurrent_tasks:
                    best.status = "running"

                assign_rec = DecisionRecord(
                    manager_decision="assign",
                    task_id=task.id,
                    selected_agent_id=best.id,
                    selected_agent_name=best.name,
                    decision_latency_ms=decision_latency,
                    reason=(
                        f"Selected '{best.name}' (reliability={best.reliability:.2f}, "
                        f"speed={best.processing_speed:.2f}×) for {task.task_type} task "
                        f"(priority={task.priority}, complexity={task.complexity})."
                    ),
                    metadata={
                        "retry_attempt": task_state.retries,
                        "candidates_evaluated": len(eligible),
                    },
                )
                trace.record(assign_rec)

                # Execute (deterministic worker — no LLM)
                worker_result = execute_task(best, task)
                wall_ms += worker_result.elapsed_ms
                task_state.completion_ms = wall_ms
                best.current_workload -= 1
                if best.current_workload == 0:
                    best.status = "idle"

                if worker_result.success:
                    task_state.status = "completed"
                    best.tasks_completed += 1

                    # Unblock dependents
                    for dep_tid, dep_set in remaining_deps.items():
                        dep_set.discard(task.id)
                        if not dep_set and task_states[dep_tid].status == "blocked":
                            task_states[dep_tid].status = "ready"

                    result_rec = DecisionRecord(
                        manager_decision="evaluate",
                        task_id=task.id,
                        selected_agent_id=best.id,
                        selected_agent_name=best.name,
                        decision_latency_ms=self._sample_latency(),
                        reason=f"Task completed successfully: {worker_result.output_summary}",
                        result="success",
                        success=True,
                        metadata={
                            "elapsed_ms": worker_result.elapsed_ms,
                            "output_summary": worker_result.output_summary,
                        },
                    )
                    trace.record(result_rec)
                    dispatched_any = True

                else:
                    # Failure handling
                    best.tasks_failed += 1
                    task_state.retries += 1

                    if task_state.retries <= MAX_RETRIES:
                        # Retry: reset to ready so it is picked up again
                        task_state.status = "ready"
                        retry_rec = DecisionRecord(
                            manager_decision="retry",
                            task_id=task.id,
                            selected_agent_id=best.id,
                            selected_agent_name=best.name,
                            decision_latency_ms=self._sample_latency(),
                            reason=(
                                f"Task failed (attempt {task_state.retries}/{MAX_RETRIES}): "
                                f"{worker_result.output_summary}. Queuing retry."
                            ),
                            result="failure",
                            success=False,
                            metadata={
                                "retry_attempt": task_state.retries,
                                "elapsed_ms": worker_result.elapsed_ms,
                            },
                        )
                        trace.record(retry_rec)
                    else:
                        # Exhausted retries → permanently failed
                        task_state.status = "failed"
                        fail_rec = DecisionRecord(
                            manager_decision="fail",
                            task_id=task.id,
                            selected_agent_id=best.id,
                            selected_agent_name=best.name,
                            decision_latency_ms=self._sample_latency(),
                            reason=(
                                f"Task permanently failed after {MAX_RETRIES} retries. "
                                f"Last error: {worker_result.output_summary}"
                            ),
                            result="failure",
                            success=False,
                            metadata={"max_retries_reached": True},
                        )
                        trace.record(fail_rec)

                        # Unblock dependents (they'll be skippable)
                        for dep_tid, dep_set in remaining_deps.items():
                            dep_set.discard(task.id)
                            if (
                                not dep_set
                                and task_states[dep_tid].status == "blocked"
                            ):
                                # Dependency failed → mark dependent as failed too
                                task_states[dep_tid].status = "failed"

                    dispatched_any = True

            if budget_exhausted:
                break

            if not dispatched_any:
                # All ready tasks waiting for agents → tick clock
                wall_ms += 10.0

        self._finalise_trace(trace, task_states, elapsed_ms=wall_ms)
        trace.budget_decisions_used = decision_cycles
        return trace

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _sample_latency(self) -> float:
        """Draw simulated manager decision latency (ms)."""
        latency = self._rng.gauss(DECISION_LATENCY_MEAN_MS, DECISION_LATENCY_STD_MS)
        return max(1.0, round(latency, 2))

    @staticmethod
    def _all_terminal(task_states: dict[str, _TaskState]) -> bool:
        """Return True when every task is in a terminal state."""
        return all(
            ts.status in ("completed", "failed", "skipped")
            for ts in task_states.values()
        )

    @staticmethod
    def _get_ready_tasks(task_states: dict[str, _TaskState]) -> list[_TaskState]:
        """
        Return tasks that are ready to execute, sorted by priority then index.
        """
        ready = [ts for ts in task_states.values() if ts.status == "ready"]
        return sorted(
            ready,
            key=lambda ts: (PRIORITY_ORDER.get(ts.task.priority, 99), ts.task.index),
        )

    @staticmethod
    def _finalise_trace(
        trace: ExecutionTrace,
        task_states: dict[str, _TaskState],
        elapsed_ms: float,
    ) -> None:
        completed = sum(1 for ts in task_states.values() if ts.status == "completed")
        failed = sum(
            1 for ts in task_states.values() if ts.status in ("failed", "skipped")
        )
        trace.total_tasks = len(task_states)
        trace.completed_tasks = completed
        trace.failed_tasks = failed
        trace.total_wall_ms = round(elapsed_ms, 2)
