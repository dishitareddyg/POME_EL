"""
Pydantic models for workload task graphs.

Mirror of frontend/src/types/workload.ts — keep both files in sync.

A TaskGraph is generated deterministically from a WorkloadGeneratorConfig
and is passed IDENTICALLY to both the Conventional and POSDCORB managers.
The simulation engine must not mutate the original graph; it works on deep
copies so the original can be replayed.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Primitives
# ---------------------------------------------------------------------------

TaskType = Literal[
    "Data Filtering",
    "Sorting",
    "Statistical Computation",
    "Aggregation",
    "Anomaly Detection",
    "Validation",
    "Summarization",
]

TaskComplexity = Literal["low", "medium", "high"]
TaskPriority = Literal["critical", "high", "medium", "low"]
TaskStatus = Literal["pending", "ready", "running", "completed", "failed", "blocked"]

# Capability required by each task type (matches frontend constant).
TASK_TYPE_CAPABILITY: dict[str, str] = {
    "Data Filtering":          "text-processing",
    "Sorting":                 "batch-execution",
    "Statistical Computation": "statistics",
    "Aggregation":             "data-visualization",
    "Anomaly Detection":       "anomaly-detection",
    "Validation":              "quality-check",
    "Summarization":           "text-summarization",
}

# Estimated base workload (ms) per complexity×type.
BASE_WORKLOAD_MS: dict[str, dict[str, int]] = {
    "low": {
        "Data Filtering":          200,
        "Sorting":                 150,
        "Statistical Computation": 400,
        "Aggregation":             250,
        "Anomaly Detection":       350,
        "Validation":              180,
        "Summarization":           300,
    },
    "medium": {
        "Data Filtering":          600,
        "Sorting":                 500,
        "Statistical Computation": 1200,
        "Aggregation":             800,
        "Anomaly Detection":       1100,
        "Validation":              550,
        "Summarization":           900,
    },
    "high": {
        "Data Filtering":          1800,
        "Sorting":                 1400,
        "Statistical Computation": 3500,
        "Aggregation":             2200,
        "Anomaly Detection":       3000,
        "Validation":              1600,
        "Summarization":           2600,
    },
}


# ---------------------------------------------------------------------------
# Core Task model
# ---------------------------------------------------------------------------


class Task(BaseModel):
    """Single unit of computational work within a TaskGraph."""

    id: str = Field(..., description="Stable unique identifier, e.g. 'task-0001'")
    index: int = Field(..., ge=0, description="Sequential index (0-based) within the workload")
    task_type: TaskType
    complexity: TaskComplexity
    required_capability: str = Field(..., description="Capability tag an agent must possess")
    priority: TaskPriority
    estimated_workload_ms: int = Field(..., ge=1, description="Estimated duration in ms")
    dependency_ids: list[str] = Field(
        default_factory=list,
        description="IDs of tasks that must complete before this one starts",
    )

    # Runtime state — set by simulation engine, not by the generator
    status: TaskStatus = "pending"
    assigned_agent_id: str | None = None
    start_time: str | None = None
    completion_time: str | None = None


# ---------------------------------------------------------------------------
# Priority distribution helper
# ---------------------------------------------------------------------------


class PriorityDistribution(BaseModel):
    critical: int = Field(10, ge=0, le=100)
    high: int = Field(25, ge=0, le=100)
    medium: int = Field(45, ge=0, le=100)
    low: int = Field(20, ge=0, le=100)


# ---------------------------------------------------------------------------
# Generator configuration
# ---------------------------------------------------------------------------


class WorkloadGeneratorConfig(BaseModel):
    num_tasks: int = Field(30, ge=1, le=500)
    complexity: TaskComplexity = "medium"
    dependency_percentage: float = Field(40.0, ge=0.0, le=100.0)
    priority_distribution: PriorityDistribution = Field(
        default_factory=PriorityDistribution
    )
    seed: int = Field(42, ge=0)


# ---------------------------------------------------------------------------
# TaskGraph — full workload as a DAG
# ---------------------------------------------------------------------------


class TaskGraph(BaseModel):
    """
    Immutable workload graph.  Generated once and passed to both managers.
    The simulation engine works on mutable copies of individual tasks.
    """

    graph_id: str
    seed: int
    tasks: list[Task]
    # Forward adjacency list: task_id → list of successor task_ids
    edges: dict[str, list[str]] = Field(default_factory=dict)
    generated_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    config: WorkloadGeneratorConfig
