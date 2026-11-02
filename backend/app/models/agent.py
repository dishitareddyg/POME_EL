"""
Pydantic models for Agent pool management.

Mirror of frontend/src/types/agent.ts — keep both files in sync.

Key design decision:
  - origin="initial"  → available to BOTH Conventional and POSDCORB managers.
  - origin="dynamic"  → reserved for POSDCORB Staffing; Conventional ignores them.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Primitives
# ---------------------------------------------------------------------------

AgentRole = Literal[
    "Researcher",
    "Analyst",
    "Processor",
    "Validator",
    "Summarizer",
    "General Worker",
]

AgentStatus = Literal["idle", "running", "waiting", "failed", "completed"]

AgentOrigin = Literal["initial", "dynamic"]


# ---------------------------------------------------------------------------
# Core agent model
# ---------------------------------------------------------------------------


class Agent(BaseModel):
    """
    Full agent record.  Runtime fields (status, cpu_usage, …) are managed
    by the simulation engine; they are stored here for persistence between
    simulation steps.
    """

    # Identity
    id: str = Field(..., description="Stable unique identifier, e.g. 'agt-001'")
    name: str = Field(..., min_length=1, max_length=100)
    role: AgentRole

    # Configuration (user-editable)
    capabilities: list[str] = Field(
        ...,
        min_length=1,
        description="Declared capability tags used for task routing",
    )
    processing_speed: float = Field(
        1.0, ge=0.1, le=5.0,
        description="Speed multiplier; 1.0 = baseline",
    )
    reliability: float = Field(
        0.95, ge=0.0, le=1.0,
        description="Probability of successfully completing a task",
    )
    max_concurrent_tasks: int = Field(
        3, ge=1, le=16,
        description="Max parallel tasks this agent can handle",
    )

    # Runtime state (managed by simulation engine)
    current_workload: int = Field(0, ge=0)
    status: AgentStatus = "idle"
    cpu_usage: float = Field(0.0, ge=0.0, le=100.0)
    tasks_completed: int = Field(0, ge=0)
    tasks_failed: int = Field(0, ge=0)

    # Pool metadata
    origin: AgentOrigin = Field(
        "initial",
        description=(
            "'initial' → in fixed pool for both managers; "
            "'dynamic' → POSDCORB Staffing reserve only"
        ),
    )
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


# ---------------------------------------------------------------------------
# API request / response shapes
# ---------------------------------------------------------------------------


class CreateAgentRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    role: AgentRole
    capabilities: list[str] = Field(..., min_length=1)
    processing_speed: float = Field(1.0, ge=0.1, le=5.0)
    reliability: float = Field(0.95, ge=0.0, le=1.0)
    max_concurrent_tasks: int = Field(3, ge=1, le=16)
    origin: AgentOrigin = "initial"


class UpdateAgentRequest(BaseModel):
    """All fields optional for partial updates (PATCH semantics)."""
    name: str | None = Field(None, min_length=1, max_length=100)
    role: AgentRole | None = None
    capabilities: list[str] | None = None
    processing_speed: float | None = Field(None, ge=0.1, le=5.0)
    reliability: float | None = Field(None, ge=0.0, le=1.0)
    max_concurrent_tasks: int | None = Field(None, ge=1, le=16)
    origin: AgentOrigin | None = None


class AgentResponse(BaseModel):
    agent: Agent
    message: str


class AgentListResponse(BaseModel):
    agents: list[Agent]
    total: int
    initial_count: int
    dynamic_count: int
