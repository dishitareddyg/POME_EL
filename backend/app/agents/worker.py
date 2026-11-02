"""
Deterministic rule-based worker executor.

Purpose
-------
Replaces expensive per-task LLM inference with a fast, deterministic
simulation of task execution.  The goal of the simulator is to evaluate
MANAGEMENT and ORCHESTRATION strategies — not the quality of individual
worker inference.

Worker execution model
----------------------
1. Sample success/failure from the agent's reliability probability using the
   task's own RNG seed (derived from agent-id + task-id → deterministic).
2. Simulate elapsed "wall time" from estimated_workload_ms / processing_speed
   (+ ±10% jitter, seeded deterministically).
3. Return a WorkerResult with full metadata.

This module has no side-effects and is thread/async-safe.
"""
from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.models.agent import Agent
from app.models.workload import Task


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------


@dataclass
class WorkerResult:
    """Outcome of one agent executing one task."""

    task_id: str
    agent_id: str
    agent_name: str
    success: bool
    elapsed_ms: float          # simulated wall time consumed
    output_summary: str        # short human-readable description of outcome
    metadata: dict[str, Any]   # free-form extra data (capability used, etc.)

    @property
    def result_label(self) -> str:
        return "success" if self.success else "failure"


# ---------------------------------------------------------------------------
# Task-type specific "processing" descriptions
# ---------------------------------------------------------------------------

_SUCCESS_DESCRIPTIONS: dict[str, str] = {
    "Data Filtering":          "Filtered {n} records; retained {k} matching entries.",
    "Sorting":                 "Sorted {n} items using merge-sort in {t} ms.",
    "Statistical Computation": "Computed mean={m:.2f}, std={s:.2f} over {n} samples.",
    "Aggregation":             "Aggregated {n} groups; produced {k} output buckets.",
    "Anomaly Detection":       "Scanned {n} data points; flagged {k} anomalies.",
    "Validation":              "Validated {n} records; {k} passed schema checks.",
    "Summarization":           "Summarized {n} items into {k}-sentence abstract.",
}

_FAILURE_DESCRIPTIONS: dict[str, str] = {
    "Data Filtering":          "Filter pipeline aborted: null pointer in row {n}.",
    "Sorting":                 "Sort failed: comparison function raised exception.",
    "Statistical Computation": "Stats error: insufficient data ({n} < minimum required).",
    "Aggregation":             "Aggregation timeout after {n} ms.",
    "Anomaly Detection":       "Anomaly detection model failed to load.",
    "Validation":              "Schema validation error: unexpected field type.",
    "Summarization":           "Summarization failed: input exceeds token limit.",
}


def _make_seed(agent_id: str, task_id: str) -> int:
    """Derive a deterministic integer seed from agent and task identifiers."""
    raw = f"{agent_id}::{task_id}".encode()
    digest = hashlib.sha256(raw).hexdigest()
    return int(digest[:16], 16) % (2**32)


def _fill_description(template: str, rng: random.Random) -> str:
    n = rng.randint(50, 10_000)
    k = rng.randint(1, max(1, n // 3))
    m = rng.uniform(0.5, 999.5)
    s = rng.uniform(0.01, m * 0.3)
    t = rng.randint(10, 2000)
    return template.format(n=n, k=k, m=m, s=s, t=t)


# ---------------------------------------------------------------------------
# Public executor
# ---------------------------------------------------------------------------


def execute_task(agent: Agent, task: Task) -> WorkerResult:
    """
    Simulate an agent executing a task deterministically.

    The outcome (success/failure) and elapsed time are derived from:
      - agent.reliability  (probability of success)
      - agent.processing_speed  (time multiplier)
      - task.estimated_workload_ms  (baseline duration)
      - sha256(agent.id + task.id) seeded RNG (reproducibility)

    No actual computation is performed.
    """
    seed = _make_seed(agent.id, task.id)
    rng = random.Random(seed)

    # Success / failure draw
    success = rng.random() < agent.reliability

    # Simulated elapsed time: estimated / processing_speed × jitter(0.9..1.1)
    jitter = rng.uniform(0.9, 1.1)
    elapsed_ms = (task.estimated_workload_ms / agent.processing_speed) * jitter

    # Build output description
    desc_map = _SUCCESS_DESCRIPTIONS if success else _FAILURE_DESCRIPTIONS
    template = desc_map.get(task.task_type, "Task {n} processed.")
    output_summary = _fill_description(template, rng)

    return WorkerResult(
        task_id=task.id,
        agent_id=agent.id,
        agent_name=agent.name,
        success=success,
        elapsed_ms=round(elapsed_ms, 2),
        output_summary=output_summary,
        metadata={
            "required_capability": task.required_capability,
            "agent_capability_match": task.required_capability in agent.capabilities,
            "agent_reliability": agent.reliability,
            "agent_processing_speed": agent.processing_speed,
            "complexity": task.complexity,
            "priority": task.priority,
        },
    )
