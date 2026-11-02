"""
Manager base protocol and shared trace data structures.

Defines the contract every manager implementation must satisfy.
The SimulationEngine depends ONLY on this interface — it never imports
ConventionalManager or POSDCORBManager directly.

Hierarchy (conceptual):
  BaseManager (Protocol)
    ├── ConventionalManager   (centralized, fixed pool)
    └── POSDCORBManager       (POSDCORB adaptive, dynamic staffing)

Structured execution trace:
  ExecutionTrace
    └── list[DecisionRecord]
          Each record captures one manager decision cycle with:
          - manager_decision  (action the manager took)
          - selected_agent    (agent ID chosen)
          - task_id           (task being acted on)
          - timestamp         (ISO-8601 wall clock)
          - decision_latency_ms
          - reason            (human-readable rationale)
          - result            (outcome after worker execution)
          - success           (boolean)
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol, runtime_checkable

from app.models.agent import Agent
from app.models.workload import Task, TaskGraph


# ---------------------------------------------------------------------------
# Decision / Trace data classes  (plain dataclasses for speed; not Pydantic)
# ---------------------------------------------------------------------------


@dataclass
class DecisionRecord:
    """
    One complete manager decision cycle.

    Created by the manager for every assign / retry / skip / fail action.
    The simulation engine collects these into an ExecutionTrace.
    """

    # What the manager decided to do
    manager_decision: str      # "assign" | "retry" | "skip" | "fail" | "wait"

    # Who was involved
    task_id: str
    selected_agent_id: str | None   # None if no agent was chosen (wait/skip)
    selected_agent_name: str | None

    # When
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    decision_latency_ms: float = 0.0   # simulated manager think-time

    # Why
    reason: str = ""

    # What happened after the decision
    result: str | None = None          # "success" | "failure" | "pending" | None
    success: bool | None = None        # True/False/None (None = not yet resolved)

    # Extra manager-specific metadata (JSON-serialisable dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "manager_decision": self.manager_decision,
            "task_id": self.task_id,
            "selected_agent_id": self.selected_agent_id,
            "selected_agent_name": self.selected_agent_name,
            "timestamp": self.timestamp,
            "decision_latency_ms": self.decision_latency_ms,
            "reason": self.reason,
            "result": self.result,
            "success": self.success,
            "metadata": self.metadata,
        }


@dataclass
class ExecutionTrace:
    """
    Complete structured audit trail for one simulation run.

    One ExecutionTrace is produced per (manager, graph) pair.
    """

    manager_type: str           # "conventional" | "posdcorb"
    graph_id: str
    seed: int

    decisions: list[DecisionRecord] = field(default_factory=list)

    # Populated by the engine at the end of the run
    total_tasks: int = 0
    completed_tasks: int = 0
    failed_tasks: int = 0
    total_wall_ms: float = 0.0   # simulated total elapsed ms
    total_decisions: int = 0
    # Number of *scheduling decisions* the manager actually consumed from its
    # decision budget.  This is the quantity both managers cap internally
    # (one per ready-task decision), and is deliberately distinct from
    # ``total_decisions`` which counts every trace record, including zero-cost
    # observation events.  ``None`` means the manager did not report it.
    budget_decisions_used: int | None = None

    @property
    def success_rate(self) -> float:
        if self.total_tasks == 0:
            return 0.0
        return self.completed_tasks / self.total_tasks

    @property
    def avg_decision_latency_ms(self) -> float:
        """
        Mean think-time of decisions that actually consumed simulated time.

        Zero-cost observation events are excluded so that managers which log
        more observability events are not rewarded with a lower average.
        """
        cost_decisions = [
            d for d in self.decisions if float(d.decision_latency_ms or 0.0) > 0.0
        ]
        if not cost_decisions:
            return 0.0
        return (
            sum(float(d.decision_latency_ms) for d in cost_decisions)
            / len(cost_decisions)
        )

    def record(self, decision: DecisionRecord) -> None:
        self.decisions.append(decision)
        self.total_decisions += 1

    def as_dict(self) -> dict[str, Any]:
        return {
            "manager_type": self.manager_type,
            "graph_id": self.graph_id,
            "seed": self.seed,
            "total_tasks": self.total_tasks,
            "completed_tasks": self.completed_tasks,
            "failed_tasks": self.failed_tasks,
            "total_wall_ms": self.total_wall_ms,
            "total_decisions": self.total_decisions,
            "budget_decisions_used": self.budget_decisions_used,
            "success_rate": self.success_rate,
            "avg_decision_latency_ms": self.avg_decision_latency_ms,
            "decisions": [d.as_dict() for d in self.decisions],
        }


# ---------------------------------------------------------------------------
# Manager protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class ManagerProtocol(Protocol):
    """
    Structural protocol (PEP 544) that every manager must satisfy.

    The SimulationEngine depends only on this interface — it calls
    `run(graph, agents)` and receives an ExecutionTrace back.
    """

    @property
    def manager_type(self) -> str:
        """Stable identifier: 'conventional' or 'posdcorb'."""
        ...

    def run(
        self,
        graph: TaskGraph,
        agents: list[Agent],
    ) -> ExecutionTrace:
        """
        Execute the full workload graph with the given agent pool.

        Parameters
        ----------
        graph:
            The TaskGraph to execute.  The manager must NOT mutate this object;
            it should work on internal copies of task state.
        agents:
            Available worker agents.  The Conventional manager uses only
            origin='initial' agents; POSDCORB may also activate origin='dynamic'
            ones via its Staffing function (not implemented yet).

        Returns
        -------
        ExecutionTrace
            A complete, structured audit trail of every decision made.
        """
        ...


# ---------------------------------------------------------------------------
# Abstract base class (optional — managers can use Protocol alone)
# ---------------------------------------------------------------------------


class BaseManager(ABC):
    """
    Optional abstract base class providing shared bookkeeping helpers.

    Managers that want to inherit rather than duck-type can subclass this.
    The SimulationEngine accepts anything that satisfies ManagerProtocol.
    """

    @property
    @abstractmethod
    def manager_type(self) -> str: ...

    @abstractmethod
    def run(self, graph: TaskGraph, agents: list[Agent]) -> ExecutionTrace: ...

    # ------------------------------------------------------------------
    # Shared helpers available to all subclasses
    # ------------------------------------------------------------------

    @staticmethod
    def _eligible_agents(
        agents: list[Agent],
        required_capability: str,
        origin_filter: str | None = "initial",
    ) -> list[Agent]:
        """
        Return agents that possess the required capability and (optionally)
        match the origin filter.  Agents whose current_workload has reached
        their max_concurrent_tasks are excluded.
        """
        result = []
        for a in agents:
            if origin_filter and a.origin != origin_filter:
                continue
            if a.current_workload >= a.max_concurrent_tasks:
                continue
            if required_capability in a.capabilities:
                result.append(a)
        return result

    @staticmethod
    def _best_agent(candidates: list[Agent]) -> Agent | None:
        """
        Select the best agent from the candidate list using a simple
        scoring heuristic:
            score = reliability × processing_speed / (workload_ratio + 0.1)
        Returns None if the list is empty.
        """
        if not candidates:
            return None

        def score(a: Agent) -> float:
            workload_ratio = (
                a.current_workload / a.max_concurrent_tasks
                if a.max_concurrent_tasks > 0
                else 1.0
            )
            return a.reliability * a.processing_speed / (workload_ratio + 0.1)

        return max(candidates, key=score)
