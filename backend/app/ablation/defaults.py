"""
Deterministic fallback agent pool for ablation studies.

Why this exists
---------------
The ablation study must be runnable without any prior database setup: the
comparison is only meaningful when *every* configuration receives the exact
same workers.  This module defines a fixed pool — four initial agents and four
dynamic reserve agents — that covers all seven task capabilities used by the
workload generator.

The pool is a pure function of the constants below: no timestamps, no random
values, no database lookups.  That makes the "all other conditions identical"
guarantee auditable.
"""
from __future__ import annotations

import copy

from app.models.agent import Agent

# Fixed timestamp so serialised agents are byte-identical across runs.
_FIXED_CREATED_AT = "2026-01-01T00:00:00+00:00"

# Capabilities required by TASK_TYPE_CAPABILITY (backend.models.workload):
#   text-processing, batch-execution, statistics, data-visualization,
#   anomaly-detection, quality-check, text-summarization
INITIAL_AGENT_SPECS: tuple[dict, ...] = (
    {
        "id": "agt-ablation-001",
        "name": "Ablation Researcher",
        "role": "Researcher",
        "capabilities": ["text-processing", "text-summarization", "quality-check"],
        "processing_speed": 1.0,
        "reliability": 0.95,
        "max_concurrent_tasks": 2,
    },
    {
        "id": "agt-ablation-002",
        "name": "Ablation Analyst",
        "role": "Analyst",
        "capabilities": ["statistics", "data-visualization"],
        "processing_speed": 1.0,
        "reliability": 0.95,
        "max_concurrent_tasks": 2,
    },
    {
        "id": "agt-ablation-003",
        "name": "Ablation Processor",
        "role": "Processor",
        "capabilities": ["batch-execution", "text-processing"],
        "processing_speed": 1.0,
        "reliability": 0.95,
        "max_concurrent_tasks": 2,
    },
    {
        "id": "agt-ablation-004",
        "name": "Ablation Validator",
        "role": "Validator",
        "capabilities": ["quality-check", "anomaly-detection"],
        "processing_speed": 1.0,
        "reliability": 0.95,
        "max_concurrent_tasks": 2,
    },
)

DYNAMIC_AGENT_SPECS: tuple[dict, ...] = (
    {
        "id": "agt-ablation-005",
        "name": "Reserve Researcher",
        "role": "Researcher",
        "capabilities": ["text-processing", "text-summarization"],
        "processing_speed": 1.0,
        "reliability": 0.95,
        "max_concurrent_tasks": 2,
    },
    {
        "id": "agt-ablation-006",
        "name": "Reserve Analyst",
        "role": "Analyst",
        "capabilities": ["statistics", "data-visualization", "anomaly-detection"],
        "processing_speed": 1.0,
        "reliability": 0.95,
        "max_concurrent_tasks": 2,
    },
    {
        "id": "agt-ablation-007",
        "name": "Reserve Processor",
        "role": "Processor",
        "capabilities": ["batch-execution", "text-processing"],
        "processing_speed": 1.0,
        "reliability": 0.95,
        "max_concurrent_tasks": 2,
    },
    {
        "id": "agt-ablation-008",
        "name": "Reserve Validator",
        "role": "Validator",
        "capabilities": ["quality-check", "anomaly-detection"],
        "processing_speed": 1.0,
        "reliability": 0.95,
        "max_concurrent_tasks": 2,
    },
)


def _build(spec: dict, origin: str) -> Agent:
    return Agent(
        id=spec["id"],
        name=spec["name"],
        role=spec["role"],
        capabilities=list(spec["capabilities"]),
        processing_speed=spec["processing_speed"],
        reliability=spec["reliability"],
        max_concurrent_tasks=spec["max_concurrent_tasks"],
        origin=origin,  # type: ignore[arg-type]
        created_at=_FIXED_CREATED_AT,
    )


def default_agents() -> list[Agent]:
    """Return a fresh copy of the deterministic 4 + 4 agent pool."""
    agents = [
        _build(spec, "initial") for spec in INITIAL_AGENT_SPECS
    ] + [
        _build(spec, "dynamic") for spec in DYNAMIC_AGENT_SPECS
    ]
    return [a.model_copy(deep=True) for a in agents]


def clone_agents(agents: list[Agent]) -> list[Agent]:
    """Deep copy a pool so callers can never mutate shared state."""
    return [copy.deepcopy(a) for a in agents]
