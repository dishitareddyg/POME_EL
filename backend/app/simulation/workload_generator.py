"""
Deterministic workload generator for the backend.

This is **not** a port of the frontend ``workloadGenerator.ts``.  The backend
uses Python's ``random.Random`` (Mersenne Twister) while the frontend uses a
hand-rolled LCG, so independently regenerating a graph from the same seed does
*not* produce the same tasks.  Exact workload parity is obtained only when the
frontend generates the graph once and sends the resulting TaskGraph payload to
the backend (which /api/simulate supports).

Within a single backend process the generator is fully deterministic: the same
``WorkloadGeneratorConfig`` — seed, task count, complexity, dependency
percentage and priority distribution — always yields the same graph.

This module is used:
  - In unit tests (to generate graphs without a frontend).
  - In /api/simulate and the batch/ablation runners when a raw config is sent.
"""
from __future__ import annotations

import random
from datetime import datetime, timezone

from app.models.workload import (
    BASE_WORKLOAD_MS,
    TASK_TYPE_CAPABILITY,
    PriorityDistribution,
    Task,
    TaskGraph,
    WorkloadGeneratorConfig,
)

TASK_TYPES: list[str] = [
    "Data Filtering",
    "Sorting",
    "Statistical Computation",
    "Aggregation",
    "Anomaly Detection",
    "Validation",
    "Summarization",
]


def _sample_priority(rng: random.Random, dist: PriorityDistribution) -> str:
    roll = rng.random() * 100
    if roll < dist.critical:
        return "critical"
    if roll < dist.critical + dist.high:
        return "high"
    if roll < dist.critical + dist.high + dist.medium:
        return "medium"
    return "low"


def _fingerprint(cfg: WorkloadGeneratorConfig) -> str:
    """
    Identity of the generated workload.

    Includes every input that changes task composition, not just seed/count:
    the previous fingerprint ignored the priority distribution and rounded the
    dependency percentage, so two materially different graphs could share an
    ID and be mistaken for the same workload in analysis output.
    """
    dist = cfg.priority_distribution
    priorities = f"p{dist.critical}_{dist.high}_{dist.medium}_{dist.low}"
    deps = f"{cfg.dependency_percentage:g}"
    return (
        f"g-{cfg.seed}-n{cfg.num_tasks}-d{deps}-{cfg.complexity}-{priorities}"
    )


def generate_workload(config: WorkloadGeneratorConfig) -> TaskGraph:
    """
    Generate a deterministic TaskGraph from the given config.

    The graph is acyclic by construction: each task can only depend on tasks
    with a strictly lower index.
    """
    rng = random.Random(config.seed)
    tasks: list[Task] = []

    for i in range(config.num_tasks):
        task_id = f"task-{i + 1:04d}"
        task_type: str = rng.choice(TASK_TYPES)
        priority = _sample_priority(rng, config.priority_distribution)

        # ± 20% jitter on base workload
        base_ms = BASE_WORKLOAD_MS[config.complexity][task_type]
        estimated_ms = round(base_ms * rng.uniform(0.8, 1.2))

        # Dependency sampling
        dependency_ids: list[str] = []
        if i > 0 and rng.random() * 100 < config.dependency_percentage:
            max_deps = min(i, 3)
            num_deps = rng.randint(1, max_deps)
            pred_indices = rng.sample(range(i), min(num_deps, i))
            dependency_ids = [tasks[pi].id for pi in pred_indices]

        status = "ready" if not dependency_ids else "blocked"
        tasks.append(
            Task(
                id=task_id,
                index=i,
                task_type=task_type,  # type: ignore[arg-type]
                complexity=config.complexity,
                required_capability=TASK_TYPE_CAPABILITY[task_type],
                priority=priority,  # type: ignore[arg-type]
                estimated_workload_ms=estimated_ms,
                dependency_ids=dependency_ids,
                status=status,  # type: ignore[arg-type]
            )
        )

    # Build forward adjacency list
    edges: dict[str, list[str]] = {t.id: [] for t in tasks}
    for task in tasks:
        for dep_id in task.dependency_ids:
            edges.setdefault(dep_id, []).append(task.id)

    return TaskGraph(
        graph_id=_fingerprint(config),
        seed=config.seed,
        tasks=tasks,
        edges=edges,
        generated_at=datetime.now(timezone.utc).isoformat(),
        config=config,
    )
