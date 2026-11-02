"""
API router: POSDCORB Ablation Study.

Endpoints
---------
POST /api/ablation/study
    Run the nine cumulative manager configurations against one identical
    workload and return the comparison table, metrics, directional
    improvements, marginal contributions and ranking.

GET  /api/ablation/configurations
    Return the configuration ladder and metric definitions without running
    anything (used by the frontend to render the study description).

Fairness contract
-----------------
The report is only meaningful when all configurations share the same workers,
tasks and resource envelope.  The request accepts an explicit agent pool and a
workload configuration, but never lets individual configurations override
them.  When no pool is supplied the router falls back to saved agents, then to
a deterministic built-in pool.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.ablation.configs import (
    build_ablation_ladder,
    build_custom_configurations,
)
from app.ablation.defaults import default_agents
from app.ablation.runner import METRIC_SPECS, run_ablation_study
from app.database.connection import get_connection
from app.models.agent import Agent
from app.models.workload import WorkloadGeneratorConfig

router = APIRouter(tags=["ablation"])

MAX_REPETITIONS = 10


# ---------------------------------------------------------------------------
# Request model
# ---------------------------------------------------------------------------


class AblationStudyRequest(BaseModel):
    """Inputs shared by every configuration in the study."""

    # ---- Worker pool -------------------------------------------------
    agents: list[Agent] | None = Field(
        default=None,
        description=(
            "Explicit agent pool. When omitted, saved agents are used if "
            "available, otherwise a deterministic 4-initial + 4-dynamic pool."
        ),
    )
    use_saved_agents: bool = Field(
        default=True,
        description="Load agents from the database when 'agents' is omitted.",
    )

    # ---- Workload ----------------------------------------------------
    workload: WorkloadGeneratorConfig = Field(
        default_factory=lambda: WorkloadGeneratorConfig(
            num_tasks=20,
            complexity="medium",
            dependency_percentage=25.0,
        ),
        description=(
            "Workload generator settings. Repetition r uses seed = "
            "base_seed + r; the generated graph is shared by every "
            "configuration."
        ),
    )

    # ---- Repetitions / limits ---------------------------------------
    repetitions: int = Field(default=1, ge=1, le=MAX_REPETITIONS)
    base_seed: int = Field(default=42, ge=0)
    max_decisions: int = Field(default=5_000, ge=1)
    max_wall_ms: float = Field(default=120_000.0, ge=1.0)
    max_agents: int | None = Field(
        default=None,
        ge=1,
        description="Optional active-agent cap applied to every configuration.",
    )
    failure_probability: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Uniform per-agent failure probability for every run.",
    )

    # ---- Optional custom ladder -------------------------------------
    configurations: list[dict[str, Any]] | None = Field(
        default=None,
        description=(
            "Optional custom configuration specs. Each spec accepts id, "
            "label, enabled_principles, adaptive_mechanisms. A conventional "
            "baseline is prepended automatically when absent."
        ),
    )


# ---------------------------------------------------------------------------
# Agent resolution
# ---------------------------------------------------------------------------


def _agents_from_db() -> list[Agent]:
    """
    Return saved agents, or an empty list when the table is unavailable.

    A missing database/table is not an error: the ablation study must remain
    runnable on a fresh checkout.
    """
    try:
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT agent_json FROM agents ORDER BY created_at, id"
            ).fetchall()
        finally:
            conn.close()
    except (sqlite3.Error, OSError):
        return []

    agents: list[Agent] = []
    for row in rows:
        try:
            payload = json.loads(row["agent_json"])
            agents.append(Agent.model_validate(payload))
        except (ValueError, TypeError, KeyError):
            continue
    return agents


def _resolve_agents(request: AblationStudyRequest) -> tuple[list[Agent], str]:
    if request.agents:
        # Fresh copies: the runner must never share mutable agent state.
        return [a.model_copy(deep=True) for a in request.agents], "request"

    if request.use_saved_agents:
        saved = _agents_from_db()
        if saved:
            return saved, "database"

    return default_agents(), "default"


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/ablation/study")
def run_study(request: AblationStudyRequest) -> dict[str, Any]:
    """Run the ablation study and return the full comparison report."""
    try:
        if request.configurations:
            configurations = build_custom_configurations(
                request.configurations
            )
        else:
            configurations = build_ablation_ladder()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    agents, agent_source = _resolve_agents(request)

    try:
        result = run_ablation_study(
            agents=agents,
            workload_config=request.workload,
            configurations=configurations,
            repetitions=request.repetitions,
            base_seed=request.base_seed,
            max_decisions=request.max_decisions,
            max_wall_ms=request.max_wall_ms,
            max_agents=request.max_agents,
            failure_probability=request.failure_probability,
            study_id=f"ablation-{uuid.uuid4().hex[:12]}",
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — surface a clean API error
        raise HTTPException(
            status_code=500, detail=f"Ablation study failed: {exc}"
        ) from exc

    payload = result.as_dict()
    payload["agent_source"] = agent_source
    payload["baseline_config_id"] = next(
        c.id for c in result.configurations if c.configuration.is_baseline
    )
    return payload


@router.get("/ablation/configurations")
def list_configurations() -> dict[str, Any]:
    """Describe the ladder and metric definitions without running a study."""
    ladder = build_ablation_ladder()
    return {
        "configurations": [c.as_dict() for c in ladder],
        "metric_specs": [m.as_dict() for m in METRIC_SPECS],
        "baseline_config_id": next(c.id for c in ladder if c.is_baseline),
        "default_workload": WorkloadGeneratorConfig(
            num_tasks=20,
            complexity="medium",
            dependency_percentage=25.0,
        ).model_dump(mode="json"),
        "notes": {
            "rungs_1_8": (
                "Cumulative: each rung enables one additional principle. The "
                "adaptive layer is disabled so each delta isolates one "
                "principle."
            ),
            "rung_9": (
                "Full POSDCORB adds the cross-cutting adaptive layer "
                "(failure replanning, slow-task replanning, recovery, "
                "deadlock detection)."
            ),
            "improvement": (
                "Directional: positive always means better than the baseline. "
                "Activation count is context-dependent and excluded from the "
                "composite score."
            ),
        },
    }
