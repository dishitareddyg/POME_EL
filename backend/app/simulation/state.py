"""
Manager ↔ Engine state contract (Module 7).

The existing managers (Modules 5–6) execute tasks internally and return an
``ExecutionTrace``.  That interface predates this module and must be preserved.

This module therefore defines the *public state vocabulary* used by the engine
and by later modules (metrics / experiments / dashboard):

  SimulationState     — a read-only snapshot of one manager run
  TaskStateSnapshot   — per-task status and timing
  AgentStateSnapshot  — per-agent status and counters
  ResourceUsage       — budget consumption vs limits
  ManagerDecision     — the generic action shape a future step-wise manager
                        would return (ASSIGN_TASK / ACTIVATE_AGENT / REPLAN …)
  CANONICAL_EVENTS    — maps manager-specific decision strings to the
                        engine's canonical event taxonomy

The engine builds these structures from an ``ExecutionTrace``; it never
mutates manager internals.  Managers are not required to consume them, so
Module 5/6 implementations remain untouched.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from app.managers.base import ExecutionTrace
from app.models.agent import Agent
from app.models.workload import TaskGraph

# ---------------------------------------------------------------------------
# Canonical event taxonomy
# ---------------------------------------------------------------------------

# Manager-specific decision strings → canonical event names.
# Both managers use assign/retry/fail/wait/evaluate; POSDCORB adds finer
# grained "posdcorb:*" records that already match the canonical vocabulary.
CANONICAL_EVENTS: dict[str, str] = {
    # Generic manager decisions (both managers)
    "assign": "task_scheduled",
    "retry": "task_reassigned",
    "fail": "task_failed",
    "wait": "dependency_waiting",
    "evaluate": "task_completed",
    # POSDCORB explicit events
    "posdcorb:PLAN_CREATED": "plan_created",
    "posdcorb:PLAN_UPDATED": "plan_updated",
    "posdcorb:TASK_GROUP_CREATED": "task_group_created",
    "posdcorb:TASK_REORGANIZED": "task_reorganized",
    "posdcorb:AGENT_ACTIVATED": "agent_activated",
    "posdcorb:AGENT_REASSIGNED": "agent_reassigned",
    "posdcorb:TASK_DIRECTED": "task_directed",
    "posdcorb:TASK_SCHEDULED": "task_scheduled",
    "posdcorb:TASK_STARTED": "task_started",
    "posdcorb:TASK_COMPLETED": "task_completed",
    "posdcorb:TASK_REASSIGNED": "task_reassigned",
    "posdcorb:DEPENDENCY_WAIT": "dependency_waiting",
    "posdcorb:STATUS_UPDATED": "status_updated",
    "posdcorb:RESOURCE_CHECK": "resource_constraint",
    "posdcorb:BUDGET_WARNING": "budget_warning",
    "posdcorb:AGENT_FAILED": "agent_failure",
    "posdcorb:TASK_FAILED": "task_failed",
    "posdcorb:RECOVERY_STARTED": "recovery_started",
    "posdcorb:RECOVERY_COMPLETED": "recovery_completed",
    # Engine envelope events
    "engine:EXPERIMENT_STARTED": "experiment_started",
    "engine:EXPERIMENT_COMPLETED": "experiment_completed",
    "engine:RESOURCE_LIMIT": "resource_constraint",
}

# Agent statuses used by the engine state layer.
EngineAgentState = Literal["active", "idle", "busy", "failed", "inactive"]


def canonical_event(manager_decision: str) -> str:
    """Map a decision string to its canonical event name (fallback: as-is)."""
    return CANONICAL_EVENTS.get(manager_decision, manager_decision)


# ---------------------------------------------------------------------------
# Snapshots
# ---------------------------------------------------------------------------


@dataclass
class TaskStateSnapshot:
    """Per-task state derived from the execution trace."""

    task_id: str
    status: str                       # pending|ready|running|completed|failed|blocked
    priority: str = "medium"
    required_capability: str = ""
    assigned_agent_id: str | None = None
    attempts: int = 0
    waiting_ms: float = 0.0
    execution_ms: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "status": self.status,
            "priority": self.priority,
            "required_capability": self.required_capability,
            "assigned_agent_id": self.assigned_agent_id,
            "attempts": self.attempts,
            "waiting_ms": round(self.waiting_ms, 2),
            "execution_ms": round(self.execution_ms, 2),
        }


@dataclass
class AgentStateSnapshot:
    """Per-agent state derived from the execution trace."""

    agent_id: str
    name: str
    origin: str = "initial"
    state: EngineAgentState = "idle"
    current_workload: int = 0
    tasks_completed: int = 0
    tasks_failed: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "name": self.name,
            "origin": self.origin,
            "state": self.state,
            "current_workload": self.current_workload,
            "tasks_completed": self.tasks_completed,
            "tasks_failed": self.tasks_failed,
        }


@dataclass
class ResourceUsage:
    """Budget consumption versus configured limits."""

    decisions_used: int = 0
    decisions_limit: int = 0
    wall_ms_used: float = 0.0
    wall_ms_limit: float = 0.0
    llm_calls_used: int = 0
    llm_calls_limit: int = 0
    tokens_used: int = 0
    tokens_limit: int = 0
    agents_in_pool: int = 0
    max_agents: int | None = None
    failure_probability: float | None = None

    @property
    def wall_fraction_used(self) -> float:
        if self.wall_ms_limit <= 0:
            return 0.0
        return min(1.0, self.wall_ms_used / self.wall_ms_limit)

    @property
    def decision_fraction_used(self) -> float:
        if self.decisions_limit <= 0:
            return 0.0
        return min(1.0, self.decisions_used / self.decisions_limit)

    def as_dict(self) -> dict[str, Any]:
        return {
            "decisions_used": self.decisions_used,
            "decisions_limit": self.decisions_limit,
            "wall_ms_used": round(self.wall_ms_used, 2),
            "wall_ms_limit": self.wall_ms_limit,
            "llm_calls_used": self.llm_calls_used,
            "llm_calls_limit": self.llm_calls_limit,
            "tokens_used": self.tokens_used,
            "tokens_limit": self.tokens_limit,
            "agents_in_pool": self.agents_in_pool,
            "max_agents": self.max_agents,
            "failure_probability": self.failure_probability,
            "wall_fraction_used": round(self.wall_fraction_used, 4),
            "decision_fraction_used": round(self.decision_fraction_used, 4),
        }


@dataclass
class SimulationState:
    """
    Complete snapshot of one manager run.

    This is the object exposed to dashboards and to Module 8.  It is derived
    from the ExecutionTrace, never from manager internals.
    """

    manager_type: str
    elapsed_ms: float
    tasks: dict[str, TaskStateSnapshot] = field(default_factory=dict)
    agents: dict[str, AgentStateSnapshot] = field(default_factory=dict)
    resources: ResourceUsage = field(default_factory=ResourceUsage)
    recent_events: list[dict[str, Any]] = field(default_factory=list)
    termination_reason: str = "running"

    # -- Convenience views -------------------------------------------------
    @property
    def pending_task_ids(self) -> list[str]:
        return [t.task_id for t in self.tasks.values() if t.status == "pending"]

    @property
    def ready_task_ids(self) -> list[str]:
        return [t.task_id for t in self.tasks.values() if t.status == "ready"]

    @property
    def running_task_ids(self) -> list[str]:
        return [t.task_id for t in self.tasks.values() if t.status == "running"]

    @property
    def completed_task_ids(self) -> list[str]:
        return [t.task_id for t in self.tasks.values() if t.status == "completed"]

    @property
    def failed_task_ids(self) -> list[str]:
        return [t.task_id for t in self.tasks.values() if t.status == "failed"]

    @property
    def active_agents(self) -> list[AgentStateSnapshot]:
        return [a for a in self.agents.values() if a.state in ("active", "busy")]

    def as_dict(self) -> dict[str, Any]:
        return {
            "manager_type": self.manager_type,
            "elapsed_ms": round(self.elapsed_ms, 2),
            "pending_tasks": self.pending_task_ids,
            "ready_tasks": self.ready_task_ids,
            "running_tasks": self.running_task_ids,
            "completed_tasks": self.completed_task_ids,
            "failed_tasks": self.failed_task_ids,
            "agents": {k: v.as_dict() for k, v in self.agents.items()},
            "resources": self.resources.as_dict(),
            "recent_events": self.recent_events,
            "termination_reason": self.termination_reason,
        }

    # -- Construction ------------------------------------------------------
    @classmethod
    def from_trace(
        cls,
        trace: ExecutionTrace,
        graph: TaskGraph,
        agents: list[Agent],
        resources: ResourceUsage | None = None,
        termination_reason: str = "running",
        recent_event_limit: int = 20,
    ) -> "SimulationState":
        """Build a state snapshot by replaying the execution trace."""
        tasks: dict[str, TaskStateSnapshot] = {
            t.id: TaskStateSnapshot(
                task_id=t.id,
                status=t.status,
                priority=t.priority,
                required_capability=t.required_capability,
            )
            for t in graph.tasks
        }

        for task in tasks.values():
            if task.status in ("pending", "blocked"):
                task.status = "pending"

        # Track agent counters from decisions.
        agent_map: dict[str, AgentStateSnapshot] = {
            a.id: AgentStateSnapshot(
                agent_id=a.id,
                name=a.name,
                origin=a.origin,
                state="idle" if a.origin == "initial" else "inactive",
            )
            for a in agents
        }

        for rec in trace.decisions:
            event = canonical_event(rec.manager_decision)
            snap = tasks.get(rec.task_id)
            elapsed = float(rec.metadata.get("elapsed_ms", 0.0) or 0.0)

            if snap is not None:
                if event in ("task_scheduled", "task_started", "task_directed", "agent_reassigned"):
                    snap.status = "running"
                    snap.assigned_agent_id = rec.selected_agent_id
                    snap.attempts += 1 if event == "task_scheduled" else 0
                elif event == "task_completed":
                    snap.status = "completed"
                    snap.execution_ms += elapsed
                elif event in ("task_failed",):
                    snap.status = "failed"
                    if rec.metadata.get("max_retries_reached") or rec.metadata.get("deadlock"):
                        snap.attempts += 1
                elif event == "dependency_waiting":
                    if snap.status not in ("running", "completed", "failed"):
                        snap.status = "ready"
                elif event == "task_reassigned":
                    if snap.status != "completed":
                        snap.status = "ready"
                        snap.execution_ms += elapsed

            aid = rec.selected_agent_id
            if aid and aid in agent_map:
                agent = agent_map[aid]
                if event == "task_completed":
                    agent.tasks_completed += 1
                    agent.state = "active"
                elif event in ("task_failed", "task_reassigned"):
                    agent.tasks_failed += 1
                elif event in ("task_scheduled", "task_started"):
                    agent.state = "busy"

            if event == "agent_activated" and aid and aid in agent_map:
                agent_map[aid].state = "active"
            if event == "agent_failure" and aid and aid in agent_map:
                agent_map[aid].state = "failed"

        # Mark agents that no longer have running work as active/idle.
        for agent in agent_map.values():
            if agent.state not in ("failed", "inactive"):
                agent.state = "active"

        recent = [
            {
                "event": canonical_event(d.manager_decision),
                "manager_decision": d.manager_decision,
                "task_id": d.task_id,
                "agent_id": d.selected_agent_id,
                "timestamp": d.timestamp,
            }
            for d in trace.decisions[-recent_event_limit:]
        ]

        return cls(
            manager_type=trace.manager_type,
            elapsed_ms=trace.total_wall_ms,
            tasks=tasks,
            agents=agent_map,
            resources=resources or ResourceUsage(),
            recent_events=recent,
            termination_reason=termination_reason,
        )


# ---------------------------------------------------------------------------
# Generic manager decision (for future step-wise managers)
# ---------------------------------------------------------------------------


@dataclass
class ManagerDecision:
    """
    Generic action returned by a manager in a step-wise interaction model.

    Modules 5–6 managers execute atomically via ``run()``; this dataclass is
    the forward-compatible contract for managers that expose per-step
    decisions.  The engine records it like any other decision.
    """

    action: Literal[
        "ASSIGN_TASK",
        "ACTIVATE_AGENT",
        "DEACTIVATE_AGENT",
        "REPLAN",
        "RETRY_TASK",
        "STOP",
    ]
    task_id: str | None = None
    agent_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "task_id": self.task_id,
            "agent_id": self.agent_id,
            "metadata": self.metadata,
        }
