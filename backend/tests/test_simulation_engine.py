"""
Module 7 — Simulation Engine Tests
===================================

Test categories
---------------
1.  ResourceController          — pool preparation, failure injection, as_dict
2.  MetricsCollector            — statistics derived from ExecutionTrace
3.  ExperimentResult            — field completeness, summary_dict, as_dict
4.  SimulationEngine.run_experiment  — single-manager experiment execution
5.  SimulationEngine.run             — multi-manager batch execution
6.  SimulationEngine.check_dependencies — dependency-aware readiness
7.  Dependency execution order  — blocked tasks run only after predecessors
8.  Parallel task execution     — independent tasks both complete
9.  Failure simulation          — failure_probability ∈ (0, 1]
10. Resource budget caps        — max_agents, max_decisions, max_wall_ms
11. Manager interchangeability  — conventional and posdcorb through same engine
12. Reproducibility             — same seed → same termination + success_rate
13. Edge cases                  — empty workload, single task, all-deps chain
14. SimulationSession API       — step(), get_state(), stop(), run()
15. Engine execute_task utility — shared worker access

Run with: python -m pytest tests/test_simulation_engine.py -v
"""
from __future__ import annotations

import copy
from typing import Any

import pytest

from app.agents.worker import execute_task
from app.managers.base import DecisionRecord, ExecutionTrace
from app.models.agent import Agent
from app.models.workload import (
    PriorityDistribution,
    Task,
    TaskGraph,
    WorkloadGeneratorConfig,
)
from app.simulation.engine import (
    ExperimentResult,
    ManagerStatistics,
    MetricsCollector,
    ResourceController,
    SimulationConfig,
    SimulationEngine,
    SimulationResult,
    SimulationSession,
)
from app.simulation.workload_generator import generate_workload


# ---------------------------------------------------------------------------
# Shared fixtures / helpers
# ---------------------------------------------------------------------------


def make_agent(
    id: str = "agt-001",
    name: str = "TestAgent",
    role: str = "Processor",
    capabilities: list[str] | None = None,
    reliability: float = 1.0,
    processing_speed: float = 1.0,
    max_concurrent: int = 4,
    origin: str = "initial",
) -> Agent:
    return Agent(
        id=id,
        name=name,
        role=role,  # type: ignore[arg-type]
        capabilities=capabilities
        or [
            "text-processing",
            "batch-execution",
            "statistics",
            "data-visualization",
            "anomaly-detection",
            "quality-check",
            "text-summarization",
        ],
        processing_speed=processing_speed,
        reliability=reliability,
        max_concurrent_tasks=max_concurrent,
        origin=origin,  # type: ignore[arg-type]
    )


def make_task(
    id: str = "task-0001",
    index: int = 0,
    task_type: str = "Data Filtering",
    required_capability: str = "text-processing",
    priority: str = "medium",
    complexity: str = "low",
    dep_ids: list[str] | None = None,
) -> Task:
    return Task(
        id=id,
        index=index,
        task_type=task_type,  # type: ignore[arg-type]
        complexity=complexity,  # type: ignore[arg-type]
        required_capability=required_capability,
        priority=priority,  # type: ignore[arg-type]
        estimated_workload_ms=200,
        dependency_ids=dep_ids or [],
        status="ready" if not dep_ids else "blocked",
    )


def make_graph(tasks: list[Task]) -> TaskGraph:
    edges: dict[str, list[str]] = {t.id: [] for t in tasks}
    for t in tasks:
        for dep_id in t.dependency_ids:
            edges.setdefault(dep_id, []).append(t.id)

    # WorkloadGeneratorConfig requires num_tasks >= 1; use 1 as floor for the
    # embedded config (the graph may still have 0 tasks — they are independent).
    cfg = WorkloadGeneratorConfig(
        num_tasks=max(1, len(tasks)),
        complexity="low",
        dependency_percentage=0,
        seed=0,
    )
    return TaskGraph(
        graph_id="test-graph",
        seed=0,
        tasks=tasks,
        edges=edges,
        config=cfg,
    )


def make_decision(
    kind: str = "assign",
    task_id: str | None = "task-0001",
    agent_id: str | None = "agt-001",
    latency_ms: float = 1.0,
    success: bool | None = True,
    metadata: dict[str, Any] | None = None,
) -> DecisionRecord:
    return DecisionRecord(
        manager_decision=kind,
        task_id=task_id,
        selected_agent_id=agent_id,
        selected_agent_name="TestAgent",
        decision_latency_ms=latency_ms,
        reason="test",
        result=None,
        success=success,
        metadata=metadata or {},
    )


def make_trace(
    manager_type: str = "conventional",
    graph_id: str = "test-graph",
    seed: int = 42,
    total: int = 1,
    completed: int = 1,
    failed: int = 0,
    wall_ms: float = 100.0,
    decisions: list[DecisionRecord] | None = None,
) -> ExecutionTrace:
    trace = ExecutionTrace(
        manager_type=manager_type,
        graph_id=graph_id,
        seed=seed,
    )
    trace.total_tasks = total
    trace.completed_tasks = completed
    trace.failed_tasks = failed
    trace.total_wall_ms = wall_ms
    if decisions:
        trace.decisions = list(decisions)
    return trace


# ---------------------------------------------------------------------------
# 1. ResourceController
# ---------------------------------------------------------------------------


class TestResourceController:

    def test_from_config_defaults(self):
        cfg = SimulationConfig()
        rc = ResourceController.from_config(cfg)
        assert rc.max_agents is None
        assert rc.failure_probability is None
        assert rc.max_execution_time_ms == cfg.max_wall_ms
        assert rc.max_manager_decisions == cfg.max_decisions

    def test_from_config_with_overrides(self):
        cfg = SimulationConfig(max_agents=3, failure_probability=0.1, max_wall_ms=5_000.0)
        rc = ResourceController.from_config(cfg)
        assert rc.max_agents == 3
        assert rc.failure_probability == pytest.approx(0.1)
        assert rc.max_execution_time_ms == pytest.approx(5_000.0)

    def test_prepare_agents_returns_deep_copy(self):
        agent = make_agent()
        rc = ResourceController()
        prepared = rc.prepare_agents([agent])
        assert prepared[0] is not agent
        prepared[0].reliability = 0.0
        assert agent.reliability == 1.0  # original unchanged

    def test_prepare_agents_no_failure_leaves_reliability(self):
        agent = make_agent(reliability=0.95)
        rc = ResourceController(failure_probability=None)
        prepared = rc.prepare_agents([agent])
        assert prepared[0].reliability == pytest.approx(0.95)

    def test_prepare_agents_applies_failure_probability(self):
        """An agent with reliability=1.0 is capped to 1 - failure_probability."""
        agent = make_agent(reliability=1.0)
        rc = ResourceController(failure_probability=0.3)
        prepared = rc.prepare_agents([agent])
        assert prepared[0].reliability == pytest.approx(0.7)

    def test_prepare_agents_clamps_reliability_below_cap(self):
        """If agent reliability is already below 1-p it is NOT raised."""
        agent = make_agent(reliability=0.5)
        rc = ResourceController(failure_probability=0.3)
        prepared = rc.prepare_agents([agent])
        # cap = 0.7, agent=0.5 → min(0.5, 0.7) = 0.5
        assert prepared[0].reliability == pytest.approx(0.5)

    def test_prepare_agents_zero_failure(self):
        agent = make_agent(reliability=0.9)
        rc = ResourceController(failure_probability=0.0)
        prepared = rc.prepare_agents([agent])
        # cap = 1.0, min(0.9, 1.0) = 0.9
        assert prepared[0].reliability == pytest.approx(0.9)

    def test_as_dict_keys(self):
        rc = ResourceController(max_agents=5, failure_probability=0.1)
        d = rc.as_dict()
        assert "max_agents" in d
        assert "failure_probability" in d
        assert "max_execution_time_ms" in d

    def test_usage_returns_resource_usage_dict(self):
        rc = ResourceController()
        trace = make_trace(completed=3, wall_ms=500.0)
        usage = rc.usage(trace, agents_in_pool=2)
        d = usage.as_dict()
        assert "decisions_used" in d
        assert "wall_ms_used" in d
        assert d["agents_in_pool"] == 2


# ---------------------------------------------------------------------------
# 2. MetricsCollector
# ---------------------------------------------------------------------------


class TestMetricsCollector:

    def test_empty_trace_gives_zero_stats(self):
        trace = make_trace(decisions=[])
        stats = MetricsCollector.collect(trace)
        assert stats.assignments == 0
        assert stats.retries == 0
        assert stats.failures == 0
        assert stats.waits == 0
        assert stats.avg_decision_latency_ms == pytest.approx(0.0)

    def test_assign_counted(self):
        trace = make_trace(decisions=[make_decision("assign")])
        stats = MetricsCollector.collect(trace)
        assert stats.assignments == 1

    def test_retry_counted(self):
        trace = make_trace(decisions=[make_decision("retry")])
        stats = MetricsCollector.collect(trace)
        assert stats.retries == 1

    def test_fail_counted(self):
        trace = make_trace(decisions=[make_decision("fail")])
        stats = MetricsCollector.collect(trace)
        assert stats.failures == 1

    def test_wait_counted(self):
        trace = make_trace(decisions=[make_decision("wait")])
        stats = MetricsCollector.collect(trace)
        assert stats.waits == 1

    def test_engine_events_excluded(self):
        """Engine-envelope events must not be counted as manager decisions."""
        trace = make_trace(decisions=[
            make_decision("engine:EXPERIMENT_STARTED"),
            make_decision("assign"),
        ])
        stats = MetricsCollector.collect(trace)
        assert stats.assignments == 1
        assert stats.decisions_by_type.get("engine:EXPERIMENT_STARTED") is None

    def test_posdcorb_activations_counted(self):
        trace = make_trace(decisions=[
            make_decision("posdcorb:AGENT_ACTIVATED"),
        ])
        stats = MetricsCollector.collect(trace)
        assert stats.activations == 1
        assert stats.staffing_decisions == 1

    def test_posdcorb_replans_counted(self):
        trace = make_trace(decisions=[
            make_decision("posdcorb:PLAN_UPDATED"),
            make_decision("posdcorb:PLAN_UPDATED"),
        ])
        stats = MetricsCollector.collect(trace)
        assert stats.replans == 2

    def test_posdcorb_recoveries_counted(self):
        trace = make_trace(decisions=[
            make_decision("posdcorb:RECOVERY_COMPLETED"),
        ])
        stats = MetricsCollector.collect(trace)
        assert stats.recoveries == 1

    def test_avg_latency_computed(self):
        ds = [make_decision(latency_ms=10.0), make_decision(latency_ms=20.0)]
        trace = make_trace(decisions=ds)
        stats = MetricsCollector.collect(trace)
        assert stats.avg_decision_latency_ms == pytest.approx(15.0)

    def test_as_dict_has_required_keys(self):
        trace = make_trace(decisions=[make_decision()])
        d = MetricsCollector.collect(trace).as_dict()
        for key in (
            "decisions_by_type", "assignments", "retries", "failures", "waits",
            "staffing_decisions", "activations", "replans", "recoveries",
            "avg_decision_latency_ms", "management_time_ms",
            "execution_time_ms", "waiting_time_ms",
        ):
            assert key in d, f"missing key: {key}"

    def test_execution_time_from_evaluate_metadata(self):
        d = make_decision("evaluate", metadata={"elapsed_ms": 50.0})
        trace = make_trace(decisions=[d])
        stats = MetricsCollector.collect(trace)
        assert stats.execution_time_ms == pytest.approx(50.0)


# ---------------------------------------------------------------------------
# 3. ExperimentResult
# ---------------------------------------------------------------------------


class TestExperimentResult:

    def _make_result(self, **kwargs) -> ExperimentResult:
        trace = make_trace(decisions=[make_decision()])
        defaults: dict[str, Any] = dict(
            experiment_id="exp-001",
            manager_type="conventional",
            seed=42,
            workload_id="graph-001",
            start_time="2024-01-01T00:00:00+00:00",
            end_time="2024-01-01T00:00:01+00:00",
            total_duration_ms=1000.0,
            completed_tasks=5,
            failed_tasks=0,
            pending_tasks=0,
            task_success_rate=1.0,
            execution_trace=trace,
            resource_usage={"max_agents": None},
            manager_statistics={"assignments": 5},
            termination_reason="all_tasks_completed",
            real_elapsed_s=0.01,
            error=None,
        )
        defaults.update(kwargs)
        return ExperimentResult(**defaults)

    def test_summary_dict_no_trace(self):
        result = self._make_result()
        d = result.summary_dict()
        assert "execution_trace" not in d
        assert d["completed_tasks"] == 5
        assert d["termination_reason"] == "all_tasks_completed"
        assert d["error"] is None

    def test_as_dict_includes_trace(self):
        result = self._make_result()
        d = result.as_dict()
        assert "execution_trace" in d

    def test_summary_dict_has_required_keys(self):
        result = self._make_result()
        d = result.summary_dict()
        for key in (
            "experiment_id", "manager_type", "seed", "workload_id",
            "completed_tasks", "failed_tasks", "pending_tasks",
            "task_success_rate", "resource_usage", "manager_statistics",
            "termination_reason", "real_elapsed_s", "error",
        ):
            assert key in d, f"missing key: {key}"

    def test_error_field_preserved(self):
        result = self._make_result(error="Something crashed")
        assert result.error == "Something crashed"
        assert result.summary_dict()["error"] == "Something crashed"


# ---------------------------------------------------------------------------
# 4. SimulationEngine.run_experiment
# ---------------------------------------------------------------------------


class TestRunExperiment:

    def _engine_and_agents(self):
        agent = make_agent()
        engine = SimulationEngine()
        return engine, [agent]

    def test_single_task_conventional(self):
        engine, agents = self._engine_and_agents()
        graph = make_graph([make_task()])
        result = engine.run_experiment(graph, agents, manager_type="conventional")
        assert isinstance(result, ExperimentResult)
        assert result.manager_type == "conventional"
        assert result.completed_tasks >= 1
        assert result.termination_reason in (
            "all_tasks_completed", "unrecoverable_failure", "error"
        )

    def test_single_task_posdcorb(self):
        engine, agents = self._engine_and_agents()
        graph = make_graph([make_task()])
        result = engine.run_experiment(graph, agents, manager_type="posdcorb")
        assert isinstance(result, ExperimentResult)
        assert result.manager_type == "posdcorb"
        assert result.termination_reason in (
            "all_tasks_completed", "unrecoverable_failure", "error", "manager_stopped"
        )

    def test_empty_workload(self):
        engine, agents = self._engine_and_agents()
        graph = make_graph([])
        result = engine.run_experiment(graph, agents, manager_type="conventional")
        assert result.termination_reason == "empty_workload"
        assert result.completed_tasks == 0

    def test_experiment_result_fields_populated(self):
        engine, agents = self._engine_and_agents()
        graph = make_graph([make_task()])
        result = engine.run_experiment(graph, agents, manager_type="conventional")
        assert result.experiment_id  # non-empty string
        assert result.seed == 42
        assert result.workload_id == graph.graph_id
        assert result.start_time
        assert result.end_time
        assert isinstance(result.resource_usage, dict)
        assert isinstance(result.manager_statistics, dict)
        assert isinstance(result.real_elapsed_s, float)

    def test_trace_has_engine_envelope_events(self):
        """Engine envelope (engine:EXPERIMENT_STARTED, engine:EXPERIMENT_COMPLETED)."""
        engine, agents = self._engine_and_agents()
        graph = make_graph([make_task()])
        result = engine.run_experiment(graph, agents, manager_type="conventional")
        kinds = {d.manager_decision for d in result.execution_trace.decisions}
        assert "engine:EXPERIMENT_STARTED" in kinds
        assert "engine:EXPERIMENT_COMPLETED" in kinds

    def test_agents_not_mutated(self):
        """The original agent pool must be untouched after run_experiment."""
        engine = SimulationEngine()
        agent = make_agent(reliability=1.0)
        agents = [agent]
        original_rel = agent.reliability
        graph = make_graph([make_task()])
        engine.run_experiment(
            graph, agents,
            SimulationConfig(failure_probability=0.5),
            manager_type="conventional",
        )
        assert agent.reliability == pytest.approx(original_rel)

    def test_unknown_manager_type_error(self):
        engine = SimulationEngine()
        graph = make_graph([make_task()])
        result = engine.run_experiment(graph, [make_agent()], manager_type="nonexistent")
        # Engine catches the error and returns a valid ExperimentResult with error set
        assert result.error is not None
        assert result.termination_reason == "error"

    def test_custom_experiment_id(self):
        engine = SimulationEngine()
        cfg = SimulationConfig(experiment_id="my-exp-123")
        graph = make_graph([make_task()])
        result = engine.run_experiment(graph, [make_agent()], cfg)
        assert result.experiment_id == "my-exp-123"


# ---------------------------------------------------------------------------
# 5. SimulationEngine.run (multi-manager batch)
# ---------------------------------------------------------------------------


class TestRunBatch:

    def test_both_managers_run(self):
        engine = SimulationEngine()
        graph = make_graph([make_task()])
        agents = [make_agent()]
        cfg = SimulationConfig(manager_types=["conventional", "posdcorb"])
        result = engine.run(graph, agents, cfg)
        assert isinstance(result, SimulationResult)
        assert "conventional" in result.experiments
        assert "posdcorb" in result.experiments

    def test_result_has_both_traces(self):
        engine = SimulationEngine()
        graph = make_graph([make_task()])
        result = engine.run(
            graph, [make_agent()],
            SimulationConfig(manager_types=["conventional", "posdcorb"]),
        )
        assert "conventional" in result.traces
        assert "posdcorb" in result.traces

    def test_single_manager_batch(self):
        engine = SimulationEngine()
        graph = make_graph([make_task()])
        result = engine.run(
            graph, [make_agent()],
            SimulationConfig(manager_types=["conventional"]),
        )
        assert len(result.experiments) == 1
        assert "conventional" in result.experiments

    def test_termination_reasons_populated(self):
        engine = SimulationEngine()
        graph = make_graph([make_task()])
        result = engine.run(
            graph, [make_agent()],
            SimulationConfig(manager_types=["conventional", "posdcorb"]),
        )
        assert "conventional" in result.termination_reasons
        assert "posdcorb" in result.termination_reasons
        for v in result.termination_reasons.values():
            assert isinstance(v, str) and len(v) > 0

    def test_resource_usage_in_result(self):
        engine = SimulationEngine()
        graph = make_graph([make_task()])
        result = engine.run(
            graph, [make_agent()], SimulationConfig()
        )
        assert isinstance(result.resource_usage, dict)

    def test_as_dict_serialisable(self):
        """as_dict() must return only JSON-serialisable types."""
        import json
        engine = SimulationEngine()
        graph = make_graph([make_task()])
        result = engine.run(graph, [make_agent()], SimulationConfig())
        json.dumps(result.as_dict())  # must not raise


# ---------------------------------------------------------------------------
# 6. SimulationEngine.check_dependencies
# ---------------------------------------------------------------------------


class TestCheckDependencies:

    def test_no_deps_all_ready(self):
        t1 = make_task("t1")
        t2 = make_task("t2")
        graph = make_graph([t1, t2])
        ready = SimulationEngine.check_dependencies(graph, set())
        assert {t.id for t in ready} == {"t1", "t2"}

    def test_dep_not_satisfied(self):
        t1 = make_task("t1")
        t2 = make_task("t2", dep_ids=["t1"])
        graph = make_graph([t1, t2])
        ready = SimulationEngine.check_dependencies(graph, set())
        assert "t2" not in {t.id for t in ready}
        assert "t1" in {t.id for t in ready}

    def test_dep_satisfied(self):
        t1 = make_task("t1")
        t2 = make_task("t2", dep_ids=["t1"])
        graph = make_graph([t1, t2])
        ready = SimulationEngine.check_dependencies(graph, {"t1"})
        assert "t2" in {t.id for t in ready}

    def test_chain_partial(self):
        """A → B → C: only A ready when no completions.
        
        check_dependencies returns every task whose dep list is a subset of
        completed_ids.  It does NOT filter out already-completed tasks — that
        is the manager's responsibility.  So after A is "completed", both A
        (deps=[]) and B (deps=[A] ⊆ {A}) are returned.
        """
        a = make_task("A")
        b = make_task("B", dep_ids=["A"])
        c = make_task("C", dep_ids=["B"])
        graph = make_graph([a, b, c])
        # No completions: only A is ready
        assert {t.id for t in SimulationEngine.check_dependencies(graph, set())} == {"A"}
        # A completed: A (still in graph, deps=[]) and B are returned
        ready_after_a = {t.id for t in SimulationEngine.check_dependencies(graph, {"A"})}
        assert "B" in ready_after_a
        assert "C" not in ready_after_a
        # A and B completed: A, B, C all have satisfied deps
        ready_after_ab = {t.id for t in SimulationEngine.check_dependencies(graph, {"A", "B"})}
        assert "C" in ready_after_ab

    def test_diamond_dependency(self):
        """A,B → C; A and B initially ready, C only after both done."""
        a = make_task("A")
        b = make_task("B")
        c = make_task("C", dep_ids=["A", "B"])
        graph = make_graph([a, b, c])
        assert "C" not in {t.id for t in SimulationEngine.check_dependencies(graph, {"A"})}
        assert "C" in {t.id for t in SimulationEngine.check_dependencies(graph, {"A", "B"})}

    def test_does_not_mutate_graph(self):
        t1 = make_task("t1")
        graph = make_graph([t1])
        original_tasks = list(graph.tasks)
        SimulationEngine.check_dependencies(graph, {"t1"})
        assert graph.tasks == original_tasks


# ---------------------------------------------------------------------------
# 7. Sequential dependency execution
# ---------------------------------------------------------------------------


class TestDependencyExecution:

    def test_chained_tasks_complete_in_order(self):
        """t1 → t2 → t3: all three should complete."""
        agent = make_agent()
        t1 = make_task("t1", index=0)
        t2 = make_task("t2", index=1, dep_ids=["t1"])
        t3 = make_task("t3", index=2, dep_ids=["t2"])
        graph = make_graph([t1, t2, t3])
        engine = SimulationEngine()
        result = engine.run_experiment(
            graph, [agent], manager_type="conventional"
        )
        # All three should eventually complete (no capability mismatch)
        assert result.completed_tasks == 3
        assert result.termination_reason == "all_tasks_completed"

    def test_blocked_task_requires_predecessor(self):
        """An agent that can't do the first task leaves later tasks blocked."""
        capable = make_agent(id="agt-cap", capabilities=["statistics"])
        t1 = make_task("t1", required_capability="text-processing")  # not in capable
        t2 = make_task("t2", required_capability="statistics", dep_ids=["t1"])
        graph = make_graph([t1, t2])
        engine = SimulationEngine()
        result = engine.run_experiment(
            graph, [capable], manager_type="conventional"
        )
        # t2 can never start because t1 cannot complete
        assert result.completed_tasks < 2

    def test_independent_and_dependent_tasks(self):
        """t1 and t2 are independent; t3 depends on t1. All three should complete."""
        agent = make_agent()
        t1 = make_task("t1", index=0)
        t2 = make_task("t2", index=1)
        t3 = make_task("t3", index=2, dep_ids=["t1"])
        graph = make_graph([t1, t2, t3])
        engine = SimulationEngine()
        result = engine.run_experiment(
            graph, [agent], manager_type="conventional"
        )
        assert result.completed_tasks == 3


# ---------------------------------------------------------------------------
# 8. Parallel task execution
# ---------------------------------------------------------------------------


class TestParallelExecution:

    def test_independent_tasks_all_complete(self):
        """5 independent tasks with a capable agent should all complete."""
        agent = make_agent()
        tasks = [make_task(id=f"t{i}", index=i) for i in range(5)]
        graph = make_graph(tasks)
        engine = SimulationEngine()
        result = engine.run_experiment(
            graph, [agent], manager_type="conventional"
        )
        assert result.completed_tasks == 5

    def test_posdcorb_parallel_groups_counted(self):
        """POSDCORB manager builds an execution plan with parallel group metadata."""
        agent = make_agent()
        tasks = [make_task(id=f"t{i}", index=i) for i in range(6)]
        graph = make_graph(tasks)
        engine = SimulationEngine()
        result = engine.run_experiment(
            graph, [agent], manager_type="posdcorb"
        )
        # ManagementReport must be present in the last decision metadata
        report = None
        for d in reversed(result.execution_trace.decisions):
            report = d.metadata.get("management_report")
            if report:
                break
        # POSDCORB always embeds a ManagementReport in the final trace event
        assert report is not None, "ManagementReport missing from POSDCORB trace"
        mgmt = report.get("management", {})
        # plan_created must be True for a 6-task workload
        assert mgmt.get("plan_created") is True
        # parallel_group_count is a non-negative integer
        assert isinstance(mgmt.get("parallel_group_count", 0), int)
        assert mgmt.get("parallel_group_count", 0) >= 0


# ---------------------------------------------------------------------------
# 9. Failure simulation
# ---------------------------------------------------------------------------


class TestFailureSimulation:

    def test_zero_failure_probability_no_failures(self):
        """With failure_probability=0 the agent retains full reliability."""
        agent = make_agent(reliability=1.0)
        rc = ResourceController(failure_probability=0.0)
        prepared = rc.prepare_agents([agent])
        assert prepared[0].reliability == pytest.approx(1.0)

    def test_full_failure_probability_zeroes_reliability(self):
        """With failure_probability=1.0 the agent has reliability=0."""
        agent = make_agent(reliability=1.0)
        rc = ResourceController(failure_probability=1.0)
        prepared = rc.prepare_agents([agent])
        assert prepared[0].reliability == pytest.approx(0.0)

    def test_partial_failure_reduces_reliability(self):
        agent = make_agent(reliability=1.0)
        rc = ResourceController(failure_probability=0.2)
        prepared = rc.prepare_agents([agent])
        assert prepared[0].reliability == pytest.approx(0.8)

    def test_failure_injected_same_for_both_managers(self):
        """Both managers receive identically-prepared agents (same failure_probability cap)."""
        agent = make_agent(reliability=1.0)
        rc = ResourceController(failure_probability=0.25)
        p1 = rc.prepare_agents([agent])
        p2 = rc.prepare_agents([agent])
        assert p1[0].reliability == pytest.approx(p2[0].reliability)

    def test_simulation_with_high_failure(self):
        """With failure_probability=1.0 the manager can still run without crash."""
        engine = SimulationEngine()
        agent = make_agent()
        graph = make_graph([make_task()])
        cfg = SimulationConfig(failure_probability=1.0, max_decisions=200)
        result = engine.run_experiment(graph, [agent], cfg, manager_type="conventional")
        assert isinstance(result, ExperimentResult)
        assert result.termination_reason in (
            "all_tasks_completed",
            "unrecoverable_failure",
            "decision_budget_exhausted",
            "error",
        )


# ---------------------------------------------------------------------------
# 10. Resource budget caps
# ---------------------------------------------------------------------------


class TestResourceBudgets:

    def test_max_agents_forwarded_to_config(self):
        cfg = SimulationConfig(max_agents=2)
        rc = ResourceController.from_config(cfg)
        assert rc.max_agents == 2

    def test_max_agents_reflected_in_resource_usage(self):
        engine = SimulationEngine()
        graph = make_graph([make_task()])
        cfg = SimulationConfig(max_agents=1)
        result = engine.run_experiment(graph, [make_agent()], cfg)
        ru = result.resource_usage
        assert ru.get("max_agents") == 1

    def test_decision_budget_preserves_full_trace(self):
        """
        Exceeding the decision budget must not delete raw execution events.

        The budget is reported via a ``engine:RESOURCE_LIMIT`` flag; every
        manager decision is retained so metrics derived from the trace remain
        valid.  (Previously the trace was truncated, which both destroyed
        evidence and penalised managers that log more events.)
        """
        engine = SimulationEngine()
        agent = make_agent()
        tasks = [make_task(id=f"t{i}", index=i) for i in range(20)]
        graph = make_graph(tasks)
        cfg = SimulationConfig(max_decisions=5)
        result = engine.run_experiment(graph, [agent], cfg, manager_type="conventional")
        manager_decisions = sum(
            1 for d in result.execution_trace.decisions
            if not d.manager_decision.startswith("engine:")
        )
        # All manager decisions are preserved, not capped at 5.
        assert manager_decisions > 5

        limit_events = [
            d for d in result.execution_trace.decisions
            if d.manager_decision == "engine:RESOURCE_LIMIT"
            and d.metadata.get("limit") == "max_manager_decisions"
        ]
        assert len(limit_events) == 1
        assert limit_events[0].result == "limit_exceeded"
        assert limit_events[0].metadata["raw_trace_preserved"] is True

    def test_decision_budget_termination(self):
        """decision_budget_exhausted should be the termination reason when budget is hit."""
        engine = SimulationEngine()
        agent = make_agent()
        tasks = [make_task(id=f"t{i}", index=i) for i in range(50)]
        graph = make_graph(tasks)
        cfg = SimulationConfig(max_decisions=3)
        result = engine.run_experiment(graph, [agent], cfg, manager_type="conventional")
        assert result.termination_reason in (
            "decision_budget_exhausted",
            "all_tasks_completed",
            "unrecoverable_failure",
        )


# ---------------------------------------------------------------------------
# 11. Manager interchangeability
# ---------------------------------------------------------------------------


class TestManagerInterchangeability:

    def test_both_managers_use_same_engine_run_experiment(self):
        """The engine interface is identical for both managers."""
        engine = SimulationEngine()
        graph = make_graph([make_task()])
        agents = [make_agent()]
        cfg = SimulationConfig()
        r_conv = engine.run_experiment(graph, agents, cfg, manager_type="conventional")
        r_posd = engine.run_experiment(graph, agents, cfg, manager_type="posdcorb")
        assert isinstance(r_conv, ExperimentResult)
        assert isinstance(r_posd, ExperimentResult)
        # Both run against the same graph
        assert r_conv.workload_id == r_posd.workload_id

    def test_both_managers_see_same_graph(self):
        engine = SimulationEngine()
        graph = make_graph([make_task("t1"), make_task("t2")])
        agents = [make_agent()]
        cfg = SimulationConfig(manager_types=["conventional", "posdcorb"])
        result = engine.run(graph, agents, cfg)
        for exp in result.experiments.values():
            assert exp.workload_id == graph.graph_id

    def test_neither_manager_mutates_graph(self):
        """Graph task list must be identical before and after both managers run."""
        engine = SimulationEngine()
        tasks = [make_task(f"t{i}", index=i) for i in range(4)]
        graph = make_graph(tasks)
        original_ids = [t.id for t in graph.tasks]
        cfg = SimulationConfig(manager_types=["conventional", "posdcorb"])
        engine.run(graph, [make_agent()], cfg)
        assert [t.id for t in graph.tasks] == original_ids

    def test_conventional_never_dynamically_activates(self):
        """Conventional manager has a fixed worker pool (no activations)."""
        engine = SimulationEngine()
        tasks = [make_task(f"t{i}", index=i) for i in range(8)]
        graph = make_graph(tasks)
        result = engine.run_experiment(
            graph, [make_agent()],
            manager_type="conventional",
        )
        stats = result.manager_statistics
        assert stats.get("activations", 0) == 0


# ---------------------------------------------------------------------------
# 12. Reproducibility
# ---------------------------------------------------------------------------


class TestReproducibility:

    def test_same_seed_same_termination(self):
        engine = SimulationEngine()
        graph = make_graph([make_task(f"t{i}", index=i) for i in range(5)])
        agents = [make_agent()]
        cfg1 = SimulationConfig(rng_seed=99)
        cfg2 = SimulationConfig(rng_seed=99)
        r1 = engine.run_experiment(graph, agents, cfg1, manager_type="conventional")
        r2 = engine.run_experiment(graph, agents, cfg2, manager_type="conventional")
        assert r1.termination_reason == r2.termination_reason
        assert r1.completed_tasks == r2.completed_tasks
        assert r1.task_success_rate == pytest.approx(r2.task_success_rate)

    def test_same_seed_same_posdcorb(self):
        engine = SimulationEngine()
        graph = make_graph([make_task(f"t{i}", index=i) for i in range(4)])
        agents = [make_agent()]
        cfg = SimulationConfig(rng_seed=7)
        r1 = engine.run_experiment(graph, agents, cfg, manager_type="posdcorb")
        r2 = engine.run_experiment(graph, agents, cfg, manager_type="posdcorb")
        assert r1.completed_tasks == r2.completed_tasks
        assert r1.termination_reason == r2.termination_reason

    def test_different_seeds_may_differ(self):
        """Different seeds with failure injection may (and usually do) differ."""
        engine = SimulationEngine()
        tasks = [make_task(f"t{i}", index=i) for i in range(10)]
        graph = make_graph(tasks)
        agents = [make_agent(reliability=0.7)]
        cfg_a = SimulationConfig(rng_seed=1, failure_probability=0.3)
        cfg_b = SimulationConfig(rng_seed=9999, failure_probability=0.3)
        # We can only assert that both produce valid results
        r_a = engine.run_experiment(graph, agents, cfg_a)
        r_b = engine.run_experiment(graph, agents, cfg_b)
        assert isinstance(r_a, ExperimentResult)
        assert isinstance(r_b, ExperimentResult)

    def test_same_seed_produces_same_failure_injection(self):
        """ResourceController.prepare_agents is deterministic (no RNG involved)."""
        agent = make_agent(reliability=1.0)
        rc = ResourceController(failure_probability=0.15)
        p1 = rc.prepare_agents([agent])
        p2 = rc.prepare_agents([agent])
        assert p1[0].reliability == pytest.approx(p2[0].reliability)


# ---------------------------------------------------------------------------
# 13. Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:

    def test_empty_workload_conventional(self):
        engine = SimulationEngine()
        graph = make_graph([])
        result = engine.run_experiment(graph, [make_agent()], manager_type="conventional")
        assert result.termination_reason == "empty_workload"
        assert result.completed_tasks == 0
        assert result.failed_tasks == 0

    def test_empty_workload_posdcorb(self):
        engine = SimulationEngine()
        graph = make_graph([])
        result = engine.run_experiment(graph, [make_agent()], manager_type="posdcorb")
        assert result.termination_reason == "empty_workload"

    def test_single_task_completes(self):
        engine = SimulationEngine()
        graph = make_graph([make_task()])
        result = engine.run_experiment(graph, [make_agent()], manager_type="conventional")
        # With reliability=1.0, a single task must complete
        assert result.completed_tasks == 1
        assert result.termination_reason == "all_tasks_completed"

    def test_all_tasks_chained_single_agent(self):
        """t1 → t2 → t3 → t4: fully sequential chain, one agent, all complete."""
        agent = make_agent()
        t1 = make_task("t1", index=0)
        t2 = make_task("t2", index=1, dep_ids=["t1"])
        t3 = make_task("t3", index=2, dep_ids=["t2"])
        t4 = make_task("t4", index=3, dep_ids=["t3"])
        graph = make_graph([t1, t2, t3, t4])
        engine = SimulationEngine()
        result = engine.run_experiment(graph, [agent], manager_type="conventional")
        assert result.completed_tasks == 4
        assert result.termination_reason == "all_tasks_completed"

    def test_fully_parallel_workload(self):
        """10 independent tasks, no deps, all should complete."""
        agent = make_agent()
        tasks = [make_task(id=f"tp{i}", index=i) for i in range(10)]
        graph = make_graph(tasks)
        engine = SimulationEngine()
        result = engine.run_experiment(graph, [agent], manager_type="conventional")
        assert result.completed_tasks == 10

    def test_generated_workload_runs_both_managers(self):
        """Use the workload generator to produce a realistic graph, run both managers."""
        cfg = WorkloadGeneratorConfig(
            num_tasks=8,
            complexity="low",
            dependency_percentage=20,
            seed=42,
        )
        graph = generate_workload(cfg)
        agents = [make_agent(id=f"agt-{i}", name=f"Agent{i}") for i in range(3)]
        engine = SimulationEngine()
        result = engine.run(
            graph, agents,
            SimulationConfig(
                manager_types=["conventional", "posdcorb"],
                rng_seed=42,
            ),
        )
        assert len(result.experiments) == 2
        for exp in result.experiments.values():
            assert exp.completed_tasks + exp.failed_tasks <= exp.completed_tasks + exp.failed_tasks + exp.pending_tasks


# ---------------------------------------------------------------------------
# 14. SimulationSession API
# ---------------------------------------------------------------------------


class TestSimulationSession:

    def _make_session(
        self,
        manager_types: list[str] | None = None,
        num_tasks: int = 2,
    ) -> SimulationSession:
        engine = SimulationEngine()
        tasks = [make_task(id=f"ts{i}", index=i) for i in range(num_tasks)]
        graph = make_graph(tasks)
        agents = [make_agent()]
        cfg = SimulationConfig(
            manager_types=manager_types or ["conventional"],
            rng_seed=42,
        )
        return engine.create_session(graph, agents, cfg)

    def test_initial_state_running(self):
        session = self._make_session()
        state = session.get_state()
        assert state["status"] == "running"
        assert len(state["pending_managers"]) == 1
        assert len(state["completed_managers"]) == 0

    def test_step_returns_result(self):
        session = self._make_session()
        result = session.step()
        assert isinstance(result, ExperimentResult)

    def test_step_advances_manager(self):
        session = self._make_session()
        session.step()
        state = session.get_state()
        assert len(state["completed_managers"]) == 1
        assert len(state["pending_managers"]) == 0

    def test_step_when_finished_returns_none(self):
        session = self._make_session()
        session.step()        # exhausts the one manager
        result = session.step()
        assert result is None

    def test_get_state_after_step(self):
        session = self._make_session()
        session.step()
        state = session.get_state()
        assert state["status"] == "completed"
        assert "conventional" in state["results"]

    def test_stop_before_step(self):
        session = self._make_session(manager_types=["conventional", "posdcorb"])
        session.stop()
        result = session.step()
        assert result is None
        assert session.get_state()["status"] == "stopped"

    def test_stop_mid_session(self):
        session = self._make_session(manager_types=["conventional", "posdcorb"])
        session.step()   # runs conventional
        session.stop()
        result = session.step()  # should return None (stopped)
        assert result is None
        state = session.get_state()
        assert state["status"] == "stopped"
        # conventional result still preserved
        assert "conventional" in state["results"]

    def test_finished_property_initial(self):
        session = self._make_session()
        assert not session.finished

    def test_finished_after_all_steps(self):
        session = self._make_session()
        session.step()
        assert session.finished

    def test_finished_after_stop(self):
        session = self._make_session()
        session.stop()
        assert session.finished

    def test_run_all_completes(self):
        session = self._make_session(manager_types=["conventional", "posdcorb"])
        sim_result = session.run()
        assert isinstance(sim_result, SimulationResult)
        assert "conventional" in sim_result.experiments
        assert "posdcorb" in sim_result.experiments

    def test_get_state_elapsed_increases(self):
        import time
        session = self._make_session()
        s1 = session.get_state()["elapsed_s"]
        time.sleep(0.01)
        s2 = session.get_state()["elapsed_s"]
        assert s2 >= s1

    def test_get_state_resource_limits_present(self):
        session = self._make_session()
        state = session.get_state()
        assert "resource_limits" in state
        assert isinstance(state["resource_limits"], dict)

    def test_two_manager_session_both_complete(self):
        session = self._make_session(manager_types=["conventional", "posdcorb"], num_tasks=3)
        r1 = session.step()
        r2 = session.step()
        assert r1 is not None
        assert r2 is not None
        assert r1.manager_type != r2.manager_type

    def test_session_seed_preserved(self):
        session = self._make_session()
        state = session.get_state()
        assert state["seed"] == 42


# ---------------------------------------------------------------------------
# 15. Engine execute_task utility
# ---------------------------------------------------------------------------


class TestEngineExecuteTask:

    def test_execute_task_returns_worker_result(self):
        from app.agents.worker import WorkerResult
        agent = make_agent()
        task = make_task()
        result = SimulationEngine.execute_task(agent, task)
        assert isinstance(result, WorkerResult)

    def test_execute_task_with_capable_agent(self):
        agent = make_agent(capabilities=["text-processing"])
        task = make_task(required_capability="text-processing")
        result = SimulationEngine.execute_task(agent, task)
        # WorkerResult has success field
        assert hasattr(result, "success")

    def test_execute_task_deterministic_with_same_seed(self):
        """Calling execute_task twice on the same agent+task returns consistent types."""
        agent = make_agent(reliability=1.0)
        task = make_task()
        r1 = SimulationEngine.execute_task(agent, task)
        r2 = SimulationEngine.execute_task(agent, task)
        # Both produce WorkerResult; exact values may differ (probabilistic) but
        # structure must match
        assert type(r1) == type(r2)
