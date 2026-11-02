"""API router for experiment management."""
import json
import time
import uuid

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from app.database.connection import get_connection
from app.models.experiment import (
    CreateExperimentRequest,
    CreateExperimentResponse,
)

router = APIRouter(tags=["experiments"])


@router.post("/experiments", response_model=CreateExperimentResponse, status_code=201)
def create_experiment(body: CreateExperimentRequest) -> CreateExperimentResponse:
    """
    Validate and persist an experiment configuration.

    Returns a generated experiment ID and acknowledges the config.
    Actual simulation is NOT triggered here; it will be wired later.
    """
    config = body.config

    # Extra cross-field validation
    if config.agents.max_agent_count < config.agents.initial_agent_count:
        raise HTTPException(
            status_code=422,
            detail="max_agent_count must be >= initial_agent_count",
        )

    priority_sum = sum(config.workload.priority_distribution.values())
    if priority_sum != 100:
        raise HTTPException(
            status_code=422,
            detail=f"priority_distribution must sum to 100, got {priority_sum}",
        )

    if not config.managers.manager_types:
        raise HTTPException(
            status_code=422,
            detail="At least one manager type must be selected",
        )

    # Persist to SQLite
    experiment_id = str(uuid.uuid4())
    created_at = time.time()
    config_json = config.model_dump_json()

    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO experiments (id, name, config_json, status, created_at)
            VALUES (?, ?, ?, 'pending', ?)
            """,
            (experiment_id, config.name, config_json, created_at),
        )
        conn.commit()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Database error: {exc}") from exc
    finally:
        conn.close()

    return CreateExperimentResponse(
        experiment_id=experiment_id,
        name=config.name,
        status="pending",
        created_at=created_at,
        message=(
            f"Experiment '{config.name}' created successfully. "
            f"ID: {experiment_id}. "
            f"Managers: {', '.join(config.managers.manager_types)}. "
            f"Tasks: {config.workload.num_tasks}. "
            f"Simulation will begin once the engine is available."
        ),
    )
