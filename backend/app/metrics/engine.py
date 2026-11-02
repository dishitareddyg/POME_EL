"""
Module 8 — MetricsEngine & ComparisonEngine
============================================

MetricsEngine
    .compute(experiment_result) -> DerivedMetrics
    .compute_host_resources(fn, *args) -> ResourceMonitorMetrics

ComparisonEngine
    .compare(conventional_metrics, posdcorb_metrics) -> ComparisonResult

Design constraints
------------------
* No manager-specific logic is embedded here.  Both managers are treated
  identically — the engine reads only ExperimentResult / ExecutionTrace.
* psutil is optional.  When missing, ResourceMonitorMetrics.available = False
  and a human-readable reason is attached.
* Division-by-zero is prevented throughout via _safe_div().
* No metrics are fabricated.  If a value cannot be derived from the trace it
  is left at its default (0 / 0.0 / False).
"""
from __future__ import annotations

import statistics
from typing import Any, Callable

from app.metrics.models import (
    AgentMetrics,
    AggregatedMetrics,
    ComparisonResult,
    ComparisonRow,
    CostMetrics,
    DerivedMetrics,
    DynamicStaffingMetrics,
    ManagementMetrics,
    PerformanceMetrics,
    ReliabilityMetrics,
    ResourceMonitorMetrics,
    SingleAgentMetrics,
    SingleTaskMetrics,
    TaskMetrics,
    _agg_numeric,
    _safe_div,
)

# We avoid importing engine/manager types at module level to keep metrics
# decoupled; we use TYPE_CHECKING so mypy still works.
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from app.simulation.engine import ExperimentResult
    from app.managers.base import DecisionRecord, ExecutionTrace


# ---------------------------------------------------------------------------
# Decision-type classification
# ---------------------------------------------------------------------------

_PLANNING_KINDS = frozenset({
    "posdcorb:PLAN_CREATED",
    "posdcorb:PLAN_UPDATED",
})
_STAFFING_KINDS = frozenset({
    "posdcorb:AGENT_ACTIVATED",
})
_COORDINATION_KINDS = frozenset({
    "assign",
    "retry",
    "fail",
    "wait",
    "posdcorb:TASK_SCHEDULED",
    "posdcorb:TASK_DIRECTED",
    "posdcorb:TASK_REASSIGNED",
    "posdcorb:DEPENDENCY_WAIT",
    "posdcorb:STATUS_UPDATED",
})
_REPLANNING_KINDS = frozenset({
    "posdcorb:PLAN_UPDATED",
})
# Reassignment is counted from the shared accounting record plus the distinct
# agent-failure recovery event.  POSDCORB also emits
# ``posdcorb:TASK_REASSIGNED`` as a human-readable observation event for the
# very same retry, so including it would count every task retry twice while
# Conventional (which has no observation events) would be counted once.
# ``posdcorb:AGENT_REASSIGNED`` is *not* a duplicate: it records a task moved
# off a failed agent, for which no shared ``retry`` record exists.
_REASSIGNMENT_KINDS = frozenset({
    "retry",
    "posdcorb:AGENT_REASSIGNED",
})
_DEPENDENCY_WAIT_KINDS = frozenset({
    "wait",
    "posdcorb:DEPENDENCY_WAIT",
})
_AGENT_FAILURE_KINDS = frozenset({
    "posdcorb:AGENT_FAILED",
})
# Same reasoning: POSDCORB emits a ``fail`` accounting record plus a
# ``posdcorb:TASK_FAILED`` observation event for the same terminal failure.
# Counting both inflates per-agent failure counts relative to Conventional.
_TASK_FAILURE_KINDS = frozenset({
    "fail",
})
_RECOVERY_KINDS = frozenset({
    "posdcorb:RECOVERY_COMPLETED",
})
# Only the shared ``evaluate`` record carries ``success=True``.  POSDCORB's
# ``TASK_COMPLETED`` is an observation event with ``success=None``, so it can
# never match the completion predicate and is deliberately excluded.
_TASK_COMPLETE_KINDS = frozenset({
    "evaluate",
})
# Queue time is the latency of the real assignment decision.  Observation
# events (TASK_DIRECTED / TASK_SCHEDULED) carry no decision latency, so using
# them would make task latency depend on which observability events a manager
# happens to emit.  Keeping this to the shared "assign" record guarantees that
# latency is comparable across ablation configurations.
_TASK_QUEUE_KINDS = frozenset({"assign"})
_ENGINE_PREFIX = "engine:"


def _is_manager_decision(kind: str) -> bool:
    return not kind.startswith(_ENGINE_PREFIX)


# ---------------------------------------------------------------------------
# MetricsEngine
# ---------------------------------------------------------------------------


class MetricsEngine:
    """
    Compute DerivedMetrics from one ExperimentResult.

    Usage
    -----
    engine = MetricsEngine()
    metrics = engine.compute(experiment_result)
    """

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def compute(self, result: "ExperimentResult") -> DerivedMetrics:
        """
        Derive all metric categories from a single ExperimentResult.

        Parameters
        ----------
        result :
            The ExperimentResult returned by SimulationEngine.run_experiment().

        Returns
        -------
        DerivedMetrics
            Complete metric bundle.  Raw result is never mutated.
        """
        trace = result.execution_trace

        # Separate manager decisions from engine-envelope events once.
        mgr_decisions = [
            d for d in trace.decisions if _is_manager_decision(d.manager_decision)
        ]

        # -- Per-task timing maps ----------------------------------------
        task_exec_ms: dict[str, float] = {}   # task_id → summed exec ms
        task_queue_ms: dict[str, float] = {}  # task_id → first assign latency
        task_retries: dict[str, int] = {}
        task_reassigns: dict[str, int] = {}
        task_status: dict[str, str] = {}
        task_agent: dict[str, str | None] = {}
        agent_busy_ms: dict[str, float] = {}  # agent_id → summed exec ms
        agent_tasks_done: dict[str, int] = {}
        agent_tasks_failed: dict[str, int] = {}

        for d in mgr_decisions:
            tid = d.task_id
            kind = d.manager_decision
            aid = d.selected_agent_id
            elapsed = float(d.metadata.get("elapsed_ms", 0.0) or 0.0)
            latency = float(d.decision_latency_ms or 0.0)

            if tid:
                if kind in _TASK_QUEUE_KINDS:
                    if tid not in task_queue_ms:
                        task_queue_ms[tid] = latency
                    if aid:
                        task_agent[tid] = aid

                if kind in ("evaluate", "retry"):
                    task_exec_ms[tid] = task_exec_ms.get(tid, 0.0) + elapsed
                    if aid:
                        agent_busy_ms[aid] = agent_busy_ms.get(aid, 0.0) + elapsed

                if kind == "retry":
                    task_retries[tid] = task_retries.get(tid, 0) + 1

                if kind in _REASSIGNMENT_KINDS:
                    task_reassigns[tid] = task_reassigns.get(tid, 0) + 1

                if kind in _TASK_COMPLETE_KINDS and d.success:
                    task_status[tid] = "completed"
                    if aid:
                        agent_tasks_done[aid] = agent_tasks_done.get(aid, 0) + 1

                if kind in _TASK_FAILURE_KINDS:
                    task_status[tid] = "failed"
                    if aid:
                        agent_tasks_failed[aid] = agent_tasks_failed.get(aid, 0) + 1

            if kind in _AGENT_FAILURE_KINDS and aid:
                agent_tasks_failed[aid] = agent_tasks_failed.get(aid, 0) + 1

        # -- Gather known task IDs (from trace + graph) ------------------
        all_task_ids: set[str] = set()
        for d in mgr_decisions:
            if d.task_id:
                all_task_ids.add(d.task_id)

        # -- Build per-task metrics ---------------------------------------
        task_latencies: list[float] = []
        task_map: dict[str, SingleTaskMetrics] = {}
        for tid in all_task_ids:
            q = task_queue_ms.get(tid, 0.0)
            e = task_exec_ms.get(tid, 0.0)
            total = q + e
            status = task_status.get(tid, "pending")
            stm = SingleTaskMetrics(
                task_id=tid,
                status=status,
                assigned_agent_id=task_agent.get(tid),
                queue_time_ms=q,
                execution_time_ms=e,
                total_latency_ms=total,
                retry_count=task_retries.get(tid, 0),
                reassignment_count=task_reassigns.get(tid, 0),
            )
            task_map[tid] = stm
            if status == "completed" and total > 0:
                task_latencies.append(total)

        # -- Performance metrics -----------------------------------------
        n_total = trace.total_tasks
        n_done = trace.completed_tasks
        n_fail = trace.failed_tasks
        n_pend = max(0, n_total - n_done - n_fail)
        wall_ms = float(trace.total_wall_ms)

        avg_lat = _safe_div(sum(task_latencies), len(task_latencies)) if task_latencies else 0.0
        med_lat = statistics.median(task_latencies) if task_latencies else 0.0
        min_lat = min(task_latencies) if task_latencies else 0.0
        max_lat = max(task_latencies) if task_latencies else 0.0
        throughput = _safe_div(n_done, wall_ms / 1000.0)

        perf = PerformanceMetrics(
            total_tasks=n_total,
            completed_tasks=n_done,
            failed_tasks=n_fail,
            pending_tasks=n_pend,
            total_execution_latency_ms=wall_ms,
            avg_task_latency_ms=avg_lat,
            median_task_latency_ms=med_lat,
            min_task_latency_ms=min_lat,
            max_task_latency_ms=max_lat,
            throughput_tasks_per_sec=throughput,
            task_success_rate=_safe_div(n_done, n_total),
            task_completion_rate=_safe_div(n_done + n_fail, n_total),
        )

        # -- Management metrics ------------------------------------------
        planning = sum(1 for d in mgr_decisions if d.manager_decision in _PLANNING_KINDS)
        staffing = sum(1 for d in mgr_decisions if d.manager_decision in _STAFFING_KINDS)
        coordination = sum(1 for d in mgr_decisions if d.manager_decision in _COORDINATION_KINDS)
        replanning = sum(1 for d in mgr_decisions if d.manager_decision in _REPLANNING_KINDS)
        reassigns = sum(1 for d in mgr_decisions if d.manager_decision in _REASSIGNMENT_KINDS)
        dep_waits = sum(1 for d in mgr_decisions if d.manager_decision in _DEPENDENCY_WAIT_KINDS)
        mgmt_time = sum(float(d.decision_latency_ms or 0.0) for d in mgr_decisions)
        n_dec = len(mgr_decisions)
        # Observation/notification events carry no simulated think-time.  They
        # are still records in the trace, but averaging them into the decision
        # latency would dilute POSDCORB's latency purely because it logs more
        # observability events.  Average only decisions that actually consumed
        # management time so the metric is comparable across managers.
        n_cost_decisions = sum(
            1 for d in mgr_decisions
            if float(d.decision_latency_ms or 0.0) > 0.0
        )

        mgmt = ManagementMetrics(
            total_decisions=n_dec,
            management_time_ms=mgmt_time,
            avg_decision_latency_ms=_safe_div(mgmt_time, n_cost_decisions),
            planning_events=planning,
            staffing_decisions=staffing,
            coordination_events=coordination,
            replanning_events=replanning,
            reassignment_count=reassigns,
            dependency_wait_events=dep_waits,
            coordination_overhead_ratio=min(1.0, _safe_div(mgmt_time, wall_ms)),
        )

        # -- Agent metrics -----------------------------------------------
        # Build agent pool from the resource_usage info that the engine attaches
        # and from the activation events in the trace.
        activated_ids: set[str] = set()
        failed_agent_ids: set[str] = set()
        for d in mgr_decisions:
            if d.manager_decision in _STAFFING_KINDS and d.selected_agent_id:
                activated_ids.add(d.selected_agent_id)
            if d.manager_decision in _AGENT_FAILURE_KINDS and d.selected_agent_id:
                failed_agent_ids.add(d.selected_agent_id)

        # Gather all agent IDs seen in the trace
        all_agent_ids: set[str] = set()
        for d in mgr_decisions:
            if d.selected_agent_id:
                all_agent_ids.add(d.selected_agent_id)

        per_agent_map: dict[str, SingleAgentMetrics] = {}
        for aid in all_agent_ids:
            busy_ms = agent_busy_ms.get(aid, 0.0)
            util = _safe_div(busy_ms, wall_ms)
            per_agent_map[aid] = SingleAgentMetrics(
                agent_id=aid,
                agent_name=aid,
                origin="dynamic" if aid in activated_ids else "initial",
                tasks_completed=agent_tasks_done.get(aid, 0),
                tasks_failed=agent_tasks_failed.get(aid, 0),
                busy_time_ms=busy_ms,
                available_time_ms=wall_ms,
                agent_utilization=min(1.0, util),
                is_activated=aid in activated_ids,
            )

        utilizations = [a.agent_utilization for a in per_agent_map.values()]
        avg_util = statistics.mean(utilizations) if utilizations else 0.0

        n_initial = len([a for a in per_agent_map.values() if not a.is_activated])
        n_dynamic = len(activated_ids)

        agents_m = AgentMetrics(
            initial_agent_count=n_initial,
            dynamic_agent_count=n_dynamic,
            total_active_agents=len(per_agent_map),
            avg_agent_utilization=avg_util,
            max_simultaneous_agents=len(per_agent_map),  # conservative upper bound
            activation_count=len(activated_ids),
            failed_agent_count=len(failed_agent_ids),
            per_agent=per_agent_map,
        )

        # -- Dynamic staffing metrics ------------------------------------
        # Try to get activation cost from POSDCORB ResourceBudget metadata
        activation_cost_ms = 0.0
        for d in mgr_decisions:
            if d.manager_decision == "posdcorb:RESOURCE_CHECK":
                activation_cost_ms = float(
                    d.metadata.get("consumed_activation_ms", 0.0) or 0.0
                )
                break
        # Also try the last management_report
        for d in reversed(trace.decisions):
            report = d.metadata.get("management_report")
            if report:
                b = report.get("budget", {})
                if b.get("consumed_activation_ms", 0.0):
                    activation_cost_ms = float(b["consumed_activation_ms"])
                break

        n_activated = len(activated_ids)
        total_active = max(n_initial + n_dynamic, 1)
        staffing_m = DynamicStaffingMetrics(
            initial_agent_count=n_initial,
            max_active_agents=total_active,
            avg_active_agents=float(total_active),  # single-snapshot estimate
            activation_count=n_activated,
            activation_rate=_safe_div(n_activated, n_dec),
            estimated_activation_cost_ms=activation_cost_ms,
            tasks_per_active_agent=_safe_div(n_done, total_active),
        )

        # -- Reliability metrics -----------------------------------------
        n_agent_fail = len(failed_agent_ids)
        n_task_fail = n_fail
        n_retry = sum(1 for d in mgr_decisions if d.manager_decision == "retry")
        n_recovery = sum(1 for d in mgr_decisions if d.manager_decision in _RECOVERY_KINDS)
        n_reassign = sum(1 for d in mgr_decisions if d.manager_decision in _REASSIGNMENT_KINDS)

        total_failure_events = n_task_fail + n_agent_fail
        recovery_rate = _safe_div(n_recovery, total_failure_events)
        tasks_lost = max(0, n_task_fail - n_recovery)

        reliability_m = ReliabilityMetrics(
            agent_failure_count=n_agent_fail,
            task_failure_count=n_task_fail,
            retry_count=n_retry,
            reassignment_count=n_reassign,
            recovery_count=n_recovery,
            recovery_success_rate=recovery_rate,
            avg_recovery_time_ms=0.0,  # not tracked per-recovery in trace
            tasks_lost_due_to_failures=tasks_lost,
        )

        # -- Cost metrics ------------------------------------------------
        ru = result.resource_usage or {}
        d_alloc = int(ru.get("max_manager_decisions", 0) or 0)
        # Prefer the engine's authoritative counter, which excludes engine
        # envelope events.  trace.total_decisions includes those events, so
        # using it would count 3 extra "decisions" per run.
        d_used = int(ru.get("decisions_used", trace.total_decisions) or 0)
        w_alloc = float(ru.get("max_execution_time_ms", 0.0) or 0.0)
        w_used = wall_ms

        # coordination_cost: management time for coordination decisions only
        coord_cost = sum(
            float(d.decision_latency_ms or 0.0)
            for d in mgr_decisions
            if d.manager_decision in _COORDINATION_KINDS
        )

        cost_m = CostMetrics(
            budget_decisions_allocated=d_alloc,
            budget_decisions_consumed=d_used,
            budget_decisions_utilization=_safe_div(d_used, d_alloc),
            budget_wall_ms_allocated=w_alloc,
            budget_wall_ms_consumed=w_used,
            budget_wall_ms_utilization=_safe_div(w_used, w_alloc),
            activation_cost_ms=activation_cost_ms,
            coordination_cost_ms=coord_cost,
            llm_calls_used=0,
            llm_calls_allocated=int(ru.get("max_llm_calls", 0) or 0),
            tokens_used=0,
            tokens_allocated=int(ru.get("max_tokens", 0) or 0),
        )

        return DerivedMetrics(
            manager_type=result.manager_type,
            experiment_id=result.experiment_id,
            seed=result.seed,
            workload_id=result.workload_id,
            performance=perf,
            tasks=TaskMetrics(tasks=task_map),
            agents=agents_m,
            management=mgmt,
            staffing=staffing_m,
            reliability=reliability_m,
            cost=cost_m,
            resource_monitor=ResourceMonitorMetrics(
                available=False,
                unavailable_reason="Host monitoring not run for this experiment.",
            ),
        )

    # ------------------------------------------------------------------
    # Host resource monitoring
    # ------------------------------------------------------------------

    def compute_host_resources(
        self,
        fn: Callable[..., Any],
        *args: Any,
        sample_interval_s: float = 0.05,
        **kwargs: Any,
    ) -> tuple[Any, ResourceMonitorMetrics]:
        """
        Execute ``fn(*args, **kwargs)`` while sampling host CPU + memory.

        Returns (result_of_fn, ResourceMonitorMetrics).

        IMPORTANT: Measurements reflect Python process load, NOT simulated
        agent workload.  Label them accordingly in reports.

        psutil is optional.  When unavailable, ResourceMonitorMetrics.available
        is False and the function result is still returned correctly.
        """
        try:
            import psutil  # type: ignore[import]
            import threading
            import time

            proc = psutil.Process()
            cpu_samples: list[float] = []
            mem_samples: list[float] = []
            stop_event = threading.Event()

            def _sampler():
                while not stop_event.is_set():
                    try:
                        cpu_samples.append(proc.cpu_percent(interval=None))
                        mem_samples.append(proc.memory_info().rss / (1024 * 1024))
                    except Exception:
                        pass
                    time.sleep(sample_interval_s)

            # Prime the cpu_percent call (first call returns 0.0)
            proc.cpu_percent(interval=None)
            t = threading.Thread(target=_sampler, daemon=True)
            t.start()
            try:
                fn_result = fn(*args, **kwargs)
            finally:
                stop_event.set()
                t.join(timeout=1.0)

            if cpu_samples:
                mon = ResourceMonitorMetrics(
                    available=True,
                    avg_cpu_percent=sum(cpu_samples) / len(cpu_samples),
                    peak_cpu_percent=max(cpu_samples),
                    avg_memory_mb=sum(mem_samples) / len(mem_samples) if mem_samples else 0.0,
                    peak_memory_mb=max(mem_samples) if mem_samples else 0.0,
                    sample_count=len(cpu_samples),
                )
            else:
                mon = ResourceMonitorMetrics(
                    available=False,
                    unavailable_reason="No samples collected (run too short).",
                )
            return fn_result, mon

        except ImportError:
            fn_result = fn(*args, **kwargs)
            return fn_result, ResourceMonitorMetrics(
                available=False,
                unavailable_reason="psutil not installed.",
            )
        except Exception as exc:
            fn_result = fn(*args, **kwargs)
            return fn_result, ResourceMonitorMetrics(
                available=False,
                unavailable_reason=f"Monitoring error: {exc}",
            )


# ---------------------------------------------------------------------------
# ComparisonEngine
# ---------------------------------------------------------------------------


# Metric spec: (attr_path, label, higher_is_better, interpretation_template)
# attr_path uses dot notation: "performance.throughput_tasks_per_sec"
_COMPARISON_SPECS: list[tuple[str, str, bool | None, str]] = [
    # Performance
    ("performance.total_execution_latency_ms", "total_execution_latency_ms", False,
     "Lower is better. POSDCORB {direction} by {pct:.1f}%."),
    ("performance.avg_task_latency_ms", "avg_task_latency_ms", False,
     "Lower is better. POSDCORB {direction} by {pct:.1f}%."),
    ("performance.median_task_latency_ms", "median_task_latency_ms", False,
     "Lower is better. POSDCORB {direction} by {pct:.1f}%."),
    ("performance.throughput_tasks_per_sec", "throughput_tasks_per_sec", True,
     "Higher is better. POSDCORB {direction} by {pct:.1f}%."),
    ("performance.task_success_rate", "task_success_rate", True,
     "Higher is better. POSDCORB {direction} by {pct:.1f}%."),
    ("performance.task_completion_rate", "task_completion_rate", True,
     "Higher is better. POSDCORB {direction} by {pct:.1f}%."),
    ("performance.completed_tasks", "completed_tasks", True,
     "Higher is better."),
    ("performance.failed_tasks", "failed_tasks", False,
     "Lower is better."),
    # Management
    ("management.coordination_overhead_ratio", "coordination_overhead_ratio", False,
     "Lower is better. Fraction of wall time spent in manager decisions."),
    ("management.avg_decision_latency_ms", "avg_decision_latency_ms", False,
     "Lower is better."),
    ("management.total_decisions", "total_decisions", None,
     "Context-dependent: counts every trace record, including zero-cost "
     "observation events, so it depends on logging granularity."),
    ("management.planning_events", "planning_events", None,
     "POSDCORB-specific event count; the baseline has no planning phase, "
     "so this is not a comparative performance metric."),
    ("management.replanning_events", "replanning_events", None,
     "Context-dependent. Replanning may indicate adaptability or instability."),
    ("management.reassignment_count", "reassignment_count", False,
     "Lower is better (fewer reassignments = more stable plan)."),
    # Agent utilisation
    ("agents.avg_agent_utilization", "avg_agent_utilization", True,
     "Higher is better. Fraction of available time agents were busy."),
    ("agents.activation_count", "activation_count", None,
     "POSDCORB-specific. Context-dependent; compare against throughput gain."),
    ("agents.failed_agent_count", "failed_agent_count", False,
     "Lower is better."),
    # Dynamic staffing
    ("staffing.tasks_per_active_agent", "tasks_per_active_agent", True,
     "Higher is better. Throughput per active agent."),
    ("staffing.activation_rate", "activation_rate", None,
     "Context-dependent. High rate may indicate over-staffing pressure."),
    # Reliability
    ("reliability.task_failure_count", "task_failure_count", False,
     "Lower is better."),
    ("reliability.retry_count", "retry_count", False,
     "Lower is better."),
    ("reliability.recovery_count", "recovery_count", None,
     "Higher = more recovery attempts; combine with recovery_success_rate."),
    ("reliability.recovery_success_rate", "recovery_success_rate", True,
     "Higher is better."),
    ("reliability.tasks_lost_due_to_failures", "tasks_lost_due_to_failures", False,
     "Lower is better."),
    # Cost
    ("cost.budget_decisions_utilization", "budget_decisions_utilization", None,
     "Context-dependent: consumed scheduling budget; higher values indicate "
     "less headroom, not better or worse performance."),
    ("cost.budget_wall_ms_utilization", "budget_wall_ms_utilization", False,
     "Lower is better. Fraction of wall-time budget consumed."),
    ("cost.activation_cost_ms", "activation_cost_ms", False,
     "Lower is better. Estimated cost of dynamic agent activations (POSDCORB)."),
    ("cost.coordination_cost_ms", "coordination_cost_ms", False,
     "Lower is better. Manager time spent on coordination decisions."),
]


def _get_nested(obj: Any, dotted_path: str) -> float:
    """Traverse dotted attribute path; return 0.0 if any segment is missing."""
    parts = dotted_path.split(".")
    cur: Any = obj
    for p in parts:
        cur = getattr(cur, p, None)
        if cur is None:
            return 0.0
    try:
        return float(cur)
    except (TypeError, ValueError):
        return 0.0


class ComparisonEngine:
    """
    Build a ComparisonResult from two DerivedMetrics objects.

    The comparison is always Conventional vs POSDCORB; if you need to compare
    other pairs pass ``manager_a`` / ``manager_b`` but note that metric
    direction labels assume the established pair order.
    """

    def compare(
        self,
        conventional: DerivedMetrics,
        posdcorb: DerivedMetrics,
    ) -> ComparisonResult:
        rows: list[ComparisonRow] = []

        for attr, label, higher_is_better, tmpl in _COMPARISON_SPECS:
            conv_val = _get_nested(conventional, attr)
            posd_val = _get_nested(posdcorb, attr)
            abs_diff = posd_val - conv_val

            # A percentage change relative to a zero baseline is undefined.
            # Do not fabricate a huge number by dividing by 1e-9: report 0.0
            # and state explicitly that the relative change is unavailable.
            baseline_is_zero = abs(conv_val) <= 1e-9
            if baseline_is_zero:
                pct_diff = 0.0
            else:
                pct_diff = (abs_diff / abs(conv_val)) * 100.0

            if higher_is_better is None:
                better = "context_dependent"
            elif abs(abs_diff) < 1e-9:
                better = "tie"
            elif higher_is_better:
                better = "posdcorb" if posd_val > conv_val else "conventional"
            else:
                better = "posdcorb" if posd_val < conv_val else "conventional"

            direction = "higher" if abs_diff >= 0 else "lower"
            if baseline_is_zero:
                interp = (
                    "Relative change undefined (baseline value is zero). "
                    f"Absolute difference: {abs_diff:+.4g}."
                )
            else:
                interp = tmpl.format(direction=direction, pct=abs(pct_diff))

            rows.append(ComparisonRow(
                metric=label,
                conventional_value=conv_val,
                posdcorb_value=posd_val,
                absolute_difference=abs_diff,
                pct_difference=pct_diff,
                higher_is_better=higher_is_better,
                better_manager=better,
                interpretation=interp,
            ))

        return ComparisonResult(
            workload_id=conventional.workload_id,
            seed=conventional.seed,
            rows=rows,
            conventional_metrics=conventional,
            posdcorb_metrics=posdcorb,
        )

    def summary_table(self, result: ComparisonResult) -> list[dict[str, Any]]:
        """Return a list of dicts suitable for tabular display or CSV export."""
        return [r.as_dict() for r in result.rows]


# ---------------------------------------------------------------------------
# Aggregator
# ---------------------------------------------------------------------------


class Aggregator:
    """
    Aggregate DerivedMetrics across multiple runs of the same manager.

    Usage
    -----
    agg = Aggregator()
    for exp_result in repeated_runs:
        agg.add(metrics_engine.compute(exp_result))
    aggregated = agg.aggregate()
    """

    def __init__(self, manager_type: str) -> None:
        self._manager_type = manager_type
        self._runs: list[DerivedMetrics] = []

    def add(self, metrics: DerivedMetrics) -> None:
        """Add one run's DerivedMetrics to the accumulator."""
        self._runs.append(metrics)

    def aggregate(self) -> AggregatedMetrics:
        """Compute statistical summaries across all added runs."""
        agg = AggregatedMetrics(
            manager_type=self._manager_type,
            run_count=len(self._runs),
            raw_runs=list(self._runs),
        )
        if not self._runs:
            return agg

        def _collect(attr: str) -> list[float]:
            return [_get_nested(r, attr) for r in self._runs]

        agg.throughput = _agg_numeric(_collect("performance.throughput_tasks_per_sec"))
        agg.total_latency_ms = _agg_numeric(_collect("performance.total_execution_latency_ms"))
        agg.avg_task_latency_ms = _agg_numeric(_collect("performance.avg_task_latency_ms"))
        agg.task_success_rate = _agg_numeric(_collect("performance.task_success_rate"))
        agg.agent_utilization = _agg_numeric(_collect("agents.avg_agent_utilization"))
        agg.coordination_overhead = _agg_numeric(
            _collect("management.coordination_overhead_ratio")
        )
        agg.activation_count = _agg_numeric(_collect("agents.activation_count"))
        agg.recovery_count = _agg_numeric(_collect("reliability.recovery_count"))
        agg.task_failure_count = _agg_numeric(_collect("reliability.task_failure_count"))
        return agg
