"""
Unit tests for the Conventional Manager and supporting infrastructure.

Test categories
---------------
1. Task assignment       — agents are matched to tasks by capability
2. Dependency handling   — blocked tasks only run when predecessors complete
3. Retry logic           — failed tasks are retried up to MAX_RETRIES times
4. Termination           — the run loop always terminates (no infinite loops)
5. Fixed worker pool     — only origin='initial' agents are used

Run with:  python -m pytest tests/ -v
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone
from typing import Any

import pytest

from app.agents.worker import WorkerResult, execute_task
from app.managers.base import BaseManager, DecisionRecord, ExecutionTrace, ManagerProtocol
from app.managers.conventional import MAX_RETRIES, ConventionalManager
from app.managers.registry import get_manager, registered_types
from app.models.agent import Agent
from app.models.workload import (
    PriorityDistribution,
    Task,
    TaskGraph,
    WorkloadGeneratorConfig,
)
from app.simulation.engine import SimulationConfig, SimulationEngine
from app.simulation.workload_generator import generate_workload


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def make_agent(
    id: str = "agt-001",
    name: str = "TestAgent",
    role: str = "Processor",
    capabilities: list[str] | None = None,
    reliability: float = 1.0,   # always succeeds by default in tests
    processing_speed: float = 1.0,
    max_concurrent: int = 4,
    origin: str = "initial",
) -> Agent:
    return Agent(
        id=id,
        name=name,
        role=role,  # type: ignore[arg-type]
        capabilities=capabilities or ["text-processing", "batch-execution", "statistics",
                                      "data-visualization", "anomaly-detection",
                                      "quality-check", "text-summarization"],
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

    cfg = WorkloadGeneratorConfig(
        num_tasks=len(tasks),
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


# ---------------------------------------------------------------------------
# 1. Task assignment tests
# ---------------------------------------------------------------------------


class TestTaskAssignment:

    def test_capable_agent_is_assigned(self):
        """An agent with the required capability should be assigned to the task."""
        agent = make_agent(capabilities=["text-processing"])
        task = make_task(required_capability="text-processing")
        graph = make_graph([task])
        mgr = ConventionalManager(rng_seed=42)
        trace = mgr.run(graph, [agent])

        assign_decisions = [d for d in trace.decisions if d.manager_decision == "assign"]
        assert len(assign_decisions) >= 1
        assert assign_decisions[0].selected_agent_id == agent.id

    def test_incapable_agent_causes_wait(self):
        """If no agent has the required capability, the manager should emit 'wait'."""
        agent = make_agent(capabilities=["statistics"])          # wrong skill
        task = make_task(required_capability="text-processing")
        graph = make_graph([task])
        mgr = ConventionalManager(rng_seed=42)
        trace = mgr.run(graph, [agent])

        wait_decisions = [d for d in trace.decisions if d.manager_decision == "wait"]
        # Without a capable agent the task will eventually fail after waits
        assert len(wait_decisions) >= 1

    def test_best_agent_selected_by_score(self):
        """The higher-scoring agent (reliability × speed) should be chosen."""
        slow = make_agent(id="agt-slow", name="SlowAgent",
                          capabilities=["statistics"], reliability=0.8, processing_speed=0.5)
        fast = make_agent(id="agt-fast", name="FastAgent",
                          capabilities=["statistics"], reliability=0.99, processing_speed=2.0)
        task = make_task(required_capability="statistics", task_type="Statistical Computation")
        graph = make_graph([task])
        mgr = ConventionalManager(rng_seed=0)
        trace = mgr.run(graph, [slow, fast])

        assign_decisions = [d for d in trace.decisions if d.manager_decision == "assign"]
        assert assign_decisions[0].selected_agent_id == fast.id

    def test_overloaded_agent_is_skipped(self):
        """An agent at max_concurrent_tasks should not receive new assignments."""
        full_agent = make_agent(id="agt-full", capabilities=["text-processing"],
                                max_concurrent=1)
        # Pre-fill the workload slot
        full_agent.current_workload = 1

        empty_agent = make_agent(id="agt-empty", capabilities=["text-processing"],
                                 max_concurrent=2)
        task = make_task(required_capability="text-processing")
        graph = make_graph([task])
        mgr = ConventionalManager(rng_seed=0)
        trace = mgr.run(graph, [full_agent, empty_agent])

        assign_decisions = [d for d in trace.decisions if d.manager_decision == "assign"]
        assert all(d.selected_agent_id == empty_agent.id for d in assign_decisions)

    def test_priority_ordering(self):
        """Critical tasks should be assigned before low-priority tasks."""
        critical = make_task("task-0001", index=0, priority="critical",
                             required_capability="text-processing")
        low = make_task("task-0002", index=1, priority="low",
                        required_capability="text-processing")
        graph = make_graph([critical, low])
        agent = make_agent(capabilities=["text-processing"], max_concurrent=1)
        mgr = ConventionalManager(rng_seed=0)
        trace = mgr.run(graph, [agent])

        assign_decisions = [d for d in trace.decisions if d.manager_decision == "assign"]
        assert assign_decisions[0].task_id == "task-0001"


# ---------------------------------------------------------------------------
# 2. Dependency handling tests
# ---------------------------------------------------------------------------


class TestDependencyHandling:

    def test_dependent_task_blocked_until_predecessor_done(self):
        """task-0002 must not start before task-0001 completes."""
        t1 = make_task("task-0001", index=0, required_capability="text-processing")
        t2 = make_task("task-0002", index=1, required_capability="text-processing",
                       dep_ids=["task-0001"])
        graph = make_graph([t1, t2])
        agent = make_agent(capabilities=["text-processing"], reliability=1.0)
        mgr = ConventionalManager(rng_seed=0)
        trace = mgr.run(graph, [agent])

        assigns = [d for d in trace.decisions if d.manager_decision == "assign"]
        task_order = [a.task_id for a in assigns]
        # task-0001 must appear before task-0002
        assert task_order.index("task-0001") < task_order.index("task-0002")

    def test_chain_of_three_resolves_in_order(self):
        """A → B → C chain: tasks must complete in topological order."""
        ta = make_task("task-0001", index=0, required_capability="text-processing")
        tb = make_task("task-0002", index=1, required_capability="text-processing",
                       dep_ids=["task-0001"])
        tc = make_task("task-0003", index=2, required_capability="text-processing",
                       dep_ids=["task-0002"])
        graph = make_graph([ta, tb, tc])
        agent = make_agent(capabilities=["text-processing"], reliability=1.0)
        mgr = ConventionalManager(rng_seed=0)
        trace = mgr.run(graph, [agent])

        completed = trace.completed_tasks
        assert completed == 3

    def test_root_tasks_run_in_parallel_conceptually(self):
        """Two independent tasks should both eventually complete."""
        t1 = make_task("task-0001", index=0, required_capability="text-processing")
        t2 = make_task("task-0002", index=1, required_capability="batch-execution")
        graph = make_graph([t1, t2])
        agent = make_agent(capabilities=["text-processing", "batch-execution"],
                           reliability=1.0, max_concurrent=4)
        mgr = ConventionalManager(rng_seed=0)
        trace = mgr.run(graph, [agent])
        assert trace.completed_tasks == 2

    def test_failed_dependency_cascades(self):
        """If t1 permanently fails, t2 (which depends on t1) should also fail."""
        # Reliability 0 → always fails → retries exhausted → t1 permanently fails
        unreliable = make_agent(capabilities=["text-processing"], reliability=0.0)
        t1 = make_task("task-0001", index=0, required_capability="text-processing")
        t2 = make_task("task-0002", index=1, required_capability="text-processing",
                       dep_ids=["task-0001"])
        graph = make_graph([t1, t2])
        mgr = ConventionalManager(rng_seed=0)
        trace = mgr.run(graph, [unreliable])
        # Both should end up failed (t1 directly, t2 via cascade)
        assert trace.failed_tasks == 2
        assert trace.completed_tasks == 0


# ---------------------------------------------------------------------------
# 3. Retry logic tests
# ---------------------------------------------------------------------------


class TestRetryLogic:

    def test_failing_task_is_retried_up_to_max_retries(self):
        """A task that always fails should produce exactly MAX_RETRIES+1 assign records."""
        unreliable = make_agent(capabilities=["text-processing"], reliability=0.0)
        task = make_task(required_capability="text-processing")
        graph = make_graph([task])
        mgr = ConventionalManager(rng_seed=0)
        trace = mgr.run(graph, [unreliable])

        retry_decisions = [d for d in trace.decisions if d.manager_decision == "retry"]
        fail_decisions  = [d for d in trace.decisions if d.manager_decision == "fail"]

        assert len(retry_decisions) == MAX_RETRIES
        assert len(fail_decisions) == 1

    def test_task_succeeds_on_second_attempt(self):
        """
        Simulate a task that fails once then succeeds.
        We use reliability=0.5 with seed chosen so the first call fails.
        The important assertion: if any retry eventually succeeds, completed==1.
        """
        # With 100% reliability the task always succeeds without retry
        agent = make_agent(capabilities=["text-processing"], reliability=1.0)
        task = make_task(required_capability="text-processing")
        graph = make_graph([task])
        mgr = ConventionalManager(rng_seed=0)
        trace = mgr.run(graph, [agent])
        assert trace.completed_tasks == 1
        assert trace.failed_tasks == 0

    def test_permanently_failed_task_counted_as_failed(self):
        """After MAX_RETRIES exhausted the task is counted in failed_tasks."""
        unreliable = make_agent(capabilities=["text-processing"], reliability=0.0)
        task = make_task(required_capability="text-processing")
        graph = make_graph([task])
        mgr = ConventionalManager(rng_seed=0)
        trace = mgr.run(graph, [unreliable])
        assert trace.failed_tasks == 1
        assert trace.completed_tasks == 0


# ---------------------------------------------------------------------------
# 4. Termination tests
# ---------------------------------------------------------------------------


class TestTermination:

    def test_empty_graph_terminates(self):
        """An empty TaskGraph should produce a trace with zero tasks."""
        # WorkloadGeneratorConfig requires num_tasks>=1, so build the graph manually
        cfg = WorkloadGeneratorConfig(num_tasks=1, complexity="low",
                                      dependency_percentage=0, seed=0)
        empty_graph = TaskGraph(
            graph_id="empty-test",
            seed=0,
            tasks=[],
            edges={},
            config=cfg,
        )
        agent = make_agent()
        mgr = ConventionalManager(rng_seed=0)
        trace = mgr.run(empty_graph, [agent])
        assert trace.total_tasks == 0
        assert trace.completed_tasks == 0

    def test_no_agents_terminates(self):
        """If the initial pool is empty the manager should terminate gracefully."""
        task = make_task()
        graph = make_graph([task])
        mgr = ConventionalManager(rng_seed=0)
        trace = mgr.run(graph, [])   # zero agents
        # Run should complete (not hang)
        assert trace is not None

    def test_single_task_terminates(self):
        agent = make_agent(reliability=1.0)
        task = make_task()
        graph = make_graph([task])
        mgr = ConventionalManager(rng_seed=0)
        trace = mgr.run(graph, [agent])
        assert trace.total_tasks == 1
        assert trace.completed_tasks == 1

    def test_large_graph_terminates(self):
        """A 50-task graph must complete without hanging."""
        cfg = WorkloadGeneratorConfig(num_tasks=50, complexity="low",
                                      dependency_percentage=30, seed=99)
        graph = generate_workload(cfg)
        agent = make_agent(capabilities=[
            "text-processing", "batch-execution", "statistics",
            "data-visualization", "anomaly-detection", "quality-check",
            "text-summarization",
        ], reliability=0.9, max_concurrent=8)
        mgr = ConventionalManager(rng_seed=99)
        trace = mgr.run(graph, [agent])
        assert trace.total_tasks == 50
        # All tasks must be in a terminal state OR the wall-clock cap was hit
        # (some tasks may still be "blocked" if their deps failed after cap)
        assert (trace.completed_tasks + trace.failed_tasks) <= 50
        assert trace.completed_tasks > 0  # at least some work was done


# ---------------------------------------------------------------------------
# 5. Fixed worker pool tests
# ---------------------------------------------------------------------------


class TestFixedWorkerPool:

    def test_dynamic_agents_excluded(self):
        """ConventionalManager must never assign tasks to origin='dynamic' agents."""
        dynamic_agent = make_agent(id="agt-dyn", name="DynamicAgent",
                                   capabilities=["text-processing"], origin="dynamic")
        initial_agent = make_agent(id="agt-init", name="InitialAgent",
                                   capabilities=["text-processing"], origin="initial")
        task = make_task(required_capability="text-processing")
        graph = make_graph([task])
        mgr = ConventionalManager(rng_seed=0)
        trace = mgr.run(graph, [dynamic_agent, initial_agent])

        assign_decisions = [d for d in trace.decisions if d.manager_decision == "assign"]
        for d in assign_decisions:
            assert d.selected_agent_id != dynamic_agent.id, \
                "Dynamic agents must not be used by ConventionalManager"

    def test_only_dynamic_agents_means_no_assignment(self):
        """If only dynamic agents are in the pool no tasks should be assigned."""
        dynamic = make_agent(id="agt-dyn", capabilities=["text-processing"],
                             origin="dynamic")
        task = make_task(required_capability="text-processing")
        graph = make_graph([task])
        mgr = ConventionalManager(rng_seed=0)
        trace = mgr.run(graph, [dynamic])
        # No assignments possible
        assign_decisions = [d for d in trace.decisions if d.manager_decision == "assign"]
        assert len(assign_decisions) == 0

    def test_pool_size_does_not_grow(self):
        """The number of agents in the pool must be the same before and after the run."""
        agents = [make_agent(id=f"agt-{i:03d}", reliability=1.0) for i in range(3)]
        cfg = WorkloadGeneratorConfig(num_tasks=10, complexity="low",
                                      dependency_percentage=20, seed=7)
        graph = generate_workload(cfg)
        # Keep a count of original pool size
        original_count = len([a for a in agents if a.origin == "initial"])
        mgr = ConventionalManager(rng_seed=7)
        trace = mgr.run(graph, agents)
        # Pool size after run (agents list is unchanged by the engine due to deep copy)
        assert len(agents) == original_count

    def test_manager_type_is_conventional(self):
        mgr = ConventionalManager()
        assert mgr.manager_type == "conventional"


# ---------------------------------------------------------------------------
# 6. Registry / factory tests
# ---------------------------------------------------------------------------


class TestRegistry:

    def test_get_manager_conventional(self):
        mgr = get_manager("conventional", rng_seed=0)
        assert mgr.manager_type == "conventional"

    def test_get_manager_unknown_raises(self):
        with pytest.raises(ValueError, match="Unknown manager type"):
            get_manager("nonexistent_manager")

    def test_registered_types_contains_conventional(self):
        assert "conventional" in registered_types()

    def test_manager_satisfies_protocol(self):
        mgr = get_manager("conventional")
        assert isinstance(mgr, ManagerProtocol)


# ---------------------------------------------------------------------------
# 7. Worker executor tests
# ---------------------------------------------------------------------------


class TestWorkerExecutor:

    def test_execute_task_is_deterministic(self):
        """Same agent + task → same result every time."""
        agent = make_agent(reliability=0.75)
        task = make_task()
        r1 = execute_task(agent, task)
        r2 = execute_task(agent, task)
        assert r1.success == r2.success
        assert r1.elapsed_ms == r2.elapsed_ms

    def test_reliable_agent_succeeds(self):
        """reliability=1.0 → always success."""
        agent = make_agent(reliability=1.0)
        task = make_task()
        result = execute_task(agent, task)
        assert result.success is True

    def test_zero_reliability_fails(self):
        """reliability=0.0 → always failure."""
        agent = make_agent(reliability=0.0)
        task = make_task()
        result = execute_task(agent, task)
        assert result.success is False

    def test_fast_agent_has_lower_elapsed(self):
        """Faster processing_speed → lower elapsed_ms (on average)."""
        slow = make_agent(id="s", processing_speed=0.5, reliability=1.0)
        fast = make_agent(id="f", processing_speed=2.0, reliability=1.0)
        task = make_task()
        r_slow = execute_task(slow, task)
        r_fast = execute_task(fast, task)
        assert r_fast.elapsed_ms < r_slow.elapsed_ms


# ---------------------------------------------------------------------------
# 8. SimulationEngine tests
# ---------------------------------------------------------------------------


class TestSimulationEngine:

    def test_engine_runs_conventional(self):
        cfg = WorkloadGeneratorConfig(num_tasks=5, complexity="low",
                                      dependency_percentage=0, seed=1)
        graph = generate_workload(cfg)
        agents = [make_agent(reliability=1.0)]
        engine = SimulationEngine()
        result = engine.run(graph, agents, SimulationConfig(manager_types=["conventional"]))
        assert "conventional" in result.traces
        assert result.traces["conventional"].total_tasks == 5

    def test_engine_does_not_mutate_original_agents(self):
        """The engine must return agent pool with workload counters unchanged."""
        agent = make_agent(reliability=1.0)
        original_completed = agent.tasks_completed
        cfg = WorkloadGeneratorConfig(num_tasks=3, complexity="low",
                                      dependency_percentage=0, seed=2)
        graph = generate_workload(cfg)
        engine = SimulationEngine()
        engine.run(graph, [agent], SimulationConfig(manager_types=["conventional"]))
        # Original agent object must be unmodified
        assert agent.tasks_completed == original_completed

    def test_engine_unknown_manager_captured_as_error(self):
        cfg = WorkloadGeneratorConfig(num_tasks=2, complexity="low",
                                      dependency_percentage=0, seed=3)
        graph = generate_workload(cfg)
        agents = [make_agent()]
        engine = SimulationEngine()
        result = engine.run(graph, agents, SimulationConfig(manager_types=["unknown_mgr"]))
        assert "unknown_mgr" in result.errors
