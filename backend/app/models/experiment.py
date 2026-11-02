"""Pydantic models for experiment configuration.

Mirror of frontend/src/types/experiment.ts. Keep both files in sync.
"""
from __future__ import annotations

import time
from typing import Literal

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Primitives
# ---------------------------------------------------------------------------

ManagerType = Literal["conventional", "posdcorb"]
ExperimentStatus = Literal["pending", "running", "completed", "failed"]
TaskComplexity = Literal["low", "medium", "high"]

# Priority distribution (must sum to 100, validated on the frontend)
# Keys: "critical", "high", "medium", "low"
PriorityDistribution = dict[str, int]


# ---------------------------------------------------------------------------
# Sub-configs  (each maps 1-to-1 with a TypeScript interface)
# ---------------------------------------------------------------------------


class WorkloadConfig(BaseModel):
    """Task-generation parameters."""

    num_tasks: int = Field(..., ge=1, le=10_000, description="Total tasks to generate")
    task_complexity: TaskComplexity = Field("medium", description="Complexity tier")
    dependency_percentage: float = Field(
        0.0, ge=0.0, le=100.0, description="% of tasks with at least one dependency"
    )
    priority_distribution: PriorityDistribution = Field(
        default_factory=lambda: {"critical": 5, "high": 20, "medium": 50, "low": 25},
        description="Task-priority share (must sum to 100)",
    )


class AgentConfig(BaseModel):
    """Agent-pool parameters."""

    initial_agent_count: int = Field(
        ..., ge=1, le=256, description="Agents alive at simulation start"
    )
    max_agent_count: int = Field(
        ..., ge=1, le=256, description="Upper cap on dynamic scaling"
    )
    agent_heterogeneity: float = Field(
        0.0,
        ge=0.0,
        le=1.0,
        description="0 = identical agents; 1 = maximally diverse skill sets",
    )
    agent_failure_probability: float = Field(
        0.0,
        ge=0.0,
        le=1.0,
        description="Per-step probability that any given agent fails",
    )


class ResourceBudget(BaseModel):
    """Hard resource limits enforced by the simulator runtime."""

    max_execution_time_s: float = Field(
        300.0, ge=1.0, description="Wall-clock seconds before simulation is aborted"
    )
    max_manager_decisions: int = Field(
        10_000, ge=1, description="Total manager decision calls allowed"
    )
    max_llm_calls: int = Field(
        5_000, ge=1, description="Total LLM inference calls allowed"
    )
    token_budget: int = Field(
        500_000, ge=1, description="Cumulative token budget across all LLM calls"
    )


class ManagerSelection(BaseModel):
    """Which manager types participate in this experiment."""

    manager_types: list[ManagerType] = Field(
        default_factory=lambda: ["conventional", "posdcorb"],
        description="Managers to benchmark; at least one required",
    )


# ---------------------------------------------------------------------------
# Top-level config
# ---------------------------------------------------------------------------


class ExperimentConfig(BaseModel):
    """Full experiment specification sent from the frontend."""

    # Identity
    name: str = Field(..., min_length=1, max_length=200, description="Human-readable label")
    random_seed: int = Field(42, description="RNG seed for reproducibility")
    repetitions: int = Field(1, ge=1, le=50, description="Independent runs per manager")

    # Sub-configs
    workload: WorkloadConfig
    agents: AgentConfig
    resources: ResourceBudget
    managers: ManagerSelection


# ---------------------------------------------------------------------------
# API request / response shapes
# ---------------------------------------------------------------------------


class CreateExperimentRequest(BaseModel):
    config: ExperimentConfig


class CreateExperimentResponse(BaseModel):
    experiment_id: str
    name: str
    status: ExperimentStatus = "pending"
    created_at: float
    message: str


# ---------------------------------------------------------------------------
# Legacy / dashboard shapes (kept for backward compat with existing pages)
# ---------------------------------------------------------------------------


class Experiment(BaseModel):
    id: int
    config: ExperimentConfig
    status: ExperimentStatus
    created_at: float
