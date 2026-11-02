"""
Simulation Engine — manager-agnostic workload executor (Module 7).

Architecture
------------
::

    SimulationEngine
      ├── ResourceController     enforces generic limits (agents/decisions/wall)
      ├── EventLogger           ExecutionTrace + engine envelope events
      ├── MetricsCollector      execution / waiting / coordination / management time
      ├── ExperimentResult      structured per-manager result (Module 8 input)
      └── SimulationSession     step() / get_state() / stop()

    run / run_experiment
      ├── clone workload reference (never mutated)
      ├── clone + prepare agent pool identically for every manager
      ├── instantiate manager via registry (no direct imports)
      ├── manager.run(graph, agents) -> ExecutionTrace
      └── instrument trace, compute metrics, classify termination

Design decisions (and why they preserve Modules 0–6)
----------------------------------------------------
* Managers own task execution.  ``ManagerProtocol.run(graph, agents)`` is the
  established contract (Modules 5–6) and is *not* reimplemented here.  The
  engine supplies infrastructure, not orchestration policy.  Putting task
  execution in the engine would duplicate logic for the two managers and
  alter the experimental variable.
* All managers receive the *same* prepared agent pool and the same immutable
  TaskGraph.  Only the manager strategy differs.
* Generic limits are forwarded through the registry; the engine never imports
  ConventionalManager or POSDCORBManager.
* Reproducibility: every manager derives its RNG from ``rng_seed``; the engine
  applies failure probability deterministically to the cloned pool.

Termination reasons
-------------------
``all_tasks_completed`` | ``empty_workload`` | ``timeout`` |
``decision_budget_exhausted`` | ``unrecoverable_failure`` | ``error`` |
``manager_stopped``
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any

from app.agents.worker import WorkerResult, execute_task
from app.managers.base import DecisionRecord, ExecutionTrace, ManagerProtocol
from app.managers.registry import get_manager
from app.models.agent import Agent
from app.models.workload import Task, TaskGraph
from app.simulation.state import (
    AgentStateSnapshot,
    ResourceUsage,
    SimulationState,
    TaskStateSnapshot,
    canonical_event,
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass
class SimulationConfig:
    """
    Resource constraints and run parameters forwarded to the engine.

    Backward compatible with Module 6: existing keyword arguments keep their
    meaning and new fields default to \"no extra constraint\".
    """

    manager_types: list[str] = field(default_factory=lambda: ["conventional"])
    rng_seed: int = 42
    max_wall_ms: float = 300_000.0      # simulated wall-clock cap
    max_decisions: int = 10_000         # manager decision budget
    # LLM-call budgets are reserved for future LLM-backed managers
    max_llm_calls: int = 5_000
    token_budget: int = 500_000

    # Module 7 additions -------------------------------------------------
    max_agents: int | None = None          # cap on active agents (dynamic staffing)
    failure_probability: float | None = None  # uniform per-step agent failure rate
    experiment_id: str | None = None

    # Manager-specific options forwarded verbatim by the registry to any
    # manager whose constructor accepts a matching parameter.  The ablation
    # study uses this to inject a pre-built POSDCORBConfig without the engine
    # knowing anything about POSDCORB.
    manager_options: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Resource control
# ---------------------------------------------------------------------------


@dataclass
class ResourceController:
    """
    Enforces the generic resource envelope for one experiment.

    The controller is manager-neutral: it prepares identical inputs for every
    manager and records what was enforced.  Manager-specific policies (e.g.
    POSDCORB activation thresholds) remain inside the managers.
    """

    max_agents: int | None = None
    max_execution_time_ms: float = 300_000.0
    max_manager_decisions: int = 10_000
    max_llm_calls: int = 5_000
    max_tokens: int = 500_000
    failure_probability: float | None = None

    @classmethod
    def from_config(cls, config: SimulationConfig) -> "ResourceController":
        return cls(
            max_agents=config.max_agents,
            max_execution_time_ms=config.max_wall_ms,
            max_manager_decisions=config.max_decisions,
            max_llm_calls=config.max_llm_calls,
            max_tokens=config.token_budget,
            failure_probability=config.failure_probability,
        )

    def prepare_agents(self, agents: list[Agent]) -> list[Agent]:
        """
        Return an independent, identically-prepared copy of the agent pool.

        Failure probability is applied uniformly: an agent cannot be more
        reliable than ``1 - failure_probability``.  This is deterministic and
        identical for both managers, preserving experimental fairness.

        ``max_agents`` is a *shared* resource constraint, so it is enforced
        here rather than inside a single manager: every manager receives the
        same deterministically truncated pool.  Previously the cap was only
        advertised in an envelope event and POSDCORB enforced it internally
        while Conventional ignored it, which broke the "identical resource
        constraints" guarantee.
        """
        prepared = [a.model_copy(deep=True) for a in agents]
        if self.max_agents is not None:
            initials = [a for a in prepared if a.origin == "initial"]
            if len(initials) > int(self.max_agents):
                # Even the initial pool exceeds the cap: truncate the full
                # prepared list so neither manager receives more agents than
                # the limit.  Dynamic extras beyond this are also dropped.
                prepared = prepared[: int(self.max_agents)]
            # When initial agents fit within max_agents, dynamic-reserve agents
            # are preserved.  POSDCORB's Staffing function enforces the active
            # count internally; Conventional never activates extras.
            # This preserves experimental fairness: both managers see the same
            # pool, and max_agents remains the effective active-agent ceiling.
        if self.failure_probability is not None:
            p = min(max(float(self.failure_probability), 0.0), 1.0)
            for agent in prepared:
                agent.reliability = min(float(agent.reliability), 1.0 - p)
        return prepared

    def usage(self, trace: ExecutionTrace, agents_in_pool: int) -> ResourceUsage:
        manager_decisions = sum(
            1 for d in trace.decisions
            if not d.manager_decision.startswith("engine:")
        )
        # Prefer the manager-reported scheduling-decision count (the quantity
        # the decision budget actually caps).  Trace-record counts include
        # zero-cost observation events and are not comparable across managers.
        budget_decisions = (
            trace.budget_decisions_used
            if trace.budget_decisions_used is not None
            else manager_decisions
        )
        return ResourceUsage(
            decisions_used=budget_decisions,
            decisions_limit=self.max_manager_decisions,
            wall_ms_used=trace.total_wall_ms,
            wall_ms_limit=self.max_execution_time_ms,
            llm_calls_used=0,          # rule-based managers make no LLM calls
            llm_calls_limit=self.max_llm_calls,
            tokens_used=0,
            tokens_limit=self.max_tokens,
            agents_in_pool=agents_in_pool,
            max_agents=self.max_agents,
            failure_probability=self.failure_probability,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "max_agents": self.max_agents,
            "max_execution_time_ms": self.max_execution_time_ms,
            "max_manager_decisions": self.max_manager_decisions,
            "max_llm_calls": self.max_llm_calls,
            "max_tokens": self.max_tokens,
            "failure_probability": self.failure_probability,
        }


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


@dataclass
class ManagerStatistics:
    """Per-manager statistics derived solely from the ExecutionTrace."""

    decisions_by_type: dict[str, int] = field(default_factory=dict)
    assignments: int = 0
    retries: int = 0
    failures: int = 0
    waits: int = 0
    evaluations: int = 0

    # POSDCORB-specific counters (0 for Conventional)
    staffing_decisions: int = 0
    activations: int = 0
    reassignments: int = 0
    replans: int = 0
    recoveries: int = 0

    tasks_completed_by_agent: dict[str, int] = field(default_factory=dict)
    avg_decision_latency_ms: float = 0.0

    # Time decomposition (ms)
    management_time_ms: float = 0.0
    execution_time_ms: float = 0.0
    coordination_time_ms: float = 0.0
    waiting_time_ms: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "decisions_by_type": self.decisions_by_type,
            "assignments": self.assignments,
            "retries": self.retries,
            "failures": self.failures,
            "waits": self.waits,
            "evaluations": self.evaluations,
            "staffing_decisions": self.staffing_decisions,
            "activations": self.activations,
            "reassignments": self.reassignments,
            "replans": self.replans,
            "recoveries": self.recoveries,
            "tasks_completed_by_agent": self.tasks_completed_by_agent,
            "avg_decision_latency_ms": round(self.avg_decision_latency_ms, 2),
            "management_time_ms": round(self.management_time_ms, 2),
            "execution_time_ms": round(self.execution_time_ms, 2),
            "coordination_time_ms": round(self.coordination_time_ms, 2),
            "waiting_time_ms": round(self.waiting_time_ms, 2),
        }


class MetricsCollector:
    """Computes ManagerStatistics from an ExecutionTrace."""

    # Decisions that represent coordination / scheduling work.
    _COORDINATION = {
        "assign",
        "retry",
        "fail",
        "wait",
        "posdcorb:TASK_SCHEDULED",
        "posdcorb:TASK_DIRECTED",
        "posdcorb:TASK_REASSIGNED",
        "posdcorb:DEPENDENCY_WAIT",
        "posdcorb:STATUS_UPDATED",
    }

    @classmethod
    def collect(cls, trace: ExecutionTrace) -> ManagerStatistics:
        stats = ManagerStatistics()

        decisions = [
            d for d in trace.decisions
            if not d.manager_decision.startswith("engine:")
        ]

        for d in decisions:
            kind = d.manager_decision
            stats.decisions_by_type[kind] = (
                stats.decisions_by_type.get(kind, 0) + 1
            )
            stats.management_time_ms += float(d.decision_latency_ms or 0.0)

            if kind == "assign":
                stats.assignments += 1
            elif kind == "retry":
                stats.retries += 1
            elif kind == "fail":
                stats.failures += 1
            elif kind == "wait":
                stats.waits += 1
            elif kind == "evaluate":
                stats.evaluations += 1
                if d.success and d.selected_agent_id:
                    stats.tasks_completed_by_agent[d.selected_agent_id] = (
                        stats.tasks_completed_by_agent.get(d.selected_agent_id, 0)
                        + 1
                    )

            # POSDCORB counters
            if kind == "posdcorb:AGENT_ACTIVATED":
                stats.activations += 1
                stats.staffing_decisions += 1
            elif kind == "posdcorb:AGENT_REASSIGNED":
                # A genuine agent-failure reassignment (no shared ``retry``
                # accounting record exists for this path).
                stats.reassignments += 1
            elif kind == "posdcorb:TASK_REASSIGNED":
                # Observation event for the same retry already counted via
                # the shared ``retry`` record; do not double count.
                pass
            elif kind == "posdcorb:PLAN_UPDATED":
                stats.replans += 1
            elif kind == "posdcorb:RECOVERY_COMPLETED":
                stats.recoveries += 1

            # Execution time: both managers attach elapsed_ms to the
            # evaluate/retry record for every worker execution.
            if kind in ("evaluate", "retry"):
                stats.execution_time_ms += float(
                    d.metadata.get("elapsed_ms", 0.0) or 0.0
                )

            if kind in cls._COORDINATION:
                stats.coordination_time_ms += float(
                    d.decision_latency_ms or 0.0
                )

        if decisions:
            # Average only decisions that consumed simulated think-time.
            # Zero-cost observation events would otherwise dilute the mean
            # for the manager that logs more of them.
            cost_decisions = [
                d for d in decisions
                if float(d.decision_latency_ms or 0.0) > 0.0
            ]
            if cost_decisions:
                stats.avg_decision_latency_ms = (
                    stats.management_time_ms / len(cost_decisions)
                )

        # Idle/waiting time is the residual of simulated wall time that was
        # not spent executing work or thinking.
        stats.waiting_time_ms = max(
            0.0,
            float(trace.total_wall_ms)
            - stats.execution_time_ms
            - stats.management_time_ms,
        )

        return stats


# ---------------------------------------------------------------------------
# Experiment result
# ---------------------------------------------------------------------------


@dataclass
class ExperimentResult:
    """
    Structured result for ONE manager run (Module 8 input).

    Contains the full execution trace plus summary statistics, resource usage
    and the classified termination reason.
    """

    experiment_id: str
    manager_type: str
    seed: int
    workload_id: str
    start_time: str
    end_time: str
    total_duration_ms: float
    completed_tasks: int
    failed_tasks: int
    pending_tasks: int
    task_success_rate: float
    execution_trace: ExecutionTrace
    resource_usage: dict[str, Any]
    manager_statistics: dict[str, Any]
    termination_reason: str
    real_elapsed_s: float
    error: str | None = None

    def summary_dict(self) -> dict[str, Any]:
        """Trace-free summary suitable for API responses / dashboards."""
        return {
            "experiment_id": self.experiment_id,
            "manager_type": self.manager_type,
            "seed": self.seed,
            "workload_id": self.workload_id,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "total_duration_ms": round(self.total_duration_ms, 2),
            "completed_tasks": self.completed_tasks,
            "failed_tasks": self.failed_tasks,
            "pending_tasks": self.pending_tasks,
            "task_success_rate": round(self.task_success_rate, 4),
            "resource_usage": self.resource_usage,
            "manager_statistics": self.manager_statistics,
            "termination_reason": self.termination_reason,
            "real_elapsed_s": round(self.real_elapsed_s, 4),
            "error": self.error,
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            **self.summary_dict(),
            "execution_trace": self.execution_trace.as_dict(),
        }


# ---------------------------------------------------------------------------
# Aggregated run result
# ---------------------------------------------------------------------------


@dataclass
class SimulationResult:
    """
    Aggregated output of one engine.run() call.

    ``traces`` is preserved for backward compatibility (Module 6 callers);
    ``experiments`` carries the richer Module 7 structures.
    """

    graph_id: str
    seed: int
    started_at: str
    finished_at: str
    real_elapsed_s: float           # wall-clock seconds consumed by this process
    traces: dict[str, ExecutionTrace]   # manager_type → trace
    errors: dict[str, str] = field(default_factory=dict)  # manager_type → error msg

    experiments: dict[str, ExperimentResult] = field(default_factory=dict)
    termination_reasons: dict[str, str] = field(default_factory=dict)
    resource_usage: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "graph_id": self.graph_id,
            "seed": self.seed,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "real_elapsed_s": round(self.real_elapsed_s, 4),
            "traces": {k: v.as_dict() for k, v in self.traces.items()},
            "errors": self.errors,
            "experiments": {
                k: v.summary_dict() for k, v in self.experiments.items()
            },
            "termination_reasons": self.termination_reasons,
            "resource_usage": self.resource_usage,
        }


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


class SimulationEngine:
    """
    Manager-agnostic simulation runner.

    Example
    -------
    ::

        engine = SimulationEngine()
        result = engine.run(graph, agents, SimulationConfig(
            manager_types=["conventional", "posdcorb"],
            rng_seed=42,
        ))
        print(result.experiments["posdcorb"].termination_reason)
    """

    # -- Single-manager experiment -------------------------------------
    def run_experiment(
        self,
        graph: TaskGraph,
        agents: list[Agent],
        config: SimulationConfig | None = None,
        manager_type: str = "conventional",
    ) -> ExperimentResult:
        """
        Run exactly one manager against the workload and return a structured
        ExperimentResult.

        The graph is never mutated.  The agent pool is deep-copied and
        prepared identically for every manager.
        """
        if config is None:
            config = SimulationConfig(manager_types=[manager_type])

        controller = ResourceController.from_config(config)
        started_at = _now_iso()
        t0 = time.perf_counter()

        agent_copy = controller.prepare_agents(agents)
        error: str | None = None

        try:
            manager = get_manager(
                manager_type,
                rng_seed=config.rng_seed,
                max_wall_ms=config.max_wall_ms,
                max_agent_count=config.max_agents,
                max_decisions=config.max_decisions,
                manager_options=config.manager_options,
            )
            if not isinstance(manager, ManagerProtocol):
                raise TypeError(
                    f"Manager '{manager_type}' does not satisfy ManagerProtocol."
                )
            trace = manager.run(graph, agent_copy)
        except Exception as exc:  # noqa: BLE001 — one manager must not kill the batch
            error = str(exc)
            trace = ExecutionTrace(
                manager_type=manager_type,
                graph_id=graph.graph_id,
                seed=config.rng_seed,
            )
            trace.total_tasks = len(graph.tasks)
            trace.failed_tasks = len(graph.tasks)

        real_elapsed_s = time.perf_counter() - t0

        # Classify BEFORE instrumentation (decision counts are pre-envelope).
        recorded_decisions = sum(
            1 for d in trace.decisions
            if not d.manager_decision.startswith("engine:")
        )
        # Use the manager's own scheduling-decision count when available: it is
        # the quantity both managers cap and therefore the only fair basis for
        # the shared decision budget.  Trace-record counts inflate managers
        # that emit more observation events.
        manager_decisions = (
            trace.budget_decisions_used
            if trace.budget_decisions_used is not None
            else recorded_decisions
        )
        termination_reason = self._termination_reason(
            trace, graph, config, error, manager_decisions
        )

        self._instrument_trace(
            trace=trace,
            config=config,
            manager_type=manager_type,
            termination_reason=termination_reason,
            agent_pool_size=len(agents),
            manager_decision_count=manager_decisions,
        )

        ended_at = _now_iso()
        pending = max(0, trace.total_tasks - trace.completed_tasks - trace.failed_tasks)
        resources = controller.usage(trace, agents_in_pool=len(agent_copy))

        return ExperimentResult(
            experiment_id=(
                config.experiment_id
                or f"{graph.graph_id}::{manager_type}"
            ),
            manager_type=manager_type,
            seed=config.rng_seed,
            workload_id=graph.graph_id,
            start_time=started_at,
            end_time=ended_at,
            total_duration_ms=float(trace.total_wall_ms),
            completed_tasks=trace.completed_tasks,
            failed_tasks=trace.failed_tasks,
            pending_tasks=pending,
            task_success_rate=float(trace.success_rate),
            execution_trace=trace,
            resource_usage=resources.as_dict(),
            manager_statistics=MetricsCollector.collect(trace).as_dict(),
            termination_reason=termination_reason,
            real_elapsed_s=real_elapsed_s,
            error=error,
        )

    # -- Multi-manager batch -------------------------------------------
    def run(
        self,
        graph: TaskGraph,
        agents: list[Agent],
        config: SimulationConfig | None = None,
    ) -> SimulationResult:
        """
        Execute the TaskGraph through each requested manager.

        Both managers receive identical inputs; only the strategy differs.
        """
        if config is None:
            config = SimulationConfig()

        started_at = _now_iso()
        t0 = time.perf_counter()

        traces: dict[str, ExecutionTrace] = {}
        errors: dict[str, str] = {}
        experiments: dict[str, ExperimentResult] = {}

        for manager_type in config.manager_types:
            # A caller-supplied experiment ID identifies the *batch*.  Each
            # manager run needs its own ID, otherwise both results share one
            # key and a later write can overwrite the other run in storage.
            per_manager_config = config
            if config.experiment_id:
                per_manager_config = replace(
                    config,
                    experiment_id=f"{config.experiment_id}::{manager_type}",
                )
            result = self.run_experiment(
                graph, agents, per_manager_config, manager_type
            )
            experiments[manager_type] = result
            traces[manager_type] = result.execution_trace
            if result.error is not None:
                errors[manager_type] = result.error

        finished_at = _now_iso()
        real_elapsed_s = time.perf_counter() - t0

        return SimulationResult(
            graph_id=graph.graph_id,
            seed=graph.seed,
            started_at=started_at,
            finished_at=finished_at,
            real_elapsed_s=real_elapsed_s,
            traces=traces,
            errors=errors,
            experiments=experiments,
            termination_reasons={
                m: r.termination_reason for m, r in experiments.items()
            },
            resource_usage=ResourceController.from_config(config).as_dict(),
        )

    # -- Step-wise session API ------------------------------------------
    def create_session(
        self,
        graph: TaskGraph,
        agents: list[Agent],
        config: SimulationConfig | None = None,
    ) -> "SimulationSession":
        """
        Create a step-wise session over the requested managers.

        Each ``step()`` executes one complete manager run (the managers'
        internal scheduling loops are atomic in Modules 5–6).
        """
        return SimulationSession(self, graph, agents, config or SimulationConfig())

    def get_state(self, session: "SimulationSession") -> dict[str, Any]:
        """Return the current state of a simulation session."""
        return session.get_state()

    def stop(self, session: "SimulationSession") -> None:
        """Request that a simulation session stop before its next step."""
        session.stop()

    # -- Manager-neutral helpers (single source of truth) ----------------
    @staticmethod
    def execute_task(agent: Agent, task: Task) -> WorkerResult:
        """Execute one task via the shared deterministic worker (Module 4)."""
        return execute_task(agent, task)

    @staticmethod
    def check_dependencies(graph: TaskGraph, completed_ids: set[str]) -> list[Task]:
        """
        Return tasks whose dependencies are all satisfied by ``completed_ids``.

        Utility for step-wise execution and tests; it never mutates the graph.
        """
        return [
            task for task in graph.tasks
            if all(dep in completed_ids for dep in task.dependency_ids)
        ]

    # -- Internals -------------------------------------------------------
    @staticmethod
    def _termination_reason(
        trace: ExecutionTrace,
        graph: TaskGraph,
        config: SimulationConfig,
        error: str | None,
        manager_decisions: int,
    ) -> str:
        if error is not None:
            return "error"
        if len(graph.tasks) == 0:
            return "empty_workload"
        if trace.total_tasks > 0 and trace.completed_tasks >= trace.total_tasks:
            return "all_tasks_completed"
        if trace.total_wall_ms >= config.max_wall_ms:
            return "timeout"
        if manager_decisions >= config.max_decisions:
            return "decision_budget_exhausted"
        if trace.completed_tasks + trace.failed_tasks >= trace.total_tasks:
            return "unrecoverable_failure"
        return "manager_stopped"

    @staticmethod
    def _instrument_trace(
        trace: ExecutionTrace,
        config: SimulationConfig,
        manager_type: str,
        termination_reason: str,
        agent_pool_size: int,
        manager_decision_count: int,
    ) -> None:
        """
        Add the engine event envelope and record resource-budget overruns.

        Envelope events (``engine:*``) are always preserved.  The decision
        budget is *reported*, never enforced by deleting evidence: truncating
        the decision list would destroy raw execution events and change every
        metric derived from them.  Conventional does not self-enforce the
        budget, so it could previously lose part of its trace while POSDCORB
        (which stops internally) did not — an unfair asymmetry.
        """
        # Preserve the POSDCORB management report before instrumentation.
        management_report: dict[str, Any] | None = None
        if trace.decisions:
            management_report = trace.decisions[-1].metadata.get("management_report")

        # Split manager decisions from any pre-existing engine events.
        manager_decisions = [
            d for d in trace.decisions
            if not d.manager_decision.startswith("engine:")
        ]

        envelope: list[DecisionRecord] = [
            DecisionRecord(
                manager_decision="engine:EXPERIMENT_STARTED",
                task_id=None,
                selected_agent_id=None,
                selected_agent_name=None,
                decision_latency_ms=0.0,
                reason=f"Experiment started for manager '{manager_type}'.",
                result=None,
                success=None,
                metadata={
                    "manager_type": manager_type,
                    "seed": config.rng_seed,
                    "graph_id": trace.graph_id,
                    "max_wall_ms": config.max_wall_ms,
                    "max_decisions": config.max_decisions,
                    "max_agents": config.max_agents,
                    "failure_probability": config.failure_probability,
                    "agent_pool_size": agent_pool_size,
                },
            )
        ]

        if config.max_agents is not None and agent_pool_size > config.max_agents:
            envelope.append(
                DecisionRecord(
                    manager_decision="engine:RESOURCE_LIMIT",
                    task_id=None,
                    selected_agent_id=None,
                    selected_agent_name=None,
                    decision_latency_ms=0.0,
                    reason=(
                        f"Agent pool capped at max_agents={config.max_agents} "
                        f"before manager instantiation (requested {agent_pool_size})."
                    ),
                    result="capped",
                    success=None,
                    metadata={
                        "limit": "max_agents",
                        "limit_value": config.max_agents,
                        "requested": agent_pool_size,
                    },
                )
            )

        if manager_decision_count >= config.max_decisions:
            envelope.append(
                DecisionRecord(
                    manager_decision="engine:RESOURCE_LIMIT",
                    task_id=None,
                    selected_agent_id=None,
                    selected_agent_name=None,
                    decision_latency_ms=0.0,
                    reason=(
                        f"Manager decision budget exceeded: "
                        f"{manager_decision_count} > {config.max_decisions}. "
                        f"All raw decisions are preserved; the run is flagged "
                        f"as decision_budget_exhausted."
                    ),
                    result="limit_exceeded",
                    success=False,
                    metadata={
                        "limit": "max_manager_decisions",
                        "limit_value": config.max_decisions,
                        "requested": manager_decision_count,
                        "raw_trace_preserved": True,
                    },
                )
            )

        completion_metadata: dict[str, Any] = {
            "termination_reason": termination_reason,
            "manager_type": manager_type,
            "total_tasks": trace.total_tasks,
            "completed_tasks": trace.completed_tasks,
            "failed_tasks": trace.failed_tasks,
            "total_wall_ms": round(trace.total_wall_ms, 2),
            "manager_decisions": len(manager_decisions),
        }
        if management_report is not None:
            completion_metadata["management_report"] = management_report

        envelope.append(
            DecisionRecord(
                manager_decision="engine:EXPERIMENT_COMPLETED",
                task_id=None,
                selected_agent_id=None,
                selected_agent_name=None,
                decision_latency_ms=0.0,
                reason=f"Experiment completed: {termination_reason}.",
                result=(
                    "success"
                    if termination_reason in ("all_tasks_completed", "empty_workload")
                    else "failure"
                ),
                success=termination_reason in ("all_tasks_completed", "empty_workload"),
                metadata=completion_metadata,
            )
        )

        trace.decisions = envelope[:1] + manager_decisions + envelope[1:]
        trace.total_decisions = len(trace.decisions)

    # -- State snapshot ---------------------------------------------------
    @staticmethod
    def state_from_result(result: ExperimentResult, graph: TaskGraph,
                          agents: list[Agent]) -> SimulationState:
        """Build a SimulationState snapshot from an ExperimentResult."""
        usage = result.resource_usage
        resources = ResourceUsage(
            decisions_used=int(usage.get("decisions_used", 0)),
            decisions_limit=int(usage.get("decisions_limit", 0)),
            wall_ms_used=float(usage.get("wall_ms_used", 0.0)),
            wall_ms_limit=float(usage.get("wall_ms_limit", 0.0)),
            llm_calls_used=int(usage.get("llm_calls_used", 0)),
            llm_calls_limit=int(usage.get("llm_calls_limit", 0)),
            tokens_used=int(usage.get("tokens_used", 0)),
            tokens_limit=int(usage.get("tokens_limit", 0)),
            agents_in_pool=int(usage.get("agents_in_pool", 0)),
            max_agents=usage.get("max_agents"),
            failure_probability=usage.get("failure_probability"),
        )
        return SimulationState.from_trace(
            result.execution_trace,
            graph,
            agents,
            resources=resources,
            termination_reason=result.termination_reason,
        )


# ---------------------------------------------------------------------------
# Step-wise session
# ---------------------------------------------------------------------------


class SimulationSession:
    """
    Step-wise façade over ``SimulationEngine``.

    One ``step()`` runs one complete manager against the workload.  This maps
    the Module 7 ``step()/get_state()/stop()`` API onto the atomic manager
    contract from Modules 5–6 without duplicating scheduling logic.
    """

    def __init__(
        self,
        engine: SimulationEngine,
        graph: TaskGraph,
        agents: list[Agent],
        config: SimulationConfig,
    ) -> None:
        self._engine = engine
        self._graph = graph
        self._agents = agents
        self._config = config
        self._pending: list[str] = list(config.manager_types)
        self._results: dict[str, ExperimentResult] = {}
        self._errors: dict[str, str] = {}
        self._stopped = False
        self._started_at = _now_iso()
        self._t0 = time.perf_counter()
        self._controller = ResourceController.from_config(config)
        # Sessions are served by FastAPI's thread pool; guard the mutable
        # pending/results state so two concurrent step/stop calls cannot run
        # the same manager twice or corrupt the queue.
        self._lock = threading.Lock()

    # -- Execution -------------------------------------------------------
    def step(self) -> ExperimentResult | None:
        """Execute the next pending manager. Returns None when finished."""
        with self._lock:
            return self._step_locked()

    def _step_locked(self) -> ExperimentResult | None:
        if self._stopped or not self._pending:
            return None
        manager_type = self._pending.pop(0)
        per_manager_config = self._config
        if self._config.experiment_id:
            per_manager_config = replace(
                self._config,
                experiment_id=f"{self._config.experiment_id}::{manager_type}",
            )
        result = self._engine.run_experiment(
            self._graph, self._agents, per_manager_config, manager_type
        )
        self._results[manager_type] = result
        if result.error is not None:
            self._errors[manager_type] = result.error
        return result

    def run(self) -> SimulationResult:
        """Run all remaining managers (unless stopped)."""
        while True:
            with self._lock:
                if self._stopped or not self._pending:
                    break
                self._step_locked()
        with self._lock:
            return SimulationResult(
                graph_id=self._graph.graph_id,
                seed=self._config.rng_seed,
                started_at=self._started_at,
                finished_at=_now_iso(),
                real_elapsed_s=time.perf_counter() - self._t0,
                traces={m: r.execution_trace for m, r in self._results.items()},
                errors=dict(self._errors),
                experiments=dict(self._results),
                termination_reasons={
                    m: r.termination_reason for m, r in self._results.items()
                },
                resource_usage=self._controller.as_dict(),
            )

    # -- Introspection ---------------------------------------------------
    @property
    def finished(self) -> bool:
        return self._stopped or not self._pending

    def get_state(self) -> dict[str, Any]:
        """Return a JSON-serialisable snapshot of the session."""
        with self._lock:
            status = (
                "stopped" if self._stopped
                else ("completed" if not self._pending else "running")
            )
            return {
                "simulation_id": (
                    self._config.experiment_id or self._graph.graph_id
                ),
                "status": status,
                "seed": self._config.rng_seed,
                "pending_managers": list(self._pending),
                "completed_managers": list(self._results),
                "elapsed_s": round(time.perf_counter() - self._t0, 4),
                "resource_limits": self._controller.as_dict(),
                "results": {
                    manager: result.summary_dict()
                    for manager, result in self._results.items()
                },
            }

    def stop(self) -> None:
        """Stop before the next step; completed results are preserved."""
        with self._lock:
            self._stopped = True


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
