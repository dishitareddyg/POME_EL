"""
Module 8 — Metrics Engine Tests
=================================

Test categories (§15 of spec)
------------------------------
1.  PerformanceMetrics       — throughput, latency, success/completion rate
2.  TaskMetrics              — per-task queue/exec/retry/reassignment
3.  AgentMetrics             — utilisation, idle, activations, failures
4.  ManagementMetrics        — decision overhead, coordination ratio
5.  DynamicStaffingMetrics   — activation rate, tasks-per-agent
6.  ReliabilityMetrics       — failures, recovery, tasks lost
7.  CostMetrics              — budget utilisation, coordination cost
8.  ResourceMonitorMetrics   — unavailable when psutil absent (graceful)
9.  MetricsEngine.compute()  — integration: end-to-end from ExperimentResult
10. ComparisonEngine         — percentage differences, better_manager logic
11. Aggregator               — mean/median/std/min/max across N runs
12. MetricsExporter          — JSON, CSV correctness
13. Edge cases               — zero tasks, one task, identical managers

All expected values are computed analytically from synthetic inputs so there
is no hidden state — the calculations are verifiable by hand.

Run with: python -m pytest tests/test_metrics_engine.py -v
"""
from __future__ import annotations

import csv
import io
import json
import math
from dataclasses import dataclass, field
from typing import Any

import pytest

from app.managers.base import DecisionRecord, ExecutionTrace
from app.metrics.engine import Aggregator, ComparisonEngine, MetricsEngine
from app.metrics.exporter import MetricsExporter
from app.metrics.models import (
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
    _safe_div,
)


# ---------------------------------------------------------------------------
# Helpers / Fixtures
# ---------------------------------------------------------------------------


def make_decision(
    kind: str = "assign",
    task_id: str | None = "t1",
    agent_id: str | None = "agt-1",
    latency_ms: float = 2.0,
    success: bool | None = True,
    metadata: dict[str, Any] | None = None,
) -> DecisionRecord:
    return DecisionRecord(
        manager_decision=kind,
        task_id=task_id,
        selected_agent_id=agent_id,
        selected_agent_name="Agent1",
        decision_latency_ms=latency_ms,
        reason="test",
        result=None,
        success=success,
        metadata=metadata or {},
    )


def make_trace(
    manager_type: str = "conventional",
    total: int = 1,
    completed: int = 1,
    failed: int = 0,
    wall_ms: float = 100.0,
    decisions: list[DecisionRecord] | None = None,
) -> ExecutionTrace:
    t = ExecutionTrace(
        manager_type=manager_type,
        graph_id="g1",
        seed=42,
    )
    t.total_tasks = total
    t.completed_tasks = completed
    t.failed_tasks = failed
    t.total_wall_ms = wall_ms
    t.total_decisions = len(decisions) if decisions else 0
    if decisions:
        t.decisions = list(decisions)
    return t


@dataclass
class _FakeResult:
    """Minimal duck-typed ExperimentResult for MetricsEngine.compute()."""
    manager_type: str = "conventional"
    experiment_id: str = "exp-001"
    seed: int = 42
    workload_id: str = "g1"
    execution_trace: ExecutionTrace = field(default_factory=lambda: make_trace())
    resource_usage: dict = field(default_factory=lambda: {
        "max_manager_decisions": 10_000,
        "max_execution_time_ms": 300_000.0,
        "max_llm_calls": 5_000,
        "max_tokens": 500_000,
    })
    termination_reason: str = "all_tasks_completed"
    error: None = None


engine = MetricsEngine()
comparison_engine = ComparisonEngine()
exporter = MetricsExporter()


# ---------------------------------------------------------------------------
# Helper: build a realistic trace with known values
# ---------------------------------------------------------------------------

def make_full_trace(
    n_tasks: int = 3,
    n_fail: int = 0,
    n_retry: int = 0,
    wall_ms: float = 300.0,
    exec_ms_per_task: float = 80.0,
    latency_ms: float = 5.0,
    manager_type: str = "conventional",
) -> ExecutionTrace:
    """
    Build a trace with n_tasks completed, n_fail failed, n_retry retried.

    For each completed task we emit:  assign → evaluate(success=True)
    For each failed task we emit:     assign → evaluate(success=False) → fail
    For each retried task we emit:    assign → retry → evaluate(success=True)
    """
    decisions = []
    task_ids = [f"t{i}" for i in range(n_tasks + n_fail + n_retry)]
    idx = 0

    # Completed tasks
    for i in range(n_tasks):
        tid = task_ids[idx]; idx += 1
        decisions.append(make_decision("assign", tid, "agt-1", latency_ms))
        decisions.append(make_decision(
            "evaluate", tid, "agt-1", latency_ms, success=True,
            metadata={"elapsed_ms": exec_ms_per_task},
        ))

    # Failed tasks
    for i in range(n_fail):
        tid = task_ids[idx]; idx += 1
        decisions.append(make_decision("assign", tid, "agt-1", latency_ms))
        decisions.append(make_decision("evaluate", tid, "agt-1", latency_ms, success=False))
        decisions.append(make_decision("fail", tid, "agt-1", latency_ms))

    # Retried tasks (retry + eventual success)
    for i in range(n_retry):
        tid = task_ids[idx]; idx += 1
        decisions.append(make_decision("assign", tid, "agt-1", latency_ms))
        decisions.append(make_decision("retry", tid, "agt-1", latency_ms))
        decisions.append(make_decision(
            "evaluate", tid, "agt-1", latency_ms, success=True,
            metadata={"elapsed_ms": exec_ms_per_task},
        ))

    total = n_tasks + n_fail + n_retry
    completed = n_tasks + n_retry  # retried tasks also eventually succeed
    failed = n_fail

    trace = ExecutionTrace(manager_type=manager_type, graph_id="g1", seed=42)
    trace.total_tasks = total
    trace.completed_tasks = completed
    trace.failed_tasks = failed
    trace.total_wall_ms = wall_ms
    trace.total_decisions = len(decisions)
    trace.decisions = decisions
    return trace


# ---------------------------------------------------------------------------
# 0. _safe_div helper
# ---------------------------------------------------------------------------

class TestSafeDiv:
    def test_normal(self):
        assert _safe_div(10.0, 4.0) == pytest.approx(2.5)

    def test_zero_denominator(self):
        assert _safe_div(5.0, 0.0) == 0.0

    def test_custom_fallback(self):
        assert _safe_div(1.0, 0.0, fallback=-1.0) == -1.0

    def test_both_zero(self):
        assert _safe_div(0.0, 0.0) == 0.0


# ---------------------------------------------------------------------------
# 1. PerformanceMetrics
# ---------------------------------------------------------------------------

class TestPerformanceMetrics:

    def _compute(self, **kwargs) -> DerivedMetrics:
        trace = make_full_trace(**kwargs)
        result = _FakeResult(execution_trace=trace)
        return engine.compute(result)

    def test_throughput_known_value(self):
        # 4 completed tasks in 200 ms → 4 / 0.2 = 20 tasks/sec
        m = self._compute(n_tasks=4, n_fail=0, wall_ms=200.0, exec_ms_per_task=40.0)
        assert m.performance.throughput_tasks_per_sec == pytest.approx(20.0, rel=0.01)

    def test_throughput_zero_wall_time(self):
        trace = make_trace(completed=5, wall_ms=0.0)
        result = _FakeResult(execution_trace=trace)
        dm = engine.compute(result)
        assert dm.performance.throughput_tasks_per_sec == 0.0

    def test_task_success_rate(self):
        m = self._compute(n_tasks=3, n_fail=1)
        # 3 completed, 1 failed → total = 4 (via make_full_trace count)
        assert m.performance.task_success_rate == pytest.approx(3 / 4, rel=0.01)

    def test_task_success_rate_zero_tasks(self):
        trace = make_trace(total=0, completed=0, failed=0)
        result = _FakeResult(execution_trace=trace)
        dm = engine.compute(result)
        assert dm.performance.task_success_rate == 0.0

    def test_task_completion_rate(self):
        m = self._compute(n_tasks=2, n_fail=1)
        # 3 total; 2 completed + 1 failed = 3/3 = 1.0
        assert m.performance.task_completion_rate == pytest.approx(1.0, rel=0.01)

    def test_avg_latency_computed(self):
        # Each task: queue=5ms + exec=80ms = 85ms
        m = self._compute(n_tasks=3, n_fail=0, exec_ms_per_task=80.0, latency_ms=5.0)
        # All tasks have the same latency, so avg == 85ms
        assert m.performance.avg_task_latency_ms == pytest.approx(85.0, rel=0.05)

    def test_median_equals_avg_for_uniform_latencies(self):
        m = self._compute(n_tasks=3, n_fail=0, exec_ms_per_task=80.0, latency_ms=5.0)
        assert m.performance.median_task_latency_ms == pytest.approx(
            m.performance.avg_task_latency_ms, rel=0.01
        )

    def test_min_max_latency_equal_for_uniform(self):
        m = self._compute(n_tasks=3, n_fail=0, exec_ms_per_task=80.0, latency_ms=5.0)
        assert m.performance.min_task_latency_ms == pytest.approx(
            m.performance.max_task_latency_ms, rel=0.01
        )

    def test_pending_tasks(self):
        trace = make_trace(total=5, completed=3, failed=1)
        result = _FakeResult(execution_trace=trace)
        dm = engine.compute(result)
        assert dm.performance.pending_tasks == 1   # 5 - 3 - 1

    def test_all_tasks_failed(self):
        m = self._compute(n_tasks=0, n_fail=3)
        assert m.performance.task_success_rate == 0.0
        assert m.performance.completed_tasks == 0

    def test_performance_as_dict_keys(self):
        m = self._compute(n_tasks=1)
        d = m.performance.as_dict()
        for k in ("total_tasks", "completed_tasks", "failed_tasks",
                   "throughput_tasks_per_sec", "task_success_rate",
                   "task_completion_rate", "avg_task_latency_ms"):
            assert k in d, f"missing key: {k}"


# ---------------------------------------------------------------------------
# 2. Task-level Metrics
# ---------------------------------------------------------------------------

class TestTaskMetrics:

    def _compute(self, **kwargs) -> DerivedMetrics:
        trace = make_full_trace(**kwargs)
        return engine.compute(_FakeResult(execution_trace=trace))

    def test_task_count_matches_trace(self):
        m = self._compute(n_tasks=4, n_fail=0)
        # Tasks with assign records
        assert len(m.tasks.tasks) == 4

    def test_completed_task_status(self):
        m = self._compute(n_tasks=2, n_fail=0)
        for stm in m.tasks.tasks.values():
            assert stm.status == "completed"

    def test_failed_task_status(self):
        m = self._compute(n_tasks=0, n_fail=2)
        for stm in m.tasks.tasks.values():
            assert stm.status == "failed"

    def test_execution_time_recorded(self):
        m = self._compute(n_tasks=1, exec_ms_per_task=60.0)
        stm = next(iter(m.tasks.tasks.values()))
        assert stm.execution_time_ms == pytest.approx(60.0, rel=0.01)

    def test_retry_counted(self):
        m = self._compute(n_tasks=0, n_fail=0, n_retry=2)
        for stm in m.tasks.tasks.values():
            assert stm.retry_count == 1  # one retry per task

    def test_total_latency_is_queue_plus_exec(self):
        m = self._compute(n_tasks=1, exec_ms_per_task=70.0, latency_ms=10.0)
        stm = next(iter(m.tasks.tasks.values()))
        assert stm.total_latency_ms == pytest.approx(
            stm.queue_time_ms + stm.execution_time_ms, rel=0.01
        )

    def test_as_rows_format(self):
        m = self._compute(n_tasks=2)
        rows = m.tasks.as_rows()
        assert len(rows) == 2
        for row in rows:
            assert "task_id" in row
            assert "status" in row
            assert "retry_count" in row


# ---------------------------------------------------------------------------
# 3. Agent Metrics
# ---------------------------------------------------------------------------

class TestAgentMetrics:

    def _compute(self, decisions) -> DerivedMetrics:
        trace = ExecutionTrace(manager_type="conventional", graph_id="g1", seed=0)
        trace.total_tasks = 2
        trace.completed_tasks = 2
        trace.failed_tasks = 0
        trace.total_wall_ms = 200.0
        trace.total_decisions = len(decisions)
        trace.decisions = decisions
        return engine.compute(_FakeResult(execution_trace=trace))

    def test_busy_time_accumulated(self):
        decisions = [
            make_decision("assign", "t1", "agt-1"),
            make_decision("evaluate", "t1", "agt-1", success=True,
                          metadata={"elapsed_ms": 50.0}),
            make_decision("assign", "t2", "agt-1"),
            make_decision("evaluate", "t2", "agt-1", success=True,
                          metadata={"elapsed_ms": 30.0}),
        ]
        dm = self._compute(decisions)
        sam = dm.agents.per_agent.get("agt-1")
        assert sam is not None
        assert sam.busy_time_ms == pytest.approx(80.0)

    def test_agent_utilization(self):
        # busy=80ms, wall=200ms → utilization=0.4
        decisions = [
            make_decision("assign", "t1", "agt-1"),
            make_decision("evaluate", "t1", "agt-1", success=True,
                          metadata={"elapsed_ms": 80.0}),
        ]
        dm = self._compute(decisions)
        sam = dm.agents.per_agent["agt-1"]
        assert sam.agent_utilization == pytest.approx(0.4, rel=0.01)

    def test_avg_agent_utilization(self):
        decisions = [
            make_decision("assign", "t1", "agt-1"),
            make_decision("evaluate", "t1", "agt-1", success=True,
                          metadata={"elapsed_ms": 80.0}),
            make_decision("assign", "t2", "agt-2"),
            make_decision("evaluate", "t2", "agt-2", success=True,
                          metadata={"elapsed_ms": 40.0}),
        ]
        dm = self._compute(decisions)
        # agt-1: 80/200=0.4, agt-2: 40/200=0.2 → avg=0.3
        assert dm.agents.avg_agent_utilization == pytest.approx(0.3, rel=0.05)

    def test_activation_count_from_posdcorb_event(self):
        decisions = [
            make_decision("posdcorb:AGENT_ACTIVATED", "t1", "agt-dyn"),
            make_decision("evaluate", "t1", "agt-dyn", success=True,
                          metadata={"elapsed_ms": 50.0}),
        ]
        dm = self._compute(decisions)
        assert dm.agents.activation_count == 1
        assert "agt-dyn" in dm.agents.per_agent
        assert dm.agents.per_agent["agt-dyn"].is_activated is True

    def test_failed_agent_count(self):
        decisions = [
            make_decision("posdcorb:AGENT_FAILED", task_id=None, agent_id="agt-1"),
        ]
        dm = self._compute(decisions)
        assert dm.agents.failed_agent_count == 1

    def test_no_agents_when_empty_trace(self):
        trace = make_trace(completed=0, wall_ms=0.0, decisions=[])
        dm = engine.compute(_FakeResult(execution_trace=trace))
        assert dm.agents.total_active_agents == 0
        assert dm.agents.avg_agent_utilization == 0.0


# ---------------------------------------------------------------------------
# 4. Management Overhead
# ---------------------------------------------------------------------------

class TestManagementMetrics:

    def _compute(self, decisions) -> DerivedMetrics:
        trace = make_trace(decisions=decisions, wall_ms=500.0, completed=2)
        return engine.compute(_FakeResult(execution_trace=trace))

    def test_total_decisions_excludes_engine_events(self):
        decisions = [
            make_decision("engine:EXPERIMENT_STARTED", task_id=None),
            make_decision("assign", "t1"),
            make_decision("engine:EXPERIMENT_COMPLETED", task_id=None),
        ]
        dm = self._compute(decisions)
        assert dm.management.total_decisions == 1

    def test_coordination_overhead_ratio(self):
        # assign → latency=10ms, wall=500ms → ratio=10/500=0.02
        decisions = [make_decision("assign", "t1", latency_ms=10.0)]
        dm = self._compute(decisions)
        assert dm.management.coordination_overhead_ratio == pytest.approx(0.02, rel=0.01)

    def test_coordination_overhead_zero_wall(self):
        trace = make_trace(decisions=[make_decision("assign")], wall_ms=0.0)
        dm = engine.compute(_FakeResult(execution_trace=trace))
        assert dm.management.coordination_overhead_ratio == 0.0

    def test_planning_events_counted(self):
        decisions = [
            make_decision("posdcorb:PLAN_CREATED", task_id=None),
            make_decision("posdcorb:PLAN_UPDATED", task_id=None),
            make_decision("assign", "t1"),
        ]
        dm = self._compute(decisions)
        assert dm.management.planning_events == 2

    def test_staffing_decisions(self):
        decisions = [
            make_decision("posdcorb:AGENT_ACTIVATED", "t1", "agt-2"),
            make_decision("posdcorb:AGENT_ACTIVATED", "t2", "agt-3"),
        ]
        dm = self._compute(decisions)
        assert dm.management.staffing_decisions == 2

    def test_replanning_events(self):
        decisions = [
            make_decision("posdcorb:PLAN_UPDATED", task_id=None),
            make_decision("posdcorb:PLAN_UPDATED", task_id=None),
        ]
        dm = self._compute(decisions)
        assert dm.management.replanning_events == 2

    def test_reassignment_count(self):
        # Only the shared ``retry`` accounting record is counted.  POSDCORB's
        # ``posdcorb:TASK_REASSIGNED`` is an observation event for the same
        # retry and must not be double-counted.
        decisions = [
            make_decision("retry", "t1"),
            make_decision("posdcorb:TASK_REASSIGNED", "t2"),
        ]
        dm = self._compute(decisions)
        assert dm.management.reassignment_count == 1

    def test_dependency_wait_counted(self):
        decisions = [
            make_decision("wait", "t1"),
            make_decision("posdcorb:DEPENDENCY_WAIT", "t2"),
        ]
        dm = self._compute(decisions)
        assert dm.management.dependency_wait_events == 2

    def test_avg_decision_latency(self):
        decisions = [
            make_decision("assign", "t1", latency_ms=10.0),
            make_decision("assign", "t2", latency_ms=20.0),
        ]
        dm = self._compute(decisions)
        assert dm.management.avg_decision_latency_ms == pytest.approx(15.0)

    def test_management_as_dict_keys(self):
        decisions = [make_decision("assign", "t1")]
        dm = self._compute(decisions)
        d = dm.management.as_dict()
        for k in ("total_decisions", "coordination_overhead_ratio",
                   "planning_events", "staffing_decisions",
                   "replanning_events", "reassignment_count"):
            assert k in d


# ---------------------------------------------------------------------------
# 5. Dynamic Staffing
# ---------------------------------------------------------------------------

class TestDynamicStaffingMetrics:

    def test_no_activations_for_conventional(self):
        trace = make_full_trace(n_tasks=5)
        dm = engine.compute(_FakeResult(execution_trace=trace))
        assert dm.staffing.activation_count == 0
        assert dm.staffing.activation_rate == 0.0

    def test_activation_rate_computed(self):
        decisions = [
            make_decision("posdcorb:AGENT_ACTIVATED", "t1", "agt-d1"),
            make_decision("assign", "t1", "agt-d1"),
            make_decision("evaluate", "t1", "agt-d1", success=True,
                          metadata={"elapsed_ms": 50.0}),
            make_decision("assign", "t2", "agt-d1"),
            make_decision("evaluate", "t2", "agt-d1", success=True,
                          metadata={"elapsed_ms": 50.0}),
        ]
        trace = make_trace(decisions=decisions, wall_ms=200.0, total=2, completed=2)
        dm = engine.compute(_FakeResult(execution_trace=trace))
        # 1 activation / 5 manager decisions
        assert dm.staffing.activation_count == 1
        assert dm.staffing.activation_rate == pytest.approx(1 / 5, rel=0.01)

    def test_tasks_per_active_agent(self):
        # 4 completed tasks, 2 agents active
        decisions = [
            make_decision("posdcorb:AGENT_ACTIVATED", "t1", "agt-d1"),
            make_decision("assign", "t1", "agt-1"),
            make_decision("evaluate", "t1", "agt-1", success=True,
                          metadata={"elapsed_ms": 40.0}),
            make_decision("assign", "t2", "agt-d1"),
            make_decision("evaluate", "t2", "agt-d1", success=True,
                          metadata={"elapsed_ms": 40.0}),
        ]
        trace = make_trace(decisions=decisions, wall_ms=200.0, total=2, completed=2)
        dm = engine.compute(_FakeResult(execution_trace=trace))
        # tasks_per_active_agent = 2 / (n_initial + n_dynamic)
        assert dm.staffing.tasks_per_active_agent > 0


# ---------------------------------------------------------------------------
# 6. Reliability Metrics
# ---------------------------------------------------------------------------

class TestReliabilityMetrics:

    def test_no_failures(self):
        trace = make_full_trace(n_tasks=5)
        dm = engine.compute(_FakeResult(execution_trace=trace))
        assert dm.reliability.task_failure_count == 0
        assert dm.reliability.retry_count == 0
        assert dm.reliability.tasks_lost_due_to_failures == 0

    def test_task_failure_count(self):
        trace = make_full_trace(n_tasks=3, n_fail=2)
        dm = engine.compute(_FakeResult(execution_trace=trace))
        assert dm.reliability.task_failure_count == 2

    def test_retry_count(self):
        trace = make_full_trace(n_tasks=2, n_retry=3)
        dm = engine.compute(_FakeResult(execution_trace=trace))
        assert dm.reliability.retry_count == 3

    def test_tasks_lost_no_recovery(self):
        trace = make_full_trace(n_tasks=0, n_fail=3)
        dm = engine.compute(_FakeResult(execution_trace=trace))
        # no recovery events → tasks_lost = 3
        assert dm.reliability.tasks_lost_due_to_failures == 3

    def test_recovery_success_rate(self):
        # 2 task failures, 1 recovery → rate = 0.5
        decisions = [
            make_decision("fail", "t1"),
            make_decision("fail", "t2"),
            make_decision("posdcorb:RECOVERY_COMPLETED", "t1"),
        ]
        trace = make_trace(decisions=decisions, total=2, failed=2, completed=0)
        dm = engine.compute(_FakeResult(execution_trace=trace))
        assert dm.reliability.recovery_count == 1
        # rate = 1 / (0 agent fails + 2 task fails) = 0.5
        assert dm.reliability.recovery_success_rate == pytest.approx(0.5)

    def test_agent_failure_count(self):
        decisions = [
            make_decision("posdcorb:AGENT_FAILED", task_id=None, agent_id="agt-1"),
            make_decision("posdcorb:AGENT_FAILED", task_id=None, agent_id="agt-2"),
        ]
        trace = make_trace(decisions=decisions)
        dm = engine.compute(_FakeResult(execution_trace=trace))
        assert dm.reliability.agent_failure_count == 2

    def test_reliability_as_dict_keys(self):
        trace = make_full_trace()
        dm = engine.compute(_FakeResult(execution_trace=trace))
        d = dm.reliability.as_dict()
        for k in ("task_failure_count", "retry_count", "recovery_count",
                   "recovery_success_rate", "tasks_lost_due_to_failures"):
            assert k in d


# ---------------------------------------------------------------------------
# 7. Cost Metrics
# ---------------------------------------------------------------------------

class TestCostMetrics:

    def test_budget_utilization(self):
        trace = make_trace(completed=1)
        trace.total_decisions = 50
        trace.total_wall_ms = 1000.0
        result = _FakeResult(
            execution_trace=trace,
            resource_usage={
                "max_manager_decisions": 200,
                "max_execution_time_ms": 10_000.0,
                "max_llm_calls": 100,
                "max_tokens": 10_000,
            },
        )
        dm = engine.compute(result)
        assert dm.cost.budget_decisions_utilization == pytest.approx(50 / 200, rel=0.01)
        assert dm.cost.budget_wall_ms_utilization == pytest.approx(1000.0 / 10_000.0, rel=0.01)

    def test_zero_budget_allocation(self):
        trace = make_trace(completed=1)
        result = _FakeResult(
            execution_trace=trace,
            resource_usage={
                "max_manager_decisions": 0,
                "max_execution_time_ms": 0.0,
            },
        )
        dm = engine.compute(result)
        assert dm.cost.budget_decisions_utilization == 0.0
        assert dm.cost.budget_wall_ms_utilization == 0.0

    def test_llm_always_zero(self):
        trace = make_full_trace()
        dm = engine.compute(_FakeResult(execution_trace=trace))
        assert dm.cost.llm_calls_used == 0
        assert dm.cost.tokens_used == 0

    def test_coordination_cost_from_assign_decisions(self):
        decisions = [
            make_decision("assign", "t1", latency_ms=15.0),
            make_decision("assign", "t2", latency_ms=15.0),
        ]
        trace = make_trace(decisions=decisions, wall_ms=200.0)
        dm = engine.compute(_FakeResult(execution_trace=trace))
        assert dm.cost.coordination_cost_ms == pytest.approx(30.0)


# ---------------------------------------------------------------------------
# 8. ResourceMonitorMetrics (unavailable path)
# ---------------------------------------------------------------------------

class TestResourceMonitorMetrics:

    def test_unavailable_when_psutil_missing(self):
        """
        MetricsEngine.compute() always sets available=False
        (host monitoring only runs via compute_host_resources()).
        """
        trace = make_full_trace()
        dm = engine.compute(_FakeResult(execution_trace=trace))
        # compute() sets available=False with an explicit reason
        assert dm.resource_monitor.available is False
        assert len(dm.resource_monitor.unavailable_reason) > 0

    def test_compute_host_resources_returns_result_and_monitor(self):
        def add(a, b):
            return a + b
        result, monitor = engine.compute_host_resources(add, 1, 2)
        assert result == 3
        assert isinstance(monitor, ResourceMonitorMetrics)

    def test_resource_monitor_as_dict_keys(self):
        mon = ResourceMonitorMetrics(available=False, unavailable_reason="test")
        d = mon.as_dict()
        for k in ("available", "avg_cpu_percent", "peak_cpu_percent",
                   "avg_memory_mb", "peak_memory_mb", "unavailable_reason"):
            assert k in d


# ---------------------------------------------------------------------------
# 9. MetricsEngine integration
# ---------------------------------------------------------------------------

class TestMetricsEngineIntegration:

    def test_compute_returns_derived_metrics(self):
        trace = make_full_trace(n_tasks=3)
        dm = engine.compute(_FakeResult(execution_trace=trace))
        assert isinstance(dm, DerivedMetrics)

    def test_manager_type_preserved(self):
        trace = make_full_trace(manager_type="posdcorb")
        dm = engine.compute(_FakeResult(
            manager_type="posdcorb", execution_trace=trace
        ))
        assert dm.manager_type == "posdcorb"

    def test_compute_with_live_engine(self):
        """Use SimulationEngine to produce a real ExperimentResult."""
        from app.models.agent import Agent
        from app.models.workload import Task, TaskGraph, WorkloadGeneratorConfig
        from app.simulation.engine import SimulationConfig, SimulationEngine

        def _agent():
            return Agent(
                id="agt-1", name="A1", role="Processor",
                capabilities=["text-processing"], processing_speed=1.0,
                reliability=1.0, max_concurrent_tasks=4,
            )

        def _task(id, index=0):
            return Task(
                id=id, index=index, task_type="Data Filtering",
                complexity="low", required_capability="text-processing",
                priority="medium", estimated_workload_ms=100,
                dependency_ids=[], status="ready",
            )

        tasks = [_task(f"t{i}", i) for i in range(4)]
        cfg_wl = WorkloadGeneratorConfig(num_tasks=4, complexity="low",
                                          dependency_percentage=0, seed=0)
        graph = TaskGraph(
            graph_id="test-g", seed=0, tasks=tasks,
            edges={t.id: [] for t in tasks}, config=cfg_wl,
        )
        sim_engine = SimulationEngine()
        exp_result = sim_engine.run_experiment(
            graph, [_agent()],
            SimulationConfig(rng_seed=42),
            manager_type="conventional",
        )
        dm = engine.compute(exp_result)
        assert dm.performance.total_tasks == 4
        assert dm.performance.task_success_rate >= 0.0
        assert dm.management.total_decisions >= 0

    def test_as_dict_json_serialisable(self):
        trace = make_full_trace(n_tasks=2)
        dm = engine.compute(_FakeResult(execution_trace=trace))
        json.dumps(dm.as_dict())  # must not raise


# ---------------------------------------------------------------------------
# 10. ComparisonEngine
# ---------------------------------------------------------------------------

class TestComparisonEngine:

    def _make_dm(self, manager: str, throughput: float, latency: float,
                  success_rate: float, overhead: float = 0.1) -> DerivedMetrics:
        dm = DerivedMetrics(
            manager_type=manager,
            experiment_id=f"exp-{manager}",
            seed=42,
            workload_id="g1",
        )
        dm.performance.throughput_tasks_per_sec = throughput
        dm.performance.task_success_rate = success_rate
        dm.performance.total_execution_latency_ms = latency
        dm.performance.avg_task_latency_ms = latency / 10
        dm.performance.total_tasks = 10
        dm.performance.completed_tasks = int(success_rate * 10)
        dm.management.coordination_overhead_ratio = overhead
        return dm

    def test_compare_returns_comparison_result(self):
        conv = self._make_dm("conventional", 10.0, 100.0, 0.8)
        posd = self._make_dm("posdcorb", 12.0, 80.0, 0.9)
        result = comparison_engine.compare(conv, posd)
        assert isinstance(result, ComparisonResult)
        assert len(result.rows) > 0

    def test_higher_throughput_wins(self):
        conv = self._make_dm("conventional", 5.0, 200.0, 0.6)
        posd = self._make_dm("posdcorb", 10.0, 200.0, 0.6)
        result = comparison_engine.compare(conv, posd)
        tput_row = next(r for r in result.rows if r.metric == "throughput_tasks_per_sec")
        assert tput_row.better_manager == "posdcorb"

    def test_lower_latency_wins(self):
        conv = self._make_dm("conventional", 10.0, 200.0, 0.8)
        posd = self._make_dm("posdcorb", 10.0, 100.0, 0.8)
        result = comparison_engine.compare(conv, posd)
        lat_row = next(r for r in result.rows
                       if r.metric == "total_execution_latency_ms")
        assert lat_row.better_manager == "posdcorb"

    def test_tie_detected(self):
        conv = self._make_dm("conventional", 10.0, 100.0, 0.8)
        posd = self._make_dm("posdcorb", 10.0, 100.0, 0.8)
        result = comparison_engine.compare(conv, posd)
        tput_row = next(r for r in result.rows if r.metric == "throughput_tasks_per_sec")
        assert tput_row.better_manager == "tie"

    def test_pct_difference_sign(self):
        """Positive pct_diff means POSDCORB is larger."""
        conv = self._make_dm("conventional", 10.0, 100.0, 0.8)
        posd = self._make_dm("posdcorb", 12.0, 100.0, 0.8)
        result = comparison_engine.compare(conv, posd)
        tput_row = next(r for r in result.rows if r.metric == "throughput_tasks_per_sec")
        # (12-10)/10 * 100 = 20%
        assert tput_row.pct_difference == pytest.approx(20.0, rel=0.01)
        assert tput_row.absolute_difference == pytest.approx(2.0, rel=0.01)

    def test_pct_difference_negative_when_conventional_better(self):
        conv = self._make_dm("conventional", 12.0, 100.0, 0.8)
        posd = self._make_dm("posdcorb", 10.0, 100.0, 0.8)
        result = comparison_engine.compare(conv, posd)
        tput_row = next(r for r in result.rows if r.metric == "throughput_tasks_per_sec")
        # (10-12)/12 * 100 = -16.67%
        assert tput_row.pct_difference < 0

    def test_context_dependent_metric_no_winner(self):
        """Replanning events are context_dependent — no winner is declared."""
        conv = self._make_dm("conventional", 10.0, 100.0, 0.8)
        posd = self._make_dm("posdcorb", 10.0, 100.0, 0.8)
        result = comparison_engine.compare(conv, posd)
        replan_rows = [r for r in result.rows if r.metric == "replanning_events"]
        if replan_rows:
            assert replan_rows[0].better_manager == "context_dependent"

    def test_comparison_as_dict_json_serialisable(self):
        conv = self._make_dm("conventional", 10.0, 100.0, 0.8)
        posd = self._make_dm("posdcorb", 12.0, 80.0, 0.9)
        result = comparison_engine.compare(conv, posd)
        json.dumps(result.as_dict())  # must not raise

    def test_workload_id_and_seed_propagated(self):
        conv = self._make_dm("conventional", 10.0, 100.0, 0.8)
        conv.workload_id = "wl-42"
        conv.seed = 99
        posd = self._make_dm("posdcorb", 10.0, 100.0, 0.8)
        result = comparison_engine.compare(conv, posd)
        assert result.workload_id == "wl-42"
        assert result.seed == 99


# ---------------------------------------------------------------------------
# 11. Aggregator
# ---------------------------------------------------------------------------

class TestAggregator:

    def _make_dm(self, throughput: float, success: float, manager: str = "conventional") -> DerivedMetrics:
        dm = DerivedMetrics(
            manager_type=manager, experiment_id="e1", seed=0, workload_id="g1"
        )
        dm.performance.throughput_tasks_per_sec = throughput
        dm.performance.task_success_rate = success
        dm.performance.total_execution_latency_ms = 1000.0 / throughput if throughput else 0.0
        return dm

    def test_aggregate_mean(self):
        agg = Aggregator("conventional")
        for t in [10.0, 20.0, 30.0]:
            agg.add(self._make_dm(t, 0.9))
        result = agg.aggregate()
        assert result.throughput["mean"] == pytest.approx(20.0, rel=0.01)

    def test_aggregate_median(self):
        agg = Aggregator("conventional")
        for t in [10.0, 20.0, 30.0]:
            agg.add(self._make_dm(t, 0.9))
        result = agg.aggregate()
        assert result.throughput["median"] == pytest.approx(20.0, rel=0.01)

    def test_aggregate_std(self):
        import statistics as _stats
        agg = Aggregator("conventional")
        vals = [10.0, 20.0, 30.0]
        for t in vals:
            agg.add(self._make_dm(t, 0.9))
        result = agg.aggregate()
        expected_std = _stats.stdev(vals)
        assert result.throughput["std"] == pytest.approx(expected_std, rel=0.01)

    def test_aggregate_min_max(self):
        agg = Aggregator("conventional")
        for t in [5.0, 15.0, 25.0]:
            agg.add(self._make_dm(t, 0.9))
        result = agg.aggregate()
        assert result.throughput["min"] == pytest.approx(5.0)
        assert result.throughput["max"] == pytest.approx(25.0)

    def test_aggregate_run_count(self):
        agg = Aggregator("posdcorb")
        for _ in range(7):
            agg.add(self._make_dm(10.0, 0.8, "posdcorb"))
        result = agg.aggregate()
        assert result.run_count == 7
        assert result.throughput["n"] == 7

    def test_aggregate_raw_runs_preserved(self):
        agg = Aggregator("conventional")
        m1 = self._make_dm(10.0, 0.9)
        m2 = self._make_dm(20.0, 0.8)
        agg.add(m1)
        agg.add(m2)
        result = agg.aggregate()
        assert len(result.raw_runs) == 2
        # Check the original DerivedMetrics objects are preserved
        assert result.raw_runs[0].performance.throughput_tasks_per_sec == 10.0

    def test_aggregate_empty(self):
        agg = Aggregator("conventional")
        result = agg.aggregate()
        assert result.run_count == 0
        assert result.throughput == {}

    def test_aggregate_single_run_std_zero(self):
        agg = Aggregator("conventional")
        agg.add(self._make_dm(15.0, 1.0))
        result = agg.aggregate()
        assert result.throughput["std"] == pytest.approx(0.0)

    def test_aggregated_as_dict_keys(self):
        agg = Aggregator("conventional")
        agg.add(self._make_dm(10.0, 0.9))
        result = agg.aggregate()
        d = result.as_dict()
        for k in ("manager_type", "run_count", "throughput",
                   "task_success_rate", "coordination_overhead"):
            assert k in d


# ---------------------------------------------------------------------------
# 12. MetricsExporter
# ---------------------------------------------------------------------------

class TestMetricsExporter:

    def _dm(self) -> DerivedMetrics:
        trace = make_full_trace(n_tasks=2)
        return engine.compute(_FakeResult(execution_trace=trace))

    def test_to_json_returns_valid_json(self):
        dm = self._dm()
        s = exporter.to_json(dm)
        data = json.loads(s)
        assert "performance" in data

    def test_derived_to_json(self):
        dm = self._dm()
        s = exporter.derived_to_json(dm)
        data = json.loads(s)
        assert data["manager_type"] == "conventional"

    def test_comparison_to_json(self):
        conv = DerivedMetrics("conventional", "e1", 42, "g1")
        posd = DerivedMetrics("posdcorb", "e2", 42, "g1")
        result = comparison_engine.compare(conv, posd)
        s = exporter.comparison_to_json(result)
        data = json.loads(s)
        assert "rows" in data

    def test_comparison_to_csv_has_headers(self):
        conv = DerivedMetrics("conventional", "e1", 42, "g1")
        posd = DerivedMetrics("posdcorb", "e2", 42, "g1")
        result = comparison_engine.compare(conv, posd)
        csv_str = exporter.comparison_to_csv(result)
        reader = csv.DictReader(io.StringIO(csv_str))
        rows = list(reader)
        assert len(rows) > 0
        assert "metric" in rows[0]
        assert "better_manager" in rows[0]

    def test_task_metrics_to_csv(self):
        dm = self._dm()
        csv_str = exporter.task_metrics_to_csv(dm)
        if csv_str:
            reader = csv.DictReader(io.StringIO(csv_str))
            rows = list(reader)
            assert all("task_id" in r for r in rows)

    def test_agent_metrics_to_csv(self):
        dm = self._dm()
        csv_str = exporter.agent_metrics_to_csv(dm)
        if csv_str:
            reader = csv.DictReader(io.StringIO(csv_str))
            rows = list(reader)
            assert all("agent_id" in r for r in rows)

    def test_aggregated_to_csv(self):
        agg = Aggregator("conventional")
        trace = make_full_trace()
        agg.add(engine.compute(_FakeResult(execution_trace=trace)))
        result = agg.aggregate()
        csv_str = exporter.aggregated_to_csv(result)
        if csv_str:
            reader = csv.DictReader(io.StringIO(csv_str))
            rows = list(reader)
            assert any(r["metric"] == "throughput" for r in rows)

    def test_summary_to_csv(self):
        dm1 = engine.compute(_FakeResult(execution_trace=make_full_trace()))
        dm2 = engine.compute(_FakeResult(
            manager_type="posdcorb",
            execution_trace=make_full_trace(manager_type="posdcorb"),
        ))
        csv_str = exporter.summary_to_csv([dm1, dm2])
        reader = csv.DictReader(io.StringIO(csv_str))
        rows = list(reader)
        assert len(rows) == 2
        manager_types = {r["manager_type"] for r in rows}
        assert "conventional" in manager_types
        assert "posdcorb" in manager_types

    def test_aggregated_to_json(self):
        agg = Aggregator("posdcorb")
        trace = make_full_trace(manager_type="posdcorb")
        agg.add(engine.compute(_FakeResult(
            manager_type="posdcorb", execution_trace=trace
        )))
        result = agg.aggregate()
        s = exporter.aggregated_to_json(result)
        data = json.loads(s)
        assert "throughput" in data


# ---------------------------------------------------------------------------
# 13. Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:

    def test_zero_task_workload(self):
        trace = make_trace(total=0, completed=0, failed=0, wall_ms=0.0, decisions=[])
        dm = engine.compute(_FakeResult(execution_trace=trace))
        assert dm.performance.total_tasks == 0
        assert dm.performance.throughput_tasks_per_sec == 0.0
        assert dm.performance.task_success_rate == 0.0
        assert dm.management.coordination_overhead_ratio == 0.0

    def test_single_task_workload(self):
        trace = make_full_trace(n_tasks=1)
        dm = engine.compute(_FakeResult(execution_trace=trace))
        assert dm.performance.completed_tasks == 1
        assert dm.performance.task_success_rate == pytest.approx(1.0)

    def test_identical_managers_comparison(self):
        dm1 = DerivedMetrics("conventional", "e1", 0, "g")
        dm2 = DerivedMetrics("posdcorb", "e2", 0, "g")
        result = comparison_engine.compare(dm1, dm2)
        # All numeric rows should be ties
        non_context = [r for r in result.rows
                       if r.better_manager not in ("context_dependent",)]
        for row in non_context:
            assert row.better_manager == "tie", f"{row.metric}: {row.better_manager}"

    def test_compare_preserves_both_metrics(self):
        conv = DerivedMetrics("conventional", "e1", 7, "g")
        posd = DerivedMetrics("posdcorb", "e2", 7, "g")
        result = comparison_engine.compare(conv, posd)
        assert result.conventional_metrics is conv
        assert result.posdcorb_metrics is posd

    def test_full_failure_run(self):
        """All tasks failed — success_rate=0, no latency data."""
        trace = make_full_trace(n_tasks=0, n_fail=5)
        dm = engine.compute(_FakeResult(execution_trace=trace))
        assert dm.performance.task_success_rate == 0.0
        assert dm.performance.avg_task_latency_ms == 0.0
        assert dm.reliability.task_failure_count == 5
        assert dm.reliability.tasks_lost_due_to_failures == 5

    def test_all_tasks_retry_success(self):
        """All tasks succeed after one retry — no losses."""
        trace = make_full_trace(n_tasks=0, n_fail=0, n_retry=4)
        dm = engine.compute(_FakeResult(execution_trace=trace))
        assert dm.performance.completed_tasks == 4
        assert dm.reliability.retry_count == 4
        assert dm.reliability.tasks_lost_due_to_failures == 0

    def test_empty_comparison_rows_when_zero_values(self):
        """Comparison with zero-zero values produces ties, not division errors."""
        conv = DerivedMetrics("conventional", "e1", 0, "g")
        posd = DerivedMetrics("posdcorb", "e2", 0, "g")
        result = comparison_engine.compare(conv, posd)
        for row in result.rows:
            assert math.isfinite(row.pct_difference)
            assert math.isfinite(row.absolute_difference)
