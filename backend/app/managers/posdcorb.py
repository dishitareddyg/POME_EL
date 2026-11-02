"""
POSDCORB-Based Adaptive Manager — core research component.

Design principle
----------------
This manager implements all seven POSDCORB functions as explicit, observable
mechanisms that produce measurable behavioural differences from the
Conventional Manager.

P — Planning      : Builds an ExecutionPlan before any task runs.
O — Organizing    : Groups tasks into capability-aligned TaskGroups.
S — Staffing      : Activates dynamic agents when workload justifies it.
D — Directing     : Emits a structured TaskDirective for each assignment.
CO — Coordinating : Enforces dependency ordering, maximises parallelism.
R — Reporting     : Maintains a live ManagementReport throughout.
B — Budgeting     : Tracks and enforces all configured resource limits.

Research constraint
-------------------
This manager CAN be outperformed by the Conventional Manager when:
  - workload is very small (planning + organising overhead dominates)
  - additional agents introduce activation cost that exceeds parallelism gain
  - the graph is purely sequential (no parallelism to exploit)

The manager is deterministic: same seed → same decisions.
All thresholds come from POSDCORBConfig (not hard-coded).
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any

from app.agents.worker import execute_task
from app.managers.base import BaseManager, DecisionRecord, ExecutionTrace
from app.managers.principles import (
    ALL_PRINCIPLES,
    PRINCIPLE_LABELS,
    normalize_principles,
)
from app.models.agent import Agent
from app.models.workload import Task, TaskGraph

# ---------------------------------------------------------------------------
# POSDCORB-specific configuration (all thresholds configurable)
# ---------------------------------------------------------------------------

PRIORITY_ORDER: dict[str, int] = {
    "critical": 0,
    "high": 1,
    "medium": 2,
    "low": 3,
}

COMPLEXITY_WEIGHT: dict[str, float] = {
    "low": 1.0,
    "medium": 2.5,
    "high": 5.0,
}


@dataclass
class POSDCORBConfig:
    """
    All tunable thresholds for the POSDCORB Manager.

    These control when the manager activates agents, when it replans,
    and how much overhead it tolerates. They are NOT hard-coded — each
    can be overridden at instantiation time to study different regimes.
    """

    # Staffing thresholds
    agent_activation_cost: float = 50.0        # ms overhead per newly activated agent
    activation_pending_threshold: int = 3      # min pending-parallel tasks to justify activation
    activation_utilisation_threshold: float = 0.8  # busy fraction above which to consider scaling

    # Hard cap on the ACTIVE agent count.  None → capped at the size of the
    # full pool (initial + dynamic).  The manager must NEVER exceed this.
    max_agent_count: int | None = None

    # Replanning triggers
    replan_on_agent_failure: bool = True
    replan_on_task_failure: bool = True
    replan_slow_task_multiplier: float = 2.5   # task is "slow" if elapsed > estimated × this
    replan_on_budget_critical: bool = True     # replan once when budget turns critical
    budget_critical_threshold: float = 0.8     # wall fraction considered "critical"

    # Failure recovery: consecutive task failures before an agent is
    # considered FAILED (only quarantined if a replacement exists).
    agent_failure_streak_threshold: int = 3

    # Bottleneck detection: fail fast when no agent in the whole pool can
    # ever serve a task's capability, instead of spinning until wall budget.
    deadlock_detection: bool = True

    # Manager-decision budget (B).  Enforced inside the manager itself so
    # POSDCORB cannot exceed the same decision budget given to Conventional.
    max_manager_decisions: int = 10_000

    # LLM-call / token budgets (reserved; rule-based manager consumes 0).
    max_llm_calls: int = 5_000
    token_budget: int = 500_000

    # Decision latency model (mirrors Conventional Manager for fair comparison)
    decision_latency_mean_ms: float = 50.0
    decision_latency_std_ms: float = 10.0

    # Organising: minimum tasks per group before splitting
    min_group_size: int = 2

    # Retry limit per task
    max_retries: int = 3

    # Simulated wall-clock safety cap
    max_wall_ms: float = 300_000.0

    # ------------------------------------------------------------------
    # Modular principle switches (ablation study support)
    # ------------------------------------------------------------------
    # Any subset of the seven POSDCORB mechanisms can be enabled.  The
    # default is the full manager.  Disabled mechanisms emit no events,
    # consume no management decisions, and do not influence scheduling.
    enabled_principles: frozenset[str] = ALL_PRINCIPLES

    # Cross-cutting adaptive features that are not single principles:
    # task/agent-failure replanning, slow-task replanning, and the
    # failure-recovery chain.  "Full POSDCORB" enables these; the
    # cumulative ablation rungs disable them so that each measured delta
    # isolates one principle.
    adaptive_mechanisms: bool = True

    def __post_init__(self) -> None:
        # Validate principle keys early so a typo never silently changes a
        # research configuration.
        object.__setattr__(
            self,
            "enabled_principles",
            normalize_principles(self.enabled_principles),
        )

    # Convenience accessors -------------------------------------------
    @property
    def planning_enabled(self) -> bool:
        return "planning" in self.enabled_principles

    @property
    def organizing_enabled(self) -> bool:
        return "organizing" in self.enabled_principles

    @property
    def staffing_enabled(self) -> bool:
        return "staffing" in self.enabled_principles

    @property
    def directing_enabled(self) -> bool:
        return "directing" in self.enabled_principles

    @property
    def coordinating_enabled(self) -> bool:
        return "coordinating" in self.enabled_principles

    @property
    def reporting_enabled(self) -> bool:
        return "reporting" in self.enabled_principles

    @property
    def budgeting_enabled(self) -> bool:
        return "budgeting" in self.enabled_principles

    def enabled_principle_labels(self) -> list[str]:
        return [
            PRINCIPLE_LABELS[p]
            for p in PRINCIPLE_LABELS
            if p in self.enabled_principles
        ]



# ---------------------------------------------------------------------------
# Budgeting data structures
# ---------------------------------------------------------------------------


@dataclass
class ResourceBudget:
    """
    Explicit resource budget tracked continuously by the Budgeting function.

    The manager consults this before every costly action (agent activation,
    further scheduling) and never knowingly exceeds the configured limits.

    Consumption fields are updated live during the run and mirrored into
    the ManagementReport for the dashboard / metrics engine.
    """

    # Limits (from POSDCORBConfig / ExperimentConfig.resources)
    max_agents: int                     # hard cap on active agents
    max_execution_time_ms: float        # simulated wall-clock cap
    max_manager_decisions: int          # total manager decision cap
    max_llm_calls: int                  # reserved (rule-based manager uses 0)
    max_tokens: int                     # reserved (rule-based manager uses 0)
    agent_activation_cost_ms: float     # per-activation overhead

    # Consumption (live)
    consumed_wall_ms: float = 0.0
    consumed_decisions: int = 0
    consumed_llm_calls: int = 0
    consumed_tokens: int = 0
    consumed_activation_ms: float = 0.0   # activation_cost × activations

    @property
    def wall_fraction_used(self) -> float:
        if self.max_execution_time_ms <= 0:
            return 1.0
        return min(1.0, self.consumed_wall_ms / self.max_execution_time_ms)

    @property
    def decision_fraction_used(self) -> float:
        if self.max_manager_decisions <= 0:
            return 1.0
        return min(1.0, self.consumed_decisions / self.max_manager_decisions)

    @property
    def overall_fraction_used(self) -> float:
        """Worst-case fraction across all budgeted dimensions."""
        return max(self.wall_fraction_used, self.decision_fraction_used)

    def can_activate(self) -> bool:
        """
        True if activating one more agent would stay within budget.

        Activation is refused when:
          - the active-agent cap is reached, OR
          - paying the activation cost would exhaust the wall budget.
        """
        remaining_wall = self.max_execution_time_ms - self.consumed_wall_ms
        return (
            self.consumed_activation_ms / self.agent_activation_cost_ms < self.max_agents
            and remaining_wall > self.agent_activation_cost_ms
        )

    def charge_activation(self) -> None:
        """Charge one agent activation against the budget."""
        self.consumed_activation_ms += self.agent_activation_cost_ms

    def as_dict(self) -> dict[str, Any]:
        return {
            "max_agents": self.max_agents,
            "max_execution_time_ms": self.max_execution_time_ms,
            "max_manager_decisions": self.max_manager_decisions,
            "max_llm_calls": self.max_llm_calls,
            "max_tokens": self.max_tokens,
            "agent_activation_cost_ms": self.agent_activation_cost_ms,
            "consumed_wall_ms": round(self.consumed_wall_ms, 2),
            "consumed_decisions": self.consumed_decisions,
            "consumed_llm_calls": self.consumed_llm_calls,
            "consumed_tokens": self.consumed_tokens,
            "consumed_activation_ms": round(self.consumed_activation_ms, 2),
            "wall_fraction_used": round(self.wall_fraction_used, 4),
            "decision_fraction_used": round(self.decision_fraction_used, 4),
            "overall_fraction_used": round(self.overall_fraction_used, 4),
        }


# ---------------------------------------------------------------------------
# Planning data structures
# ---------------------------------------------------------------------------


@dataclass
class ExecutionPlan:
    """
    The pre-execution plan produced by the Planning function.

    Stored verbatim in the ExecutionTrace metadata so it can be inspected
    or exported by the metrics engine.
    """

    graph_id: str
    total_tasks: int
    dependency_edges: dict[str, list[str]]     # task_id → list[successor_id]
    reverse_deps: dict[str, list[str]]         # task_id → list[predecessor_id]
    parallel_groups: list[list[str]]           # batches of task_ids that can run simultaneously
    priority_order: list[str]                  # task_ids sorted by priority then index
    estimated_total_workload_ms: float
    estimated_bottleneck_task_ids: list[str]   # tasks on critical path
    required_capabilities: list[str]           # deduplicated
    initial_pool_sufficient: bool              # whether N_init >= parallelism demand
    plan_notes: list[str]                      # human-readable observations

    def as_dict(self) -> dict[str, Any]:
        return {
            "graph_id": self.graph_id,
            "total_tasks": self.total_tasks,
            "parallel_groups": self.parallel_groups,
            "priority_order": self.priority_order,
            "estimated_total_workload_ms": self.estimated_total_workload_ms,
            "estimated_bottleneck_task_ids": self.estimated_bottleneck_task_ids,
            "required_capabilities": self.required_capabilities,
            "initial_pool_sufficient": self.initial_pool_sufficient,
            "plan_notes": self.plan_notes,
        }


# ---------------------------------------------------------------------------
# Organising data structures
# ---------------------------------------------------------------------------


@dataclass
class TaskGroup:
    """
    A logical group of tasks sharing the same required capability.

    The organisational structure:
        Manager
          ├── TaskGroup(capability=A) → [task1, task2, ...]
          └── TaskGroup(capability=B) → [task3, task4, ...]
    """

    group_id: str
    required_capability: str
    task_ids: list[str] = field(default_factory=list)
    assigned_agent_ids: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "group_id": self.group_id,
            "required_capability": self.required_capability,
            "task_count": len(self.task_ids),
            "task_ids": self.task_ids,
            "assigned_agent_ids": self.assigned_agent_ids,
        }


# ---------------------------------------------------------------------------
# Directing data structures
# ---------------------------------------------------------------------------


@dataclass
class TaskDirective:
    """
    Structured management directive issued for each task assignment.

    This is the observable record of the Directing function — not a
    natural-language prompt but a typed management instruction.
    """

    task_id: str
    agent_id: str
    priority: str
    required_capability: str
    expected_output: str
    retry_limit: int
    retry_attempt: int
    dependency_status: str        # "all_satisfied" | "none_required"
    resource_constraint_note: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "agent_id": self.agent_id,
            "priority": self.priority,
            "required_capability": self.required_capability,
            "expected_output": self.expected_output,
            "retry_limit": self.retry_limit,
            "retry_attempt": self.retry_attempt,
            "dependency_status": self.dependency_status,
            "resource_constraint_note": self.resource_constraint_note,
        }


# ---------------------------------------------------------------------------
# Staffing records
# ---------------------------------------------------------------------------


@dataclass
class StaffingDecision:
    """Record of one staffing action (activation or deactivation)."""

    timestamp: str
    action: str                 # "activate" | "skip_activation"
    agent_id: str | None
    reason: str
    pending_tasks_at_decision: int
    active_agents_at_decision: int
    expected_benefit_ms: float
    resource_cost_ms: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "action": self.action,
            "agent_id": self.agent_id,
            "reason": self.reason,
            "pending_tasks": self.pending_tasks_at_decision,
            "active_agents": self.active_agents_at_decision,
            "expected_benefit_ms": self.expected_benefit_ms,
            "resource_cost_ms": self.resource_cost_ms,
        }


# ---------------------------------------------------------------------------
# Management Report
# ---------------------------------------------------------------------------


@dataclass
class ManagementReport:
    """
    Continuously updated management state — the Reporting function output.

    Consumed by the frontend dashboard and metrics engine.
    """

    # Workload state
    total_tasks: int = 0
    pending_tasks: int = 0
    running_tasks: int = 0
    completed_tasks: int = 0
    failed_tasks: int = 0

    # Agent state
    initial_agent_count: int = 0
    active_agent_count: int = 0
    idle_agent_count: int = 0
    busy_agent_count: int = 0
    dynamically_activated_count: int = 0
    failed_agent_count: int = 0

    # Performance
    tasks_completed_by_agent: dict[str, int] = field(default_factory=dict)
    tasks_failed_by_agent: dict[str, int] = field(default_factory=dict)
    total_retries: int = 0
    task_latencies_ms: list[float] = field(default_factory=list)

    # Management decisions
    plan_created: bool = False
    plan_updates: int = 0
    staffing_decisions: list[StaffingDecision] = field(default_factory=list)
    reassignment_count: int = 0
    replan_count: int = 0
    coordination_events: int = 0
    parallel_group_count: int = 0
    directives_issued: int = 0

    # Failure recovery
    recovery_count: int = 0

    # Budget
    budget_consumed_wall_ms: float = 0.0
    budget_consumed_decisions: int = 0
    budget_consumed_activation_ms: float = 0.0
    budget_warnings: int = 0

    @property
    def avg_task_latency_ms(self) -> float:
        if not self.task_latencies_ms:
            return 0.0
        return sum(self.task_latencies_ms) / len(self.task_latencies_ms)

    def as_dict(self) -> dict[str, Any]:
        return {
            "workload": {
                "total_tasks": self.total_tasks,
                "pending_tasks": self.pending_tasks,
                "running_tasks": self.running_tasks,
                "completed_tasks": self.completed_tasks,
                "failed_tasks": self.failed_tasks,
            },
            "agents": {
                "initial_agent_count": self.initial_agent_count,
                "active_agent_count": self.active_agent_count,
                "idle_agent_count": self.idle_agent_count,
                "busy_agent_count": self.busy_agent_count,
                "dynamically_activated_count": self.dynamically_activated_count,
                "failed_agent_count": self.failed_agent_count,
            },
            "performance": {
                "tasks_completed_by_agent": self.tasks_completed_by_agent,
                "tasks_failed_by_agent": self.tasks_failed_by_agent,
                "total_retries": self.total_retries,
                "avg_task_latency_ms": round(self.avg_task_latency_ms, 2),
            },
            "management": {
                "plan_created": self.plan_created,
                "plan_updates": self.plan_updates,
                "staffing_decisions": len(self.staffing_decisions),
                "reassignment_count": self.reassignment_count,
                "replan_count": self.replan_count,
                "coordination_events": self.coordination_events,
                "parallel_group_count": self.parallel_group_count,
                "directives_issued": self.directives_issued,
                "recovery_count": self.recovery_count,
            },
            "budget": {
                "consumed_wall_ms": round(self.budget_consumed_wall_ms, 2),
                "consumed_decisions": self.budget_consumed_decisions,
                "consumed_activation_ms": round(self.budget_consumed_activation_ms, 2),
                "budget_warnings": self.budget_warnings,
            },
        }


# ---------------------------------------------------------------------------
# Internal task state
# ---------------------------------------------------------------------------


class _TaskState:
    """Mutable wrapper around a Task used during execution."""

    __slots__ = (
        "task", "status", "assigned_agent_id", "retries",
        "start_ms", "completion_ms", "group_id",
    )

    def __init__(self, task: Task) -> None:
        self.task: Task = task
        self.status: str = task.status          # "ready" | "blocked" | "running" | "completed" | "failed"
        self.assigned_agent_id: str | None = None
        self.retries: int = 0
        self.start_ms: float = 0.0
        self.completion_ms: float = 0.0
        self.group_id: str | None = None        # assigned by Organising


# ---------------------------------------------------------------------------
# POSDCORB Manager
# ---------------------------------------------------------------------------


class POSDCORBManager(BaseManager):
    """
    POSDCORB-Based Adaptive Manager.

    Implements all seven POSDCORB functions as explicit mechanisms:

    P  — _plan()         : Pre-execution analysis and ExecutionPlan creation
    O  — _organise()     : Capability-based task grouping
    S  — _staff()        : Dynamic agent activation/management
    D  — _direct()       : Structured TaskDirective emission
    CO — _coordinate()   : Dependency-aware parallel scheduling
    R  — _report()       : Continuous ManagementReport maintenance
    B  — _budget_check() : Resource limit enforcement

    The SimulationEngine calls .run(graph, agents) exactly as it does for
    the Conventional Manager — no engine changes required.
    """

    manager_type: str = "posdcorb"  # type: ignore[override]

    def __init__(
        self,
        rng_seed: int = 42,
        config: POSDCORBConfig | None = None,
        max_wall_ms: float | None = None,
        max_agent_count: int | None = None,
        max_manager_decisions: int | None = None,
    ) -> None:
        self._rng_seed = rng_seed
        self._rng = random.Random(rng_seed)
        cfg = config or POSDCORBConfig()
        # Engine-supplied generic resource limits override the manager defaults
        # without changing the POSDCORB policy itself.
        overrides: dict[str, Any] = {}
        if max_wall_ms is not None:
            overrides["max_wall_ms"] = float(max_wall_ms)
        if max_agent_count is not None:
            overrides["max_agent_count"] = int(max_agent_count)
        if max_manager_decisions is not None:
            overrides["max_manager_decisions"] = int(max_manager_decisions)
        self._cfg = replace(cfg, **overrides) if overrides else cfg

    # ------------------------------------------------------------------
    # Public API — satisfies ManagerProtocol / BaseManager
    # ------------------------------------------------------------------

    def run(
        self,
        graph: TaskGraph,
        agents: list[Agent],
    ) -> ExecutionTrace:
        """
        Execute the TaskGraph using POSDCORB adaptive management.

        The sequence is:
          1. (B) Budget initialisation
          2. (P) Planning
          3. (O) Organising
          4. (S) Initial staffing assessment
          5. Main loop: (CO) Coordinate → (S) Staff → (D) Direct → (R) Report
          6. Finalise trace with ManagementReport
        """
        trace = ExecutionTrace(
            manager_type=self.manager_type,
            graph_id=graph.graph_id,
            seed=graph.seed,
        )

        # Deep-copy agents so we never mutate originals
        all_agents: list[Agent] = [a.model_copy(deep=True) for a in agents]

        # Separate initial pool from dynamic reserve
        initial_pool: list[Agent] = [a for a in all_agents if a.origin == "initial"]
        dynamic_reserve: list[Agent] = [a for a in all_agents if a.origin == "dynamic"]

        # Active pool starts as initial only; dynamic agents added by Staffing
        active_pool: list[Agent] = list(initial_pool)

        # Build internal task states
        task_states: dict[str, _TaskState] = {
            t.id: _TaskState(t) for t in graph.tasks
        }

        # Initialise dependency tracking (forward → remaining predecessors)
        remaining_deps: dict[str, set[str]] = {
            tid: set(ts.task.dependency_ids)
            for tid, ts in task_states.items()
        }
        # Mark initially ready tasks
        for tid, deps in remaining_deps.items():
            if not deps:
                task_states[tid].status = "ready"

        # Initialise ManagementReport
        report = ManagementReport(
            total_tasks=len(task_states),
            initial_agent_count=len(initial_pool),
            active_agent_count=len(active_pool),
        )

        # ----------------------------------------------------------------
        # B — Budget initialisation
        # ----------------------------------------------------------------
        budget_wall_ms: float = self._cfg.max_wall_ms
        # Active agents may never exceed the configured cap nor the pool size.
        if self._cfg.max_agent_count is not None:
            max_agents: int = min(len(all_agents), self._cfg.max_agent_count)
        else:
            max_agents = len(all_agents)    # total pool = upper bound
        wall_ms: float = 0.0

        budget = ResourceBudget(
            max_agents=max_agents,
            max_execution_time_ms=budget_wall_ms,
            max_manager_decisions=self._cfg.max_manager_decisions,
            max_llm_calls=self._cfg.max_llm_calls,
            max_tokens=self._cfg.token_budget,
            agent_activation_cost_ms=self._cfg.agent_activation_cost,
        )

        if self._cfg.budgeting_enabled:
            self._record_event(
                trace, "RESOURCE_CHECK",
                task_id=None, agent_id=None,
                reason=(
                    f"Budget initialised: max_wall_ms={budget_wall_ms:.0f}, "
                    f"max_agents={max_agents}, "
                    f"max_manager_decisions={self._cfg.max_manager_decisions}, "
                    f"initial_pool={len(initial_pool)}, "
                    f"dynamic_reserve={len(dynamic_reserve)}"
                ),
                metadata=budget.as_dict(),
            )

        # ----------------------------------------------------------------
        # P — Planning
        # ----------------------------------------------------------------
        if self._cfg.planning_enabled:
            plan = self._plan(graph, task_states, active_pool, max_agents)
            report.plan_created = True

            self._record_event(
                trace, "PLAN_CREATED",
                task_id=None, agent_id=None,
                reason=(
                    f"ExecutionPlan created: {plan.total_tasks} tasks, "
                    f"{len(plan.parallel_groups)} parallel groups, "
                    f"estimated_total_workload={plan.estimated_total_workload_ms:.0f}ms, "
                    f"initial_pool_sufficient={plan.initial_pool_sufficient}. "
                    + "; ".join(plan.plan_notes)
                ),
                metadata=plan.as_dict(),
            )

        # ----------------------------------------------------------------
        # O — Organising
        # ----------------------------------------------------------------
        task_groups: dict[str, TaskGroup] = {}
        if self._cfg.organizing_enabled:
            task_groups = self._organise(task_states)

            for tg in task_groups.values():
                self._record_event(
                    trace, "TASK_GROUP_CREATED",
                    task_id=None, agent_id=None,
                    reason=(
                        f"TaskGroup '{tg.group_id}' created for capability "
                        f"'{tg.required_capability}' with {len(tg.task_ids)} tasks."
                    ),
                    metadata=tg.as_dict(),
                )

            # Assign group_id onto task states
            for tg in task_groups.values():
                for tid in tg.task_ids:
                    task_states[tid].group_id = tg.group_id

        # Agents quarantined after consecutive failures (failure recovery).
        quarantined: list[Agent] = []
        # Consecutive task-failure streak per agent (failure detection signal).
        agent_failure_streaks: dict[str, int] = {}

        # ----------------------------------------------------------------
        # S — Initial Staffing Assessment
        # ----------------------------------------------------------------
        if self._cfg.staffing_enabled:
            active_pool, dynamic_reserve = self._staff(
                task_states, active_pool, dynamic_reserve,
                budget, wall_ms, report, trace,
            )

        # ----------------------------------------------------------------
        # Main coordination loop
        # ----------------------------------------------------------------
        budget_exhausted: bool = False
        budget_critical_replanned: bool = False

        while not self._all_terminal(task_states):
            if wall_ms >= self._cfg.max_wall_ms:
                if self._cfg.budgeting_enabled:
                    self._record_event(
                        trace, "BUDGET_WARNING",
                        task_id=None, agent_id=None,
                        reason=f"Wall-clock budget exhausted at {wall_ms:.0f}ms. Stopping.",
                        metadata={"wall_ms": wall_ms},
                    )
                    report.budget_warnings += 1
                break

            # B — Replan once when the budget turns critical
            if (
                self._cfg.budgeting_enabled
                and self._cfg.planning_enabled
                and self._cfg.replan_on_budget_critical
                and not budget_critical_replanned
                and wall_ms >= self._cfg.max_wall_ms * self._cfg.budget_critical_threshold
            ):
                budget_critical_replanned = True
                self._trigger_replan(
                    "budget_critical", "", task_states,
                    active_pool, dynamic_reserve, budget.max_agents,
                    wall_ms, report, trace,
                )

            # ----------------------------------------------------------------
            # CO — Coordinating: identify ready tasks
            # ----------------------------------------------------------------
            ready = self._get_ready_tasks(task_states)

            if not ready:
                # Nothing runnable — tick simulated clock
                wall_ms += 10.0
                report.budget_consumed_wall_ms = wall_ms
                continue

            dispatched_any = False

            # Process all ready tasks in this scheduling round
            for task_state in ready:
                task = task_state.task

                # B — Budget check before each decision
                if not self._budget_check(budget, report, trace):
                    budget_exhausted = True
                    break

                eligible = self._eligible_agents(
                    active_pool,
                    required_capability=task.required_capability,
                    origin_filter=None,    # POSDCORB uses both initial AND activated dynamic
                )
                best = self._best_agent(eligible)

                decision_latency = self._sample_latency()
                wall_ms += decision_latency
                report.budget_consumed_wall_ms = wall_ms
                report.budget_consumed_decisions += 1
                budget.consumed_wall_ms = wall_ms
                budget.consumed_decisions = report.budget_consumed_decisions

                if best is None:
                    # CO — Record dependency/availability wait
                    if self._cfg.coordinating_enabled:
                        self._record_event(
                            trace, "DEPENDENCY_WAIT",
                            task_id=task.id, agent_id=None,
                            reason=(
                                f"No agent with capability '{task.required_capability}' "
                                f"currently available. Task queued."
                            ),
                            metadata={
                                "required_capability": task.required_capability,
                                "active_pool_size": len(active_pool),
                            },
                        )
                        report.coordination_events += 1
                    continue

                # D — Directing: emit structured TaskDirective
                dep_status = (
                    "none_required"
                    if not task.dependency_ids
                    else "all_satisfied"
                )
                directive: TaskDirective | None = None
                if self._cfg.directing_enabled:
                    directive = self._direct(task, best, task_state.retries, dep_status)

                    self._record_event(
                        trace, "TASK_DIRECTED",
                        task_id=task.id, agent_id=best.id,
                        reason=(
                            f"Directive issued: agent='{best.name}', "
                            f"priority={directive.priority}, "
                            f"capability={directive.required_capability}, "
                            f"retry_attempt={directive.retry_attempt}/{directive.retry_limit}"
                        ),
                        metadata=directive.as_dict(),
                    )

                # CO — Schedule task
                task_state.status = "running"
                task_state.assigned_agent_id = best.id
                task_state.start_ms = wall_ms
                best.current_workload += 1
                if best.current_workload >= best.max_concurrent_tasks:
                    best.status = "running"
                report.directives_issued += 1

                if self._cfg.coordinating_enabled:
                    self._record_event(
                        trace, "TASK_SCHEDULED",
                        task_id=task.id, agent_id=best.id,
                        reason=(
                            f"Task scheduled on '{best.name}' "
                            f"(reliability={best.reliability:.2f}, speed={best.processing_speed:.2f}×, "
                            f"group='{task_state.group_id}')."
                        ),
                        metadata={
                            "group_id": task_state.group_id,
                            "priority": task.priority,
                            "complexity": task.complexity,
                            "retry_attempt": task_state.retries,
                        },
                    )

                # Also produce a standard assign DecisionRecord for engine compatibility
                assign_rec = DecisionRecord(
                    manager_decision="assign",
                    task_id=task.id,
                    selected_agent_id=best.id,
                    selected_agent_name=best.name,
                    decision_latency_ms=decision_latency,
                    reason=(
                        f"[POSDCORB] Directed '{best.name}' "
                        f"(reliability={best.reliability:.2f}, speed={best.processing_speed:.2f}×) "
                        f"for {task.task_type} task (priority={task.priority}, "
                        f"complexity={task.complexity}, group='{task_state.group_id}')."
                    ),
                    metadata={
                        "retry_attempt": task_state.retries,
                        "candidates_evaluated": len(eligible),
                        "directive": directive.as_dict() if directive else None,
                        "posdcorb_function": (
                            "directing" if self._cfg.directing_enabled else "assignment"
                        ),
                    },
                )
                trace.record(assign_rec)

                # Execute via the same deterministic worker used by Conventional Manager
                if self._cfg.coordinating_enabled:
                    self._record_event(
                        trace, "TASK_STARTED",
                        task_id=task.id, agent_id=best.id,
                        reason=(
                            f"Execution started on '{best.name}' "
                            f"(estimated={task.estimated_workload_ms}ms)."
                        ),
                        metadata={
                            "estimated_workload_ms": task.estimated_workload_ms,
                            "start_wall_ms": round(wall_ms, 2),
                        },
                    )

                worker_result = execute_task(best, task)
                wall_ms += worker_result.elapsed_ms
                task_state.completion_ms = wall_ms
                best.current_workload -= 1
                if best.current_workload == 0:
                    best.status = "idle"

                report.budget_consumed_wall_ms = wall_ms
                budget.consumed_wall_ms = wall_ms

                # P/CO — Adaptive replan when a task overruns its estimate
                if (
                    self._cfg.adaptive_mechanisms
                    and self._cfg.replan_slow_task_multiplier > 0
                    and worker_result.elapsed_ms
                    > task.estimated_workload_ms * self._cfg.replan_slow_task_multiplier
                ):
                    self._trigger_replan(
                        "slow_task", task.id, task_states,
                        active_pool, dynamic_reserve, budget.max_agents,
                        wall_ms, report, trace,
                    )

                if worker_result.success:
                    task_state.status = "completed"
                    best.tasks_completed += 1
                    # A successful task resets the agent's failure streak.
                    agent_failure_streaks[best.id] = 0
                    report.completed_tasks += 1
                    report.task_latencies_ms.append(
                        task_state.completion_ms - task_state.start_ms
                    )
                    report.tasks_completed_by_agent[best.id] = (
                        report.tasks_completed_by_agent.get(best.id, 0) + 1
                    )

                    # CO — Unblock dependent tasks
                    unblocked = self._unblock_dependents(
                        task.id, task_states, remaining_deps
                    )
                    if unblocked and self._cfg.coordinating_enabled:
                        report.coordination_events += 1

                    if self._cfg.coordinating_enabled:
                        self._record_event(
                            trace, "TASK_COMPLETED",
                            task_id=task.id, agent_id=best.id,
                            reason=(
                                f"Task completed in {worker_result.elapsed_ms:.0f}ms "
                                f"(estimated={task.estimated_workload_ms}ms). "
                                f"Unblocked dependents: {unblocked}."
                            ),
                            metadata={
                                "elapsed_ms": worker_result.elapsed_ms,
                                "estimated_workload_ms": task.estimated_workload_ms,
                                "output_summary": worker_result.output_summary,
                                "unblocked_task_ids": unblocked,
                            },
                        )

                    result_rec = DecisionRecord(
                        manager_decision="evaluate",
                        task_id=task.id,
                        selected_agent_id=best.id,
                        selected_agent_name=best.name,
                        decision_latency_ms=self._sample_latency(),
                        reason=f"[POSDCORB] Task completed: {worker_result.output_summary}",
                        result="success",
                        success=True,
                        metadata={
                            "elapsed_ms": worker_result.elapsed_ms,
                            "output_summary": worker_result.output_summary,
                            "posdcorb_function": "coordinating",
                        },
                    )
                    trace.record(result_rec)
                    dispatched_any = True

                else:
                    # ----------------------------------------------------------------
                    # FAILURE DETECTION (R) — track the agent's failure streak
                    # ----------------------------------------------------------------
                    best.tasks_failed += 1
                    task_state.retries += 1
                    report.total_retries += 1
                    report.tasks_failed_by_agent[best.id] = (
                        report.tasks_failed_by_agent.get(best.id, 0) + 1
                    )
                    agent_failure_streaks[best.id] = (
                        agent_failure_streaks.get(best.id, 0) + 1
                    )

                    if task_state.retries <= self._cfg.max_retries:
                        # Reset to ready for reassignment
                        task_state.status = "ready"
                        task_state.assigned_agent_id = None

                        # R — Report failure; CO — Record reassignment
                        if self._cfg.coordinating_enabled:
                            self._record_event(
                                trace, "TASK_REASSIGNED",
                                task_id=task.id, agent_id=best.id,
                                reason=(
                                    f"Task failed on '{best.name}' "
                                    f"(attempt {task_state.retries}/{self._cfg.max_retries}). "
                                    f"Queuing for reassignment. "
                                    f"Error: {worker_result.output_summary}"
                                ),
                                metadata={
                                    "retry_attempt": task_state.retries,
                                    "failed_agent_id": best.id,
                                },
                            )
                            report.reassignment_count += 1

                        retry_rec = DecisionRecord(
                            manager_decision="retry",
                            task_id=task.id,
                            selected_agent_id=best.id,
                            selected_agent_name=best.name,
                            decision_latency_ms=self._sample_latency(),
                            reason=(
                                f"[POSDCORB] Task failed (attempt "
                                f"{task_state.retries}/{self._cfg.max_retries}): "
                                f"{worker_result.output_summary}. Recovery scheduled."
                            ),
                            result="failure",
                            success=False,
                            metadata={
                                "retry_attempt": task_state.retries,
                                "elapsed_ms": worker_result.elapsed_ms,
                                "posdcorb_function": "coordinating+reporting",
                            },
                        )
                        trace.record(retry_rec)

                        # ------------------------------------------------------------
                        # AGENT-FAILURE DETECTION → RECOVERY CHAIN
                        # (R detects → CO interrupts → S finds replacement →
                        #  D re-directs → CO reschedules)
                        # ------------------------------------------------------------
                        if (
                            self._cfg.adaptive_mechanisms
                            and self._cfg.replan_on_agent_failure
                            and "staffing" in self._cfg.enabled_principles
                            and agent_failure_streaks[best.id]
                            >= self._cfg.agent_failure_streak_threshold
                        ):
                            active_pool, dynamic_reserve = self._recover_from_agent_failure(
                                failed_agent=best,
                                interrupted_task_id=task.id,
                                task_states=task_states,
                                active_pool=active_pool,
                                dynamic_reserve=dynamic_reserve,
                                quarantined=quarantined,
                                budget=budget,
                                wall_ms=wall_ms,
                                report=report,
                                trace=trace,
                            )

                        # Trigger adaptive replanning if configured
                        elif (
                            self._cfg.adaptive_mechanisms
                            and self._cfg.replan_on_task_failure
                        ):
                            self._trigger_replan(
                                "task_failure", task.id, task_states,
                                active_pool, dynamic_reserve, budget.max_agents,
                                wall_ms, report, trace,
                            )

                    else:
                        # Permanently failed
                        task_state.status = "failed"
                        report.failed_tasks += 1

                        # CO — Cascade failure to dependents
                        cascaded = self._cascade_failure(
                            task.id, task_states, remaining_deps
                        )

                        self._record_event(
                            trace, "TASK_FAILED",
                            task_id=task.id, agent_id=best.id,
                            reason=(
                                f"Task permanently failed after "
                                f"{self._cfg.max_retries} retries. "
                                f"Cascaded failure to: {cascaded}. "
                                f"Last error: {worker_result.output_summary}"
                            ),
                            metadata={
                                "max_retries_reached": True,
                                "cascaded_task_ids": cascaded,
                            },
                        )

                        fail_rec = DecisionRecord(
                            manager_decision="fail",
                            task_id=task.id,
                            selected_agent_id=best.id,
                            selected_agent_name=best.name,
                            decision_latency_ms=self._sample_latency(),
                            reason=(
                                f"[POSDCORB] Task permanently failed after "
                                f"{self._cfg.max_retries} retries. "
                                f"Last error: {worker_result.output_summary}"
                            ),
                            result="failure",
                            success=False,
                            metadata={"max_retries_reached": True},
                        )
                        trace.record(fail_rec)

                    dispatched_any = True

            if budget_exhausted:
                break

            # ----------------------------------------------------------------
            # CO — Deadlock detection: fail fast when no agent in the WHOLE
            # pool (active + reserve, minus quarantined) can ever serve a
            # ready task's capability.  Avoids spinning until wall budget.
            # ----------------------------------------------------------------
            if (
                self._cfg.adaptive_mechanisms
                and self._cfg.coordinating_enabled
                and self._cfg.deadlock_detection
                and ready
            ):
                quarantined_ids = {a.id for a in quarantined}
                for task_state in ready:
                    if task_state.status != "ready":
                        continue
                    cap = task_state.task.required_capability
                    servable = any(
                        cap in a.capabilities
                        for a in (active_pool + dynamic_reserve)
                        if a.id not in quarantined_ids
                    )
                    if servable:
                        continue
                    task_state.status = "failed"
                    report.failed_tasks += 1
                    cascaded = self._cascade_failure(
                        task_state.task.id, task_states, remaining_deps
                    )
                    self._record_event(
                        trace, "TASK_FAILED",
                        task_id=task_state.task.id, agent_id=None,
                        reason=(
                            f"Deadlock detected: no agent in the pool "
                            f"(active={len(active_pool)}, reserve={len(dynamic_reserve)}, "
                            f"quarantined={len(quarantined)}) can serve capability "
                            f"'{cap}'. Task failed fast instead of waiting for budget."
                        ),
                        metadata={
                            "deadlock": True,
                            "required_capability": cap,
                            "cascaded_task_ids": cascaded,
                        },
                    )
                    fail_rec = DecisionRecord(
                        manager_decision="fail",
                        task_id=task_state.task.id,
                        selected_agent_id=None,
                        selected_agent_name=None,
                        decision_latency_ms=self._sample_latency(),
                        reason=(
                            f"[POSDCORB] Task permanently failed (deadlock): no "
                            f"available agent can serve capability '{cap}'."
                        ),
                        result="failure",
                        success=False,
                        metadata={
                            "deadlock": True,
                            "required_capability": cap,
                        },
                    )
                    trace.record(fail_rec)

            # ----------------------------------------------------------------
            # S — Dynamic staffing re-evaluation each scheduling round
            # ----------------------------------------------------------------
            if self._cfg.staffing_enabled:
                active_pool, dynamic_reserve = self._staff(
                    task_states, active_pool, dynamic_reserve,
                    budget, wall_ms, report, trace,
                )

            # ----------------------------------------------------------------
            # R — Reporting: update live status
            # ----------------------------------------------------------------
            self._update_report(
                report, task_states, active_pool, wall_ms, trace,
                quarantined=quarantined,
            )

            if not dispatched_any:
                # All ready tasks waiting for agents → tick clock
                wall_ms += 10.0
                report.budget_consumed_wall_ms = wall_ms

        # ----------------------------------------------------------------
        # Finalise
        # ----------------------------------------------------------------
        report.budget_consumed_wall_ms = wall_ms
        self._finalise_trace(trace, task_states, wall_ms, report)
        trace.budget_decisions_used = report.budget_consumed_decisions
        return trace

    # ==================================================================
    # P — PLANNING
    # ==================================================================

    def _plan(
        self,
        graph: TaskGraph,
        task_states: dict[str, _TaskState],
        active_pool: list[Agent],
        max_agents: int,
    ) -> ExecutionPlan:
        """
        Analyse the complete workload and build an ExecutionPlan.

        Steps:
        1. Identify all tasks and their dependencies.
        2. Build a topological ordering.
        3. Determine which tasks can execute in parallel (parallel groups).
        4. Estimate task complexity/workload.
        5. Identify task priorities.
        6. Identify potential bottlenecks (critical path).
        7. Estimate whether the initial worker pool is sufficient.
        """
        # Build reverse dependency map: task_id → predecessors
        reverse_deps: dict[str, list[str]] = {t.id: [] for t in graph.tasks}
        for t in graph.tasks:
            for dep_id in t.dependency_ids:
                reverse_deps[t.id].append(dep_id)

        # Build forward edges map: task_id → successors
        fwd_edges: dict[str, list[str]] = {t.id: [] for t in graph.tasks}
        for t in graph.tasks:
            for dep_id in t.dependency_ids:
                fwd_edges.setdefault(dep_id, []).append(t.id)

        # Topological sort using Kahn's algorithm (deterministic order)
        in_degree: dict[str, int] = {t.id: len(t.dependency_ids) for t in graph.tasks}
        topo_queue: list[str] = sorted(
            [tid for tid, deg in in_degree.items() if deg == 0],
            key=lambda tid: (
                PRIORITY_ORDER.get(task_states[tid].task.priority, 99),
                task_states[tid].task.index,
            ),
        )
        topo_order: list[str] = []
        remaining_in: dict[str, int] = dict(in_degree)
        q = list(topo_queue)
        while q:
            tid = q.pop(0)
            topo_order.append(tid)
            for succ in sorted(fwd_edges.get(tid, [])):
                remaining_in[succ] -= 1
                if remaining_in[succ] == 0:
                    q.append(succ)
                    q.sort(
                        key=lambda x: (
                            PRIORITY_ORDER.get(task_states[x].task.priority, 99),
                            task_states[x].task.index,
                        )
                    )

        # Build parallel groups: tasks that can run simultaneously
        # (tasks with the same topological level)
        level: dict[str, int] = {}
        for tid in topo_order:
            preds = reverse_deps.get(tid, [])
            level[tid] = max((level.get(p, 0) + 1 for p in preds), default=0)

        levels_to_tasks: dict[int, list[str]] = {}
        for tid, lv in level.items():
            levels_to_tasks.setdefault(lv, []).append(tid)

        parallel_groups: list[list[str]] = [
            sorted(
                tasks,
                key=lambda x: (
                    PRIORITY_ORDER.get(task_states[x].task.priority, 99),
                    task_states[x].task.index,
                ),
            )
            for lv, tasks in sorted(levels_to_tasks.items())
        ]

        # Priority ordering (global)
        priority_order: list[str] = sorted(
            task_states.keys(),
            key=lambda tid: (
                PRIORITY_ORDER.get(task_states[tid].task.priority, 99),
                task_states[tid].task.index,
            ),
        )

        # Estimated total workload
        estimated_total_ms = sum(
            ts.task.estimated_workload_ms for ts in task_states.values()
        )

        # Critical path: tasks that appear in every sequential chain
        # Approximate: tasks with both many successors AND many predecessors
        bottleneck_ids: list[str] = []
        for tid, ts in task_states.items():
            successor_count = len(fwd_edges.get(tid, []))
            predecessor_count = len(reverse_deps.get(tid, []))
            if successor_count >= 2 and predecessor_count >= 1:
                bottleneck_ids.append(tid)
            elif ts.task.complexity == "high" and successor_count >= 1:
                bottleneck_ids.append(tid)

        # Required capabilities
        required_capabilities = list({
            ts.task.required_capability for ts in task_states.values()
        })

        # Max parallel group size → minimum agent count needed
        max_parallelism = max((len(g) for g in parallel_groups), default=1)
        initial_pool_sufficient = len(active_pool) >= min(max_parallelism, max_agents)

        plan_notes: list[str] = []
        plan_notes.append(
            f"Max concurrency level: {max_parallelism} tasks in one group."
        )
        plan_notes.append(
            f"Topological depth: {len(parallel_groups)} sequential levels."
        )
        if bottleneck_ids:
            plan_notes.append(
                f"Potential bottlenecks detected: {len(bottleneck_ids)} tasks."
            )
        if not initial_pool_sufficient:
            plan_notes.append(
                f"Initial pool ({len(active_pool)} agents) may be insufficient "
                f"for peak parallelism ({max_parallelism} concurrent tasks). "
                f"Staffing may activate additional agents."
            )
        else:
            plan_notes.append(
                f"Initial pool ({len(active_pool)} agents) appears sufficient."
            )

        return ExecutionPlan(
            graph_id=graph.graph_id,
            total_tasks=len(task_states),
            dependency_edges=fwd_edges,
            reverse_deps=reverse_deps,
            parallel_groups=parallel_groups,
            priority_order=priority_order,
            estimated_total_workload_ms=float(estimated_total_ms),
            estimated_bottleneck_task_ids=bottleneck_ids,
            required_capabilities=required_capabilities,
            initial_pool_sufficient=initial_pool_sufficient,
            plan_notes=plan_notes,
        )

    # ==================================================================
    # O — ORGANISING
    # ==================================================================

    def _organise(
        self, task_states: dict[str, _TaskState]
    ) -> dict[str, TaskGroup]:
        """
        Group tasks by required capability into TaskGroups.

        The organisational structure:
            Manager
              ├── TaskGroup(capability=text-processing)   → [t1, t2, t5]
              └── TaskGroup(capability=statistics)        → [t3, t4]

        Groups are sorted deterministically by capability name.
        """
        groups: dict[str, TaskGroup] = {}
        for ts in task_states.values():
            cap = ts.task.required_capability
            if cap not in groups:
                gid = f"group-{cap}"
                groups[cap] = TaskGroup(
                    group_id=gid,
                    required_capability=cap,
                )
            groups[cap].task_ids.append(ts.task.id)

        # Sort task_ids within each group by priority then index
        for tg in groups.values():
            tg.task_ids.sort(
                key=lambda tid: (
                    PRIORITY_ORDER.get(task_states[tid].task.priority, 99),
                    task_states[tid].task.index,
                )
            )

        return groups

    # ==================================================================
    # S — STAFFING
    # ==================================================================

    def _staff(
        self,
        task_states: dict[str, _TaskState],
        active_pool: list[Agent],
        dynamic_reserve: list[Agent],
        budget: ResourceBudget,
        wall_ms: float,
        report: ManagementReport,
        trace: ExecutionTrace,
    ) -> tuple[list[Agent], list[Agent]]:
        """
        Evaluate whether to activate additional workers from the dynamic reserve.

        Policy (threshold-based, all thresholds configurable):
        --------------------------------------------------------
        Activate an agent if ALL of the following hold:
        1. There are pending parallel tasks > idle agent count
           (idle agents = active agents not at capacity)
        2. active_count < budget.max_agents   (never exceeds the cap)
        3. estimated_benefit_ms > activation_cost_ms
        4. resource budget allows the activation (B — Budgeting)

        Do NOT simply maximise agents — activation has a cost.
        The manager may decide NOT to activate even when it could.
        """
        if not dynamic_reserve:
            return active_pool, dynamic_reserve

        # Count pending (ready but not running) tasks
        pending_parallel = sum(
            1 for ts in task_states.values() if ts.status == "ready"
        )
        idle_agents = sum(
            1 for a in active_pool
            if a.current_workload < a.max_concurrent_tasks
        )
        active_count = len(active_pool)

        # Utilisation: fraction of agent capacity currently in use
        total_capacity = sum(a.max_concurrent_tasks for a in active_pool) or 1
        used_capacity = sum(a.current_workload for a in active_pool)
        utilisation = used_capacity / total_capacity

        # Estimate benefit: how much wall time could be saved by one extra agent?
        avg_task_ms = (
            sum(ts.task.estimated_workload_ms for ts in task_states.values()
                if ts.status in ("ready", "blocked"))
            / max(pending_parallel, 1)
        )
        # Benefit = average time saved by parallelising one more agent
        benefit_ms = avg_task_ms / max(active_count, 1)

        # B — Budget gate: refuses when the cap or wall budget is reached.
        # When Budgeting is disabled the manager still respects the generic
        # active-agent cap supplied by the engine, but it does not consult the
        # resource budget before activating.
        budget_allows = (
            budget.can_activate() if self._cfg.budgeting_enabled else True
        )

        should_activate = (
            pending_parallel > idle_agents
            and active_count < budget.max_agents
            and benefit_ms > self._cfg.agent_activation_cost
            and pending_parallel >= self._cfg.activation_pending_threshold
            and wall_ms < self._cfg.max_wall_ms * 0.95   # don't activate near end
            and budget_allows
        )

        timestamp = datetime.now(timezone.utc).isoformat()

        if should_activate:
            # Pick the best dynamic agent for the most-needed capability
            most_needed_cap = self._most_needed_capability(task_states, active_pool)
            candidate = self._pick_dynamic_agent(
                dynamic_reserve, most_needed_cap
            )

            if candidate is not None:
                # Activate
                dynamic_reserve = [a for a in dynamic_reserve if a.id != candidate.id]
                candidate.origin = "dynamic"    # mark as dynamically activated
                active_pool = list(active_pool) + [candidate]
                report.dynamically_activated_count += 1
                report.active_agent_count = len(active_pool)
                # B — charge the activation cost against the budget
                budget.charge_activation()
                report.budget_consumed_activation_ms = budget.consumed_activation_ms

                staffing_rec = StaffingDecision(
                    timestamp=timestamp,
                    action="activate",
                    agent_id=candidate.id,
                    reason=(
                        f"Activated '{candidate.name}': "
                        f"pending={pending_parallel}, idle_agents={idle_agents}, "
                        f"utilisation={utilisation:.0%}, "
                        f"benefit={benefit_ms:.0f}ms > cost={self._cfg.agent_activation_cost:.0f}ms, "
                        f"capability_needed='{most_needed_cap}', "
                        f"active_agents={active_count + 1}/{budget.max_agents}"
                    ),
                    pending_tasks_at_decision=pending_parallel,
                    active_agents_at_decision=active_count,
                    expected_benefit_ms=round(benefit_ms, 2),
                    resource_cost_ms=self._cfg.agent_activation_cost,
                )
                report.staffing_decisions.append(staffing_rec)

                self._record_event(
                    trace, "AGENT_ACTIVATED",
                    task_id=None, agent_id=candidate.id,
                    reason=staffing_rec.reason,
                    metadata=staffing_rec.as_dict(),
                )
            else:
                # No suitable dynamic agent for the needed capability
                staffing_rec = StaffingDecision(
                    timestamp=timestamp,
                    action="skip_activation",
                    agent_id=None,
                    reason=(
                        f"Activation desired but no dynamic agent has capability "
                        f"'{most_needed_cap}'. Reserve: {[a.id for a in dynamic_reserve]}"
                    ),
                    pending_tasks_at_decision=pending_parallel,
                    active_agents_at_decision=active_count,
                    expected_benefit_ms=round(benefit_ms, 2),
                    resource_cost_ms=self._cfg.agent_activation_cost,
                )
                report.staffing_decisions.append(staffing_rec)

        else:
            # Do not activate — record the decision for research transparency
            if dynamic_reserve:  # Only record if there were candidates to consider
                if not budget_allows:
                    skip_reason = (
                        f"Activation refused by budget (B): "
                        f"active={active_count}/{budget.max_agents}, "
                        f"wall={budget.wall_fraction_used:.0%} of budget, "
                        f"activations_charged={budget.consumed_activation_ms:.0f}ms."
                    )
                else:
                    skip_reason = (
                        f"Staffing evaluated: activation not justified. "
                        f"pending={pending_parallel}, idle={idle_agents}, "
                        f"utilisation={utilisation:.0%}, "
                        f"benefit={benefit_ms:.0f}ms <= cost={self._cfg.agent_activation_cost:.0f}ms "
                        f"OR pending < threshold ({self._cfg.activation_pending_threshold})"
                    )
                staffing_rec = StaffingDecision(
                    timestamp=timestamp,
                    action="skip_activation",
                    agent_id=None,
                    reason=skip_reason,
                    pending_tasks_at_decision=pending_parallel,
                    active_agents_at_decision=active_count,
                    expected_benefit_ms=round(benefit_ms, 2),
                    resource_cost_ms=self._cfg.agent_activation_cost,
                )
                report.staffing_decisions.append(staffing_rec)

        return active_pool, dynamic_reserve

    def _most_needed_capability(
        self,
        task_states: dict[str, _TaskState],
        active_pool: list[Agent],
    ) -> str:
        """
        Return the capability that has the most pending tasks relative to
        how many agents in the active pool can serve it.
        """
        cap_demand: dict[str, int] = {}
        for ts in task_states.values():
            if ts.status in ("ready", "blocked"):
                cap = ts.task.required_capability
                cap_demand[cap] = cap_demand.get(cap, 0) + 1

        cap_supply: dict[str, int] = {}
        for a in active_pool:
            for cap in a.capabilities:
                cap_supply[cap] = cap_supply.get(cap, 0) + 1

        # Ratio of demand to supply; pick highest ratio
        best_cap = max(
            cap_demand,
            key=lambda c: cap_demand[c] / max(cap_supply.get(c, 0), 0.1),
            default="text-processing",
        )
        return best_cap

    def _pick_dynamic_agent(
        self,
        dynamic_reserve: list[Agent],
        preferred_capability: str,
    ) -> Agent | None:
        """
        Select the best dynamic agent from the reserve, preferring one
        that has the needed capability.
        Agents are selected deterministically (sorted by id, scored by capability match).
        """
        if not dynamic_reserve:
            return None

        # Sort by: capability match (True > False) then reliability × speed
        def score(a: Agent) -> tuple[int, float]:
            has_cap = int(preferred_capability in a.capabilities)
            perf = a.reliability * a.processing_speed
            return (has_cap, perf)

        return max(dynamic_reserve, key=score)

    # ==================================================================
    # FAILURE RECOVERY  (R → CO → S → D → CO chain)
    # ==================================================================

    def _recover_from_agent_failure(
        self,
        failed_agent: Agent,
        interrupted_task_id: str,
        task_states: dict[str, _TaskState],
        active_pool: list[Agent],
        dynamic_reserve: list[Agent],
        quarantined: list[Agent],
        budget: ResourceBudget,
        wall_ms: float,
        report: ManagementReport,
        trace: ExecutionTrace,
    ) -> tuple[list[Agent], list[Agent]]:
        """
        Full failure-recovery chain, triggered when an agent's consecutive
        task-failure streak reaches ``agent_failure_streak_threshold``:

        1. R (Reporting)  — the streak signal already detected the failure.
        2. CO (Coordinating) — the interrupted task is marked (caller reset
           it to 'ready'); the failed agent is quarantined.
        3. S (Staffing)   — search for a replacement: first an idle capable
           agent in the active pool, else a reserve agent (activated via the
           normal budgeted activation, or directly if budget allows).
        4. D (Directing)  — issue a new TaskDirective to the replacement.
        5. CO (Coordinating) — the task stays 'ready' and is rescheduled by
           the normal loop; the retry counter was already incremented.

        If NO suitable replacement exists the failure is recorded, the
        simulation continues with other independent tasks, and replanning
        is triggered.  The simulation never crashes on a worker failure.
        """
        report.recovery_count += 1
        interrupted_task = task_states.get(interrupted_task_id)

        # --- 2. CO — quarantine the failed agent -------------------------
        if failed_agent in active_pool:
            active_pool = [a for a in active_pool if a.id != failed_agent.id]
        failed_agent.status = "failed"
        quarantined.append(failed_agent)
        report.failed_agent_count = len(quarantined)
        report.active_agent_count = len(active_pool)

        self._record_event(
            trace, "AGENT_FAILED",
            task_id=interrupted_task_id, agent_id=failed_agent.id,
            reason=(
                f"Agent '{failed_agent.name}' failed "
                f"{self._cfg.agent_failure_streak_threshold} consecutive tasks. "
                f"Quarantined; interrupted task '{interrupted_task_id}'."
            ),
            metadata={
                "failure_streak": self._cfg.agent_failure_streak_threshold,
                "agent_tasks_failed": failed_agent.tasks_failed,
                "quarantined_count": len(quarantined),
            },
        )

        self._record_event(
            trace, "RECOVERY_STARTED",
            task_id=interrupted_task_id, agent_id=failed_agent.id,
            reason=(
                f"Recovery started for task '{interrupted_task_id}' after "
                f"failure of '{failed_agent.name}'. "
                f"Searching active pool + reserve for a replacement."
            ),
            metadata={
                "active_pool_size": len(active_pool),
                "reserve_size": len(dynamic_reserve),
            },
        )

        # --- 3. S — find a replacement -----------------------------------
        required_cap = (
            interrupted_task.task.required_capability
            if interrupted_task is not None
            else None
        )
        replacement: Agent | None = None

        # (a) idle capable agent already in the active pool
        pool_candidates = self._eligible_agents(
            active_pool,
            required_capability=required_cap or "",
            origin_filter=None,
        )
        replacement = self._best_agent(pool_candidates)

        # (b) reserve agent with the capability — activate it (budgeted)
        if replacement is None and dynamic_reserve:
            candidate = self._pick_dynamic_agent(dynamic_reserve, required_cap or "")
            if (
                candidate is not None
                and len(active_pool) < budget.max_agents
                and budget.can_activate()
            ):
                dynamic_reserve = [
                    a for a in dynamic_reserve if a.id != candidate.id
                ]
                candidate.origin = "dynamic"
                active_pool = list(active_pool) + [candidate]
                replacement = candidate
                report.dynamically_activated_count += 1
                budget.charge_activation()
                report.budget_consumed_activation_ms = budget.consumed_activation_ms

                self._record_event(
                    trace, "AGENT_ACTIVATED",
                    task_id=interrupted_task_id, agent_id=candidate.id,
                    reason=(
                        f"Recovery activation: '{candidate.name}' activated to "
                        f"replace quarantined '{failed_agent.name}' for capability "
                        f"'{required_cap}'."
                    ),
                    metadata={
                        "reason_code": "failure_recovery",
                        "replaces_agent_id": failed_agent.id,
                        "active_agents": len(active_pool),
                        "max_agents": budget.max_agents,
                    },
                )

        # --- 4/5. D + CO — re-direct and reschedule, or record failure ----
        if replacement is not None:
            self._record_event(
                trace, "AGENT_REASSIGNED",
                task_id=interrupted_task_id, agent_id=replacement.id,
                reason=(
                    f"Task '{interrupted_task_id}' reassigned from quarantined "
                    f"'{failed_agent.name}' to '{replacement.name}' "
                    f"(reliability={replacement.reliability:.2f}, "
                    f"speed={replacement.processing_speed:.2f}×)."
                ),
                metadata={
                    "from_agent_id": failed_agent.id,
                    "to_agent_id": replacement.id,
                    "retry_attempt": (
                        interrupted_task.retries if interrupted_task else None
                    ),
                },
            )
            report.reassignment_count += 1

            # D — fresh directive for the replacement worker
            if interrupted_task is not None:
                directive = self._direct(
                    interrupted_task.task,
                    replacement,
                    interrupted_task.retries,
                    "all_satisfied" if interrupted_task.task.dependency_ids
                    else "none_required",
                )
                self._record_event(
                    trace, "TASK_DIRECTED",
                    task_id=interrupted_task_id, agent_id=replacement.id,
                    reason=(
                        f"Recovery directive issued to '{replacement.name}' for "
                        f"interrupted task '{interrupted_task_id}' "
                        f"(attempt {interrupted_task.retries}/{self._cfg.max_retries})."
                    ),
                    metadata={**directive.as_dict(), "recovery": True},
                )

            self._record_event(
                trace, "RECOVERY_COMPLETED",
                task_id=interrupted_task_id, agent_id=replacement.id,
                reason=(
                    f"Recovery completed: task '{interrupted_task_id}' rescheduled "
                    f"on '{replacement.name}'. Simulating continues."
                ),
                metadata={
                    "replacement_agent_id": replacement.id,
                    "quarantined_agent_ids": [a.id for a in quarantined],
                },
            )
        else:
            # No suitable worker exists — record the failure and continue
            # with other independent tasks; allow replanning to adjust.
            self._record_event(
                trace, "RECOVERY_COMPLETED",
                task_id=interrupted_task_id, agent_id=failed_agent.id,
                reason=(
                    f"Recovery FAILED: no capable replacement for "
                    f"'{failed_agent.name}' (capability='{required_cap}'). "
                    f"Task '{interrupted_task_id}' left queued; other independent "
                    f"tasks continue. Replanning triggered."
                ),
                metadata={
                    "replacement_found": False,
                    "quarantined_agent_ids": [a.id for a in quarantined],
                },
            )
            if self._cfg.replan_on_agent_failure:
                self._trigger_replan(
                    "agent_failure", interrupted_task_id, task_states,
                    active_pool, dynamic_reserve, budget.max_agents,
                    wall_ms, report, trace,
                )

        return active_pool, dynamic_reserve

    # ==================================================================
    # D — DIRECTING
    # ==================================================================

    def _direct(
        self,
        task: Task,
        agent: Agent,
        retry_attempt: int,
        dependency_status: str,
    ) -> TaskDirective:
        """
        Create a structured TaskDirective for an assignment.

        The directive is an observable management artefact — not a
        natural-language prompt — that makes the directing action traceable.
        """
        expected_output = (
            f"{task.task_type} result for task {task.id} "
            f"(complexity={task.complexity})"
        )
        resource_note = (
            f"Agent capacity: {agent.current_workload + 1}/{agent.max_concurrent_tasks}. "
            f"Reliability: {agent.reliability:.0%}."
        )
        return TaskDirective(
            task_id=task.id,
            agent_id=agent.id,
            priority=task.priority,
            required_capability=task.required_capability,
            expected_output=expected_output,
            retry_limit=self._cfg.max_retries,
            retry_attempt=retry_attempt,
            dependency_status=dependency_status,
            resource_constraint_note=resource_note,
        )

    # ==================================================================
    # CO — COORDINATING
    # ==================================================================

    @staticmethod
    def _unblock_dependents(
        completed_task_id: str,
        task_states: dict[str, _TaskState],
        remaining_deps: dict[str, set[str]],
    ) -> list[str]:
        """Remove completed task from pending deps; return list of newly unblocked tasks."""
        unblocked: list[str] = []
        for dep_tid, dep_set in remaining_deps.items():
            dep_set.discard(completed_task_id)
            if not dep_set and task_states[dep_tid].status == "blocked":
                task_states[dep_tid].status = "ready"
                unblocked.append(dep_tid)
        return unblocked

    @staticmethod
    def _cascade_failure(
        failed_task_id: str,
        task_states: dict[str, _TaskState],
        remaining_deps: dict[str, set[str]],
    ) -> list[str]:
        """Cascade permanent failure to tasks that depended on this task."""
        cascaded: list[str] = []
        for dep_tid, dep_set in remaining_deps.items():
            dep_set.discard(failed_task_id)
            if not dep_set and task_states[dep_tid].status == "blocked":
                task_states[dep_tid].status = "failed"
                cascaded.append(dep_tid)
        return cascaded

    @staticmethod
    def _get_ready_tasks(task_states: dict[str, _TaskState]) -> list[_TaskState]:
        """Return tasks ready to execute, sorted by priority then index."""
        ready = [ts for ts in task_states.values() if ts.status == "ready"]
        return sorted(
            ready,
            key=lambda ts: (
                PRIORITY_ORDER.get(ts.task.priority, 99),
                ts.task.index,
            ),
        )

    @staticmethod
    def _all_terminal(task_states: dict[str, _TaskState]) -> bool:
        return all(
            ts.status in ("completed", "failed", "skipped")
            for ts in task_states.values()
        )

    # ==================================================================
    # R — REPORTING
    # ==================================================================

    def _update_report(
        self,
        report: ManagementReport,
        task_states: dict[str, _TaskState],
        active_pool: list[Agent],
        wall_ms: float,
        trace: ExecutionTrace,
        quarantined: list[Agent] | None = None,
    ) -> None:
        """Update the ManagementReport with current state snapshot."""
        report.pending_tasks = sum(
            1 for ts in task_states.values() if ts.status in ("ready", "blocked")
        )
        report.running_tasks = sum(
            1 for ts in task_states.values() if ts.status == "running"
        )
        report.completed_tasks = sum(
            1 for ts in task_states.values() if ts.status == "completed"
        )
        report.failed_tasks = sum(
            1 for ts in task_states.values() if ts.status == "failed"
        )
        report.active_agent_count = len(active_pool)
        report.idle_agent_count = sum(
            1 for a in active_pool if a.current_workload == 0
        )
        report.busy_agent_count = sum(
            1 for a in active_pool if a.current_workload > 0
        )
        if quarantined is not None:
            report.failed_agent_count = len(quarantined)
        report.budget_consumed_wall_ms = wall_ms

        if self._cfg.reporting_enabled:
            self._record_event(
                trace, "STATUS_UPDATED",
                task_id=None, agent_id=None,
                reason=(
                    f"Status: completed={report.completed_tasks}, "
                    f"pending={report.pending_tasks}, "
                    f"failed={report.failed_tasks}, "
                    f"active_agents={report.active_agent_count}, "
                    f"wall_ms={wall_ms:.0f}"
                ),
                metadata=report.as_dict(),
            )

    # ==================================================================
    # B — BUDGETING
    # ==================================================================

    def _budget_check(
        self,
        budget: ResourceBudget,
        report: ManagementReport,
        trace: ExecutionTrace,
    ) -> bool:
        """
        B — Budgeting gate.

        Returns True if we are within budget; False if we should stop.
        Enforces BOTH the wall-clock limit and the manager-decision cap.
        Records a BUDGET_WARNING event when a limit is hit or turns critical.

        When the Budgeting principle is disabled this gate is a no-op; the
        simulation loop's wall-clock safety valve still prevents runaway runs.
        """
        if not self._cfg.budgeting_enabled:
            return True

        wall_ms = budget.consumed_wall_ms

        if wall_ms >= budget.max_execution_time_ms:
            self._record_event(
                trace, "BUDGET_WARNING",
                task_id=None, agent_id=None,
                reason=(
                    f"Wall-clock budget exceeded: "
                    f"{wall_ms:.0f} >= {budget.max_execution_time_ms:.0f}ms"
                ),
                metadata={
                    "wall_ms": wall_ms,
                    "limit_ms": budget.max_execution_time_ms,
                },
            )
            report.budget_warnings += 1
            return False

        if budget.consumed_decisions >= budget.max_manager_decisions:
            self._record_event(
                trace, "BUDGET_WARNING",
                task_id=None, agent_id=None,
                reason=(
                    f"Manager-decision budget exceeded: "
                    f"{budget.consumed_decisions} >= "
                    f"{budget.max_manager_decisions} decisions"
                ),
                metadata={
                    "consumed_decisions": budget.consumed_decisions,
                    "limit_decisions": budget.max_manager_decisions,
                },
            )
            report.budget_warnings += 1
            return False

        # Warn when 90% consumed
        if (
            wall_ms >= budget.max_execution_time_ms * 0.9
            and report.budget_warnings == 0
        ):
            self._record_event(
                trace, "BUDGET_WARNING",
                task_id=None, agent_id=None,
                reason=(
                    f"Budget at 90%: {wall_ms:.0f}ms / "
                    f"{budget.max_execution_time_ms:.0f}ms. "
                    f"Staffing limited."
                ),
                metadata={
                    "wall_ms": wall_ms,
                    "pct_consumed": wall_ms / budget.max_execution_time_ms,
                },
            )
            report.budget_warnings += 1

        return True

    # ==================================================================
    # Adaptive replanning
    # ==================================================================

    def _trigger_replan(
        self,
        trigger: str,
        trigger_id: str,
        task_states: dict[str, _TaskState],
        active_pool: list[Agent],
        dynamic_reserve: list[Agent],
        max_agents: int,
        wall_ms: float,
        report: ManagementReport,
        trace: ExecutionTrace,
    ) -> None:
        """
        P (adaptive) — Reassess the organisation after a significant event.

        Triggers:
          - task_failure    : a task failed; re-prioritise remaining work
          - agent_failure   : an agent was quarantined; re-check coverage
          - slow_task       : a task overran its estimate by the configured
                              multiplier (bottleneck signal)
          - budget_critical : wall budget crossed the critical threshold

        Replanning performs targeted adjustments (it does NOT re-run the
        full planning algorithm):

        1. Re-group all still-pending (ready/blocked) tasks into per-
           capability groups (O — Organizing), recording TASK_REORGANIZED.
        2. Recompute capability coverage against the surviving pool and
           note uncovered capabilities as bottlenecks in the trace.
        3. Increment plan_updates / replan_count for the report.
        """
        if not self._cfg.planning_enabled:
            return

        report.replan_count += 1
        report.plan_updates += 1

        pending = [
            ts for ts in task_states.values()
            if ts.status in ("ready", "blocked")
        ]
        failed_count = sum(1 for ts in task_states.values() if ts.status == "failed")

        # --- O — re-group remaining tasks by capability --------------------
        regrouped: dict[str, list[str]] = {}
        for ts in pending:
            regrouped.setdefault(ts.task.required_capability, []).append(ts.task.id)
        for cap in regrouped:
            regrouped[cap].sort(
                key=lambda tid: (
                    PRIORITY_ORDER.get(task_states[tid].task.priority, 99),
                    task_states[tid].task.index,
                )
            )

        # --- capability coverage against surviving pool --------------------
        pool_caps: set[str] = set()
        for a in active_pool:
            pool_caps.update(a.capabilities)
        uncovered = sorted(
            cap for cap in regrouped if cap not in pool_caps
        )

        self._record_event(
            trace, "PLAN_UPDATED",
            task_id=trigger_id, agent_id=None,
            reason=(
                f"Replanning triggered by '{trigger}'"
                + (f" on '{trigger_id}'" if trigger_id else "")
                + f". State: pending={len(pending)}, failed={failed_count}, "
                f"active_agents={len(active_pool)}, wall_ms={wall_ms:.0f}ms. "
                f"Replan #{report.replan_count}."
            ),
            metadata={
                "trigger": trigger,
                "trigger_id": trigger_id,
                "replan_number": report.replan_count,
                "pending_tasks": len(pending),
                "failed_tasks": failed_count,
                "active_agents": len(active_pool),
                "max_agents": max_agents,
                "wall_ms": wall_ms,
                "uncovered_capabilities": uncovered,
            },
        )

        # --- O — record the reorganisation ---------------------------------
        if regrouped:
            self._record_event(
                trace, "TASK_REORGANIZED",
                task_id=trigger_id, agent_id=None,
                reason=(
                    f"Remaining workload reorganised into {len(regrouped)} "
                    f"capability group(s): "
                    + ", ".join(
                        f"'{cap}'({len(tids)} tasks)"
                        for cap, tids in sorted(regrouped.items())
                    )
                    + (f". Uncovered: {uncovered}." if uncovered else ".")
                ),
                metadata={
                    "trigger": trigger,
                    "groups": {
                        cap: tids for cap, tids in sorted(regrouped.items())
                    },
                    "uncovered_capabilities": uncovered,
                },
            )

    # ==================================================================
    # Shared helpers
    # ==================================================================

    def _sample_latency(self) -> float:
        """Draw simulated manager decision latency (ms)."""
        latency = self._rng.gauss(
            self._cfg.decision_latency_mean_ms,
            self._cfg.decision_latency_std_ms,
        )
        return max(1.0, round(latency, 2))

    def _record_event(
        self,
        trace: ExecutionTrace,
        event_type: str,
        task_id: str | None,
        agent_id: str | None,
        reason: str,
        metadata: dict[str, Any] | None = None,
    ) -> DecisionRecord:
        """
        Create and record a POSDCORB event as a DecisionRecord.

        Using DecisionRecord for all events (including non-assign ones) keeps
        the trace format consistent with the Conventional Manager and allows
        the existing metrics engine to process both traces identically.
        """
        rec = DecisionRecord(
            manager_decision=f"posdcorb:{event_type}",
            task_id=task_id or "",
            selected_agent_id=agent_id,
            selected_agent_name=None,
            decision_latency_ms=0.0,   # event records don't add latency themselves
            reason=reason,
            result=None,
            success=None,
            metadata={
                "posdcorb_event_type": event_type,
                **(metadata or {}),
            },
        )
        trace.record(rec)
        return rec

    @staticmethod
    def _finalise_trace(
        trace: ExecutionTrace,
        task_states: dict[str, _TaskState],
        elapsed_ms: float,
        report: ManagementReport,
    ) -> None:
        completed = sum(1 for ts in task_states.values() if ts.status == "completed")
        failed = sum(
            1 for ts in task_states.values() if ts.status in ("failed", "skipped")
        )
        trace.total_tasks = len(task_states)
        trace.completed_tasks = completed
        trace.failed_tasks = failed
        trace.total_wall_ms = round(elapsed_ms, 2)

        # Sync the report's final counters so embedded values are accurate.
        report.budget_consumed_wall_ms = elapsed_ms
        report.completed_tasks = completed
        report.failed_tasks = failed
        report.pending_tasks = sum(
            1 for ts in task_states.values() if ts.status in ("ready", "blocked")
        )
        report.running_tasks = sum(
            1 for ts in task_states.values() if ts.status == "running"
        )

        # Embed the ManagementReport in the trace for downstream consumption.
        # Guard: if no decisions were ever recorded (empty graph), append a
        # synthetic anchor record so the report is still reachable.
        if not trace.decisions:
            anchor = DecisionRecord(
                manager_decision="posdcorb:FINAL_REPORT",
                task_id=None,
                selected_agent_id=None,
                selected_agent_name=None,
                decision_latency_ms=0.0,
                reason="Run finished with no scheduling decisions.",
                result="completed",
                success=True,
                metadata={},
            )
            trace.record(anchor)
        trace.decisions[-1].metadata["management_report"] = report.as_dict() if trace.decisions else {}
