"""
Module 8 — Metric Data Models
==============================

All dataclasses here are plain Python (no Pydantic) for speed.
Each has an ``as_dict()`` method for JSON serialisation and CSV export.

Hierarchy
---------
DerivedMetrics              ← computed from ONE ExperimentResult
  ├── PerformanceMetrics    — latency, throughput, task counts
  ├── TaskMetrics           — per-task queue/wait/exec/retry
  ├── AgentMetrics          — utilisation, idle, active, activated
  ├── ManagementMetrics     — decision overhead, coordination ratio
  ├── ReliabilityMetrics    — failures, retries, recovery
  ├── CostMetrics           — budget consumption, activation cost
  └── ResourceMonitorMetrics — host CPU/memory (best-effort via psutil)

AggregatedMetrics           ← mean/median/std/min/max across N runs
ComparisonResult            ← side-by-side delta between two managers
ComparisonRow               ← one metric row inside a ComparisonResult
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_div(num: float, den: float, fallback: float = 0.0) -> float:
    """Division that returns fallback instead of ZeroDivisionError."""
    return num / den if den != 0.0 else fallback


def _pct(v: float) -> float:
    return round(v * 100.0, 4)


def _r(v: float, n: int = 4) -> float:
    return round(v, n)


# ---------------------------------------------------------------------------
# 1. Performance Metrics
# ---------------------------------------------------------------------------


@dataclass
class PerformanceMetrics:
    """
    Task-level performance indicators derived from one ExecutionTrace.

    Definitions
    -----------
    total_execution_latency_ms :
        Total simulated wall-clock time consumed by the manager run.
    avg_task_latency_ms :
        total_execution_latency_ms / completed_tasks.
        0 when no tasks completed.
    throughput_tasks_per_sec :
        completed_tasks / (total_execution_latency_ms / 1000).
        0 when total_execution_latency_ms == 0.
    task_success_rate :
        completed_tasks / total_tasks.  [0, 1].
    task_completion_rate :
        (completed_tasks + failed_tasks) / total_tasks.  [0, 1].
    """

    # Raw counts (sourced directly from ExecutionTrace)
    total_tasks: int = 0
    completed_tasks: int = 0
    failed_tasks: int = 0
    pending_tasks: int = 0

    # Latency (ms)
    total_execution_latency_ms: float = 0.0
    avg_task_latency_ms: float = 0.0
    median_task_latency_ms: float = 0.0
    min_task_latency_ms: float = 0.0
    max_task_latency_ms: float = 0.0

    # Throughput (tasks / second of simulated time)
    throughput_tasks_per_sec: float = 0.0

    # Rates [0.0, 1.0]
    task_success_rate: float = 0.0
    task_completion_rate: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "total_tasks": self.total_tasks,
            "completed_tasks": self.completed_tasks,
            "failed_tasks": self.failed_tasks,
            "pending_tasks": self.pending_tasks,
            "total_execution_latency_ms": _r(self.total_execution_latency_ms),
            "avg_task_latency_ms": _r(self.avg_task_latency_ms),
            "median_task_latency_ms": _r(self.median_task_latency_ms),
            "min_task_latency_ms": _r(self.min_task_latency_ms),
            "max_task_latency_ms": _r(self.max_task_latency_ms),
            "throughput_tasks_per_sec": _r(self.throughput_tasks_per_sec, 6),
            "task_success_rate": _r(self.task_success_rate),
            "task_completion_rate": _r(self.task_completion_rate),
        }


# ---------------------------------------------------------------------------
# 2. Task-level Metrics
# ---------------------------------------------------------------------------


@dataclass
class SingleTaskMetrics:
    """
    Per-task timing and outcome record.

    Fields
    ------
    queue_time_ms :
        Estimated time the task spent waiting before its first assignment.
        Approximated as decision_latency_ms of the "assign"/"directed" record.
    execution_time_ms :
        Time spent actually executing (elapsed_ms from evaluate/retry records).
    total_latency_ms :
        queue_time_ms + execution_time_ms.
    retry_count :
        Number of retry decisions for this task.
    reassignment_count :
        Number of reassignment events for this task.
    """

    task_id: str
    status: str = "unknown"          # completed | failed | pending
    assigned_agent_id: str | None = None
    queue_time_ms: float = 0.0
    execution_time_ms: float = 0.0
    total_latency_ms: float = 0.0
    retry_count: int = 0
    reassignment_count: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "status": self.status,
            "assigned_agent_id": self.assigned_agent_id,
            "queue_time_ms": _r(self.queue_time_ms),
            "execution_time_ms": _r(self.execution_time_ms),
            "total_latency_ms": _r(self.total_latency_ms),
            "retry_count": self.retry_count,
            "reassignment_count": self.reassignment_count,
        }


@dataclass
class TaskMetrics:
    """Collection of per-task metrics for one manager run."""

    tasks: dict[str, SingleTaskMetrics] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {tid: t.as_dict() for tid, t in self.tasks.items()}

    def as_rows(self) -> list[dict[str, Any]]:
        return [t.as_dict() for t in self.tasks.values()]


# ---------------------------------------------------------------------------
# 3. Agent Utilisation Metrics
# ---------------------------------------------------------------------------


@dataclass
class SingleAgentMetrics:
    """
    Per-agent utilisation record.

    agent_utilization :
        busy_time_ms / available_time_ms.  [0, 1].
        available_time_ms == total_execution_latency_ms for the run.
    """

    agent_id: str
    agent_name: str
    origin: str = "initial"        # "initial" | "dynamic"
    tasks_completed: int = 0
    tasks_failed: int = 0
    busy_time_ms: float = 0.0      # estimated from evaluate/retry elapsed_ms
    available_time_ms: float = 0.0  # = total_execution_latency_ms
    agent_utilization: float = 0.0  # busy_time_ms / available_time_ms
    is_activated: bool = False      # True for POSDCORB dynamic activations

    def as_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "agent_name": self.agent_name,
            "origin": self.origin,
            "tasks_completed": self.tasks_completed,
            "tasks_failed": self.tasks_failed,
            "busy_time_ms": _r(self.busy_time_ms),
            "available_time_ms": _r(self.available_time_ms),
            "agent_utilization": _r(self.agent_utilization),
            "is_activated": self.is_activated,
        }


@dataclass
class AgentMetrics:
    """
    Aggregate agent utilisation for one manager run.

    avg_agent_utilization :
        Mean of per-agent utilisation values.
    max_simultaneous_agents :
        Peak number of agents concurrently active/busy.
        Estimated from the trace; exact value not tracked by current managers,
        so reported as available_agent_count (conservative upper bound).
    """

    initial_agent_count: int = 0
    dynamic_agent_count: int = 0   # POSDCORB activations only
    total_active_agents: int = 0   # initial + dynamic
    avg_agent_utilization: float = 0.0
    max_simultaneous_agents: int = 0
    activation_count: int = 0      # number of AGENT_ACTIVATED events
    failed_agent_count: int = 0
    per_agent: dict[str, SingleAgentMetrics] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "initial_agent_count": self.initial_agent_count,
            "dynamic_agent_count": self.dynamic_agent_count,
            "total_active_agents": self.total_active_agents,
            "avg_agent_utilization": _r(self.avg_agent_utilization),
            "max_simultaneous_agents": self.max_simultaneous_agents,
            "activation_count": self.activation_count,
            "failed_agent_count": self.failed_agent_count,
            "per_agent": {k: v.as_dict() for k, v in self.per_agent.items()},
        }


# ---------------------------------------------------------------------------
# 4. Management Overhead Metrics
# ---------------------------------------------------------------------------


@dataclass
class ManagementMetrics:
    """
    Manager decision overhead for one run.

    coordination_overhead_ratio :
        management_time_ms / total_execution_latency_ms.
        0 when total_execution_latency_ms == 0.

    Definitions
    -----------
    management_time_ms :
        Sum of decision_latency_ms across all manager decisions
        (excludes engine envelope events).
    planning_events :
        Count of PLAN_CREATED + PLAN_UPDATED decisions.
    staffing_decisions :
        Count of AGENT_ACTIVATED decisions.
    coordination_events :
        Count of scheduling/sequencing decisions (assign, wait, directed,
        scheduled, dependency_wait, status_updated).
    replanning_events :
        Count of PLAN_UPDATED decisions.
    reassignment_count :
        Count of TASK_REASSIGNED / AGENT_REASSIGNED decisions.
    dependency_wait_events :
        Count of DEPENDENCY_WAIT / wait decisions.
    """

    total_decisions: int = 0
    management_time_ms: float = 0.0
    avg_decision_latency_ms: float = 0.0
    planning_events: int = 0
    staffing_decisions: int = 0
    coordination_events: int = 0
    replanning_events: int = 0
    reassignment_count: int = 0
    dependency_wait_events: int = 0
    coordination_overhead_ratio: float = 0.0   # management_time / total_wall

    def as_dict(self) -> dict[str, Any]:
        return {
            "total_decisions": self.total_decisions,
            "management_time_ms": _r(self.management_time_ms),
            "avg_decision_latency_ms": _r(self.avg_decision_latency_ms),
            "planning_events": self.planning_events,
            "staffing_decisions": self.staffing_decisions,
            "coordination_events": self.coordination_events,
            "replanning_events": self.replanning_events,
            "reassignment_count": self.reassignment_count,
            "dependency_wait_events": self.dependency_wait_events,
            "coordination_overhead_ratio": _r(self.coordination_overhead_ratio),
        }


# ---------------------------------------------------------------------------
# 5. Dynamic Staffing Metrics
# ---------------------------------------------------------------------------


@dataclass
class DynamicStaffingMetrics:
    """
    Metrics specifically for evaluating dynamic agent activation (POSDCORB).

    For the Conventional Manager all fields are 0 / None — these metrics are
    still computed so comparisons remain structurally identical.

    activation_rate :
        activation_count / total_decisions (fraction of decisions about staffing).
    tasks_per_active_agent :
        completed_tasks / total_active_agents.

    Note: "Did staffing improve performance?" requires comparing this object
    against the Conventional baseline's PerformanceMetrics — see ComparisonResult.
    """

    initial_agent_count: int = 0
    max_active_agents: int = 0
    avg_active_agents: float = 0.0      # approximated from activated count
    activation_count: int = 0
    activation_rate: float = 0.0        # activations / total_decisions
    estimated_activation_cost_ms: float = 0.0  # from ResourceBudget metadata
    tasks_per_active_agent: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "initial_agent_count": self.initial_agent_count,
            "max_active_agents": self.max_active_agents,
            "avg_active_agents": _r(self.avg_active_agents),
            "activation_count": self.activation_count,
            "activation_rate": _r(self.activation_rate),
            "estimated_activation_cost_ms": _r(self.estimated_activation_cost_ms),
            "tasks_per_active_agent": _r(self.tasks_per_active_agent),
        }


# ---------------------------------------------------------------------------
# 6. Reliability & Recovery Metrics
# ---------------------------------------------------------------------------


@dataclass
class ReliabilityMetrics:
    """
    Failure and recovery metrics for one manager run.

    recovery_success_rate :
        recoveries / max(1, task_failures + agent_failures).
        0 when no failures occurred.
    tasks_lost_due_to_failures :
        failed_tasks − recovered_tasks (cannot go below 0).
    """

    agent_failure_count: int = 0
    task_failure_count: int = 0
    retry_count: int = 0
    reassignment_count: int = 0
    recovery_count: int = 0
    recovery_success_rate: float = 0.0
    avg_recovery_time_ms: float = 0.0    # not directly tracked; set to 0.0
    tasks_lost_due_to_failures: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "agent_failure_count": self.agent_failure_count,
            "task_failure_count": self.task_failure_count,
            "retry_count": self.retry_count,
            "reassignment_count": self.reassignment_count,
            "recovery_count": self.recovery_count,
            "recovery_success_rate": _r(self.recovery_success_rate),
            "avg_recovery_time_ms": _r(self.avg_recovery_time_ms),
            "tasks_lost_due_to_failures": self.tasks_lost_due_to_failures,
        }


# ---------------------------------------------------------------------------
# 7. Cost / Budget Metrics
# ---------------------------------------------------------------------------


@dataclass
class CostMetrics:
    """
    Resource budget consumption metrics.

    ``budget_decisions_*`` measure the manager's *scheduling decisions* — the
    quantity both managers cap internally — not the number of trace records
    (which also counts zero-cost observation events).
    Null/zero fields for LLM-related budgets when no LLM is in use.
    These are reported as 0 rather than fabricated estimates.
    """

    budget_decisions_allocated: int = 0
    budget_decisions_consumed: int = 0
    budget_decisions_utilization: float = 0.0   # consumed / allocated

    budget_wall_ms_allocated: float = 0.0
    budget_wall_ms_consumed: float = 0.0
    budget_wall_ms_utilization: float = 0.0

    activation_cost_ms: float = 0.0   # from POSDCORB ResourceBudget
    coordination_cost_ms: float = 0.0  # management_time for coordination decisions

    # LLM (not implemented — always 0 in rule-based simulation)
    llm_calls_used: int = 0
    llm_calls_allocated: int = 0
    tokens_used: int = 0
    tokens_allocated: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "budget_decisions_allocated": self.budget_decisions_allocated,
            "budget_decisions_consumed": self.budget_decisions_consumed,
            "budget_decisions_utilization": _r(self.budget_decisions_utilization),
            "budget_wall_ms_allocated": _r(self.budget_wall_ms_allocated),
            "budget_wall_ms_consumed": _r(self.budget_wall_ms_consumed),
            "budget_wall_ms_utilization": _r(self.budget_wall_ms_utilization),
            "activation_cost_ms": _r(self.activation_cost_ms),
            "coordination_cost_ms": _r(self.coordination_cost_ms),
            "llm_calls_used": self.llm_calls_used,
            "llm_calls_allocated": self.llm_calls_allocated,
            "tokens_used": self.tokens_used,
            "tokens_allocated": self.tokens_allocated,
        }


# ---------------------------------------------------------------------------
# 8. Host Resource Monitor Metrics  (psutil — best-effort)
# ---------------------------------------------------------------------------


@dataclass
class ResourceMonitorMetrics:
    """
    Actual host CPU and memory during a simulation run.

    IMPORTANT DISTINCTION
    ---------------------
    These values reflect host process utilisation while running the Python
    simulation loop — NOT the simulated computational workload of the synthetic
    tasks.  The simulation uses deterministic, lightweight Python functions;
    actual CPU load is negligible and does NOT reflect the modelled agent
    workload.

    available :
        False when psutil is not installed or monitoring failed.
        Consumers should not draw performance conclusions from these fields
        when available == False.
    """

    available: bool = False
    avg_cpu_percent: float = 0.0
    peak_cpu_percent: float = 0.0
    avg_memory_mb: float = 0.0
    peak_memory_mb: float = 0.0
    # Number of host-resource samples taken
    sample_count: int = 0
    unavailable_reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "avg_cpu_percent": _r(self.avg_cpu_percent),
            "peak_cpu_percent": _r(self.peak_cpu_percent),
            "avg_memory_mb": _r(self.avg_memory_mb),
            "peak_memory_mb": _r(self.peak_memory_mb),
            "sample_count": self.sample_count,
            "unavailable_reason": self.unavailable_reason,
        }


# ---------------------------------------------------------------------------
# 9. DerivedMetrics — full per-run metric bundle
# ---------------------------------------------------------------------------


@dataclass
class DerivedMetrics:
    """
    Complete metric set derived from ONE ExperimentResult.

    Kept separate from the raw ExperimentResult so raw simulation data
    is never mutated by metric computation.
    """

    manager_type: str
    experiment_id: str
    seed: int
    workload_id: str

    performance: PerformanceMetrics = field(default_factory=PerformanceMetrics)
    tasks: TaskMetrics = field(default_factory=TaskMetrics)
    agents: AgentMetrics = field(default_factory=AgentMetrics)
    management: ManagementMetrics = field(default_factory=ManagementMetrics)
    staffing: DynamicStaffingMetrics = field(default_factory=DynamicStaffingMetrics)
    reliability: ReliabilityMetrics = field(default_factory=ReliabilityMetrics)
    cost: CostMetrics = field(default_factory=CostMetrics)
    resource_monitor: ResourceMonitorMetrics = field(
        default_factory=ResourceMonitorMetrics
    )

    def as_dict(self) -> dict[str, Any]:
        return {
            "manager_type": self.manager_type,
            "experiment_id": self.experiment_id,
            "seed": self.seed,
            "workload_id": self.workload_id,
            "performance": self.performance.as_dict(),
            "tasks": self.tasks.as_dict(),
            "agents": self.agents.as_dict(),
            "management": self.management.as_dict(),
            "staffing": self.staffing.as_dict(),
            "reliability": self.reliability.as_dict(),
            "cost": self.cost.as_dict(),
            "resource_monitor": self.resource_monitor.as_dict(),
        }


# ---------------------------------------------------------------------------
# 10. Aggregated Metrics — statistics across N runs
# ---------------------------------------------------------------------------


def _agg_numeric(values: list[float]) -> dict[str, float]:
    """Compute mean/median/std/min/max for a list of floats."""
    if not values:
        return {"mean": 0.0, "median": 0.0, "std": 0.0, "min": 0.0, "max": 0.0, "n": 0}
    n = len(values)
    mn = statistics.mean(values)
    md = statistics.median(values)
    std = statistics.stdev(values) if n >= 2 else 0.0
    return {
        "mean": _r(mn),
        "median": _r(md),
        "std": _r(std),
        "min": _r(min(values)),
        "max": _r(max(values)),
        "n": n,
    }


@dataclass
class AggregatedMetrics:
    """
    Statistical summary across multiple runs of the same manager.

    raw_runs preserves individual DerivedMetrics so no experiment data
    is overwritten or lost.
    """

    manager_type: str
    run_count: int = 0
    raw_runs: list[DerivedMetrics] = field(default_factory=list)

    # Aggregated performance scalars
    throughput: dict[str, float] = field(default_factory=dict)
    total_latency_ms: dict[str, float] = field(default_factory=dict)
    avg_task_latency_ms: dict[str, float] = field(default_factory=dict)
    task_success_rate: dict[str, float] = field(default_factory=dict)
    agent_utilization: dict[str, float] = field(default_factory=dict)
    coordination_overhead: dict[str, float] = field(default_factory=dict)
    activation_count: dict[str, float] = field(default_factory=dict)
    recovery_count: dict[str, float] = field(default_factory=dict)
    task_failure_count: dict[str, float] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "manager_type": self.manager_type,
            "run_count": self.run_count,
            "throughput": self.throughput,
            "total_latency_ms": self.total_latency_ms,
            "avg_task_latency_ms": self.avg_task_latency_ms,
            "task_success_rate": self.task_success_rate,
            "agent_utilization": self.agent_utilization,
            "coordination_overhead": self.coordination_overhead,
            "activation_count": self.activation_count,
            "recovery_count": self.recovery_count,
            "task_failure_count": self.task_failure_count,
        }


# ---------------------------------------------------------------------------
# 11. Comparison
# ---------------------------------------------------------------------------


@dataclass
class ComparisonRow:
    """
    One metric comparison between two managers.

    higher_is_better :
        True  → the manager with the larger value is "better"
                 (e.g. throughput, success_rate, utilization).
        False → the manager with the smaller value is "better"
                 (e.g. latency, overhead, failures).
        None  → context-dependent; no automatic direction is claimed.
                Context-dependent metrics are not used to declare a winner.

    better_manager :
        Identifier string of the manager that wins on this metric, or
        "tie" when values are identical, or "context_dependent".

    pct_difference :
        ((posdcorb - conventional) / |conventional|) * 100.
        Positive = POSDCORB is larger; negative = Conventional is larger.
        This is a signed, directional percentage.  When the baseline is zero
        the relative change is undefined; pct_difference is then 0.0 and the
        interpretation states that explicitly.

    NOTE: A lower-latency result with lower throughput is NOT automatically
    "better".  Callers must interpret ComparisonRows jointly.
    """

    metric: str
    conventional_value: float
    posdcorb_value: float
    absolute_difference: float        # posdcorb − conventional
    pct_difference: float             # signed %
    higher_is_better: bool | None
    better_manager: str               # "conventional" | "posdcorb" | "tie"
    interpretation: str               # human-readable one-liner

    def as_dict(self) -> dict[str, Any]:
        return {
            "metric": self.metric,
            "conventional_value": _r(self.conventional_value),
            "posdcorb_value": _r(self.posdcorb_value),
            "absolute_difference": _r(self.absolute_difference),
            "pct_difference": _r(self.pct_difference),
            "higher_is_better": self.higher_is_better,
            "better_manager": self.better_manager,
            "interpretation": self.interpretation,
        }


@dataclass
class ComparisonResult:
    """
    Side-by-side comparison of Conventional vs POSDCORB on a shared workload.
    """

    workload_id: str
    seed: int
    rows: list[ComparisonRow] = field(default_factory=list)
    conventional_metrics: DerivedMetrics | None = None
    posdcorb_metrics: DerivedMetrics | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "workload_id": self.workload_id,
            "seed": self.seed,
            "rows": [r.as_dict() for r in self.rows],
            "conventional_metrics": (
                self.conventional_metrics.as_dict()
                if self.conventional_metrics else None
            ),
            "posdcorb_metrics": (
                self.posdcorb_metrics.as_dict()
                if self.posdcorb_metrics else None
            ),
        }
