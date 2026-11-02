"""
API router: POST /api/simulate

Accepts a TaskGraph + agent list + simulation config and runs the
requested manager(s) through the SimulationEngine.

The frontend sends the pre-generated TaskGraph directly so the exact same
workload (seeded on the client) is used â€” no server-side re-generation is
needed.  The backend can also re-generate from a WorkloadGeneratorConfig
if the caller omits the graph (useful for CLI / test usage).
"""
from __future__ import annotations

import json
import logging
import threading
import uuid as _uuid
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.database.connection import get_connection
from app.managers.registry import registered_types
from app.models.agent import Agent
from app.models.workload import (
    PriorityDistribution,
    Task,
    TaskGraph,
    WorkloadGeneratorConfig,
)
from app.simulation.engine import SimulationConfig, SimulationEngine
from app.simulation.workload_generator import generate_workload

router = APIRouter(tags=["simulation"])


# ---------------------------------------------------------------------------
# In-memory session store (lightweight; process-scoped)
# ---------------------------------------------------------------------------
# Maps session_id â†’ SimulationSession.  Used by the step-wise /session API.
# Sessions are ephemeral and not persisted to the DB.  The store is bounded
# and guarded because FastAPI runs sync endpoints in a thread pool.
_SESSION_STORE: dict[str, Any] = {}
_SESSION_LOCK = threading.Lock()
_MAX_SESSIONS = 100


def _get_session(session_id: str) -> Any:
    with _SESSION_LOCK:
        return _SESSION_STORE.get(session_id)


def _store_session(session_id: str, session: Any) -> None:
    with _SESSION_LOCK:
        # Drop finished sessions first; if the store is still full, evict the
        # oldest entry (dict preserves insertion order).
        if len(_SESSION_STORE) >= _MAX_SESSIONS:
            for key in [k for k, s in _SESSION_STORE.items() if s.finished]:
                _SESSION_STORE.pop(key, None)
        while len(_SESSION_STORE) >= _MAX_SESSIONS:
            _SESSION_STORE.pop(next(iter(_SESSION_STORE)), None)
        _SESSION_STORE[session_id] = session


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Request / Response shapes
# ---------------------------------------------------------------------------


class SimulateRequest(BaseModel):
    """
    Payload sent by the frontend to trigger a simulation run.

    Either ``graph`` or ``workload_config`` must be provided.
    If both are present, ``graph`` takes precedence.
    """

    # Pre-generated graph from the frontend workload generator
    graph: TaskGraph | None = None

    # Alternative: let the backend generate the graph from a config
    workload_config: WorkloadGeneratorConfig | None = None

    # Agent pool â€” if omitted, the persisted pool from the DB is used
    agents: list[Agent] | None = None

    # Simulation parameters
    manager_types: list[str] = Field(
        default_factory=lambda: ["conventional"],
        description="Manager type(s) to run: ['conventional', 'posdcorb']",
    )
    rng_seed: int = Field(42, ge=0)
    max_decisions: int = Field(10_000, ge=1)
    max_wall_ms: float = Field(300_000.0, ge=1.0)

    # Module 7: generic resource envelope (manager-neutral)
    max_agents: int | None = Field(
        None,
        ge=1,
        description="Cap on concurrently active agents (dynamic staffing).",
    )
    failure_probability: float | None = Field(
        None,
        ge=0.0,
        le=1.0,
        description="Uniform per-execution failure probability (0.0â€“1.0).",
    )

    # Optional: experiment ID to associate this run with
    experiment_id: str | None = None


class SimulateResponse(BaseModel):
    graph_id: str
    seed: int
    manager_types: list[str]
    results: dict   # manager_type â†’ summary dict
    errors: dict    # manager_type â†’ error string
    real_elapsed_s: float

    # Module 7 additions (backward compatible: existing keys are unchanged)
    experiments: dict = Field(default_factory=dict)      # manager_type â†’ ExperimentResult summary
    termination_reasons: dict = Field(default_factory=dict)
    resource_usage: dict = Field(default_factory=dict)
    max_wall_ms: float | None = None


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------


@router.post("/simulate", response_model=SimulateResponse)
def run_simulation(body: SimulateRequest) -> SimulateResponse:
    """
    Run one or more managers against a workload graph.

    Steps
    -----
    1. Resolve the TaskGraph (from payload or by generating from config).
    2. Resolve the agent pool (from payload or from DB).
    3. Validate that all requested manager types are registered.
    4. Run the SimulationEngine.
    5. Return structured results (one summary per manager).
    """

    # -- 1. Resolve TaskGraph -------------------------------------------------
    graph: TaskGraph
    if body.graph is not None:
        graph = body.graph
    elif body.workload_config is not None:
        graph = generate_workload(body.workload_config)
    else:
        raise HTTPException(
            status_code=422,
            detail="Provide either 'graph' or 'workload_config' in the request body.",
        )

    # -- 2. Resolve agent pool ------------------------------------------------
    agents: list[Agent]
    if body.agents is not None:
        agents = body.agents
    else:
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT agent_json FROM agents ORDER BY created_at ASC"
            ).fetchall()
        finally:
            conn.close()

        if not rows:
            raise HTTPException(
                status_code=422,
                detail=(
                    "No agents found in the pool. "
                    "Add agents via POST /api/agents or include 'agents' in the request."
                ),
            )
        agents = [Agent(**json.loads(r["agent_json"])) for r in rows]

    # -- 3. Validate manager types --------------------------------------------
    available = registered_types()
    unknown = [m for m in body.manager_types if m not in available]
    if unknown:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown manager type(s): {unknown}. Available: {available}",
        )

    # -- 4. Run ---------------------------------------------------------------
    engine = SimulationEngine()
    sim_config = SimulationConfig(
        manager_types=body.manager_types,
        rng_seed=body.rng_seed,
        max_decisions=body.max_decisions,
        max_wall_ms=body.max_wall_ms,
        max_agents=body.max_agents,
        failure_probability=body.failure_probability,
        experiment_id=body.experiment_id,
    )
    sim_result = engine.run(graph, agents, sim_config)

    # -- 5. Persist result summary to DB (if experiment_id provided) ----------
    if body.experiment_id:
        _persist_run_result(body.experiment_id, sim_result.as_dict())

    # Build summary: strip full decision list to keep response small
    results_summary: dict = {}
    for mgr_type, trace in sim_result.traces.items():
        experiment = sim_result.experiments.get(mgr_type)
        results_summary[mgr_type] = {
            "total_tasks":            trace.total_tasks,
            "completed_tasks":        trace.completed_tasks,
            "failed_tasks":           trace.failed_tasks,
            "success_rate":           round(trace.success_rate, 4),
            "total_wall_ms":          trace.total_wall_ms,
            "total_decisions":        trace.total_decisions,
            "avg_decision_latency_ms": round(trace.avg_decision_latency_ms, 2),
            "decision_count_by_type": _count_decisions(trace),
            # Module 7: structured experiment summary (Module 8 consumes this)
            "termination_reason": (
                experiment.termination_reason if experiment else None
            ),
            "manager_statistics": (
                experiment.manager_statistics if experiment else {}
            ),
            "resource_usage": (
                experiment.resource_usage if experiment else {}
            ),
            "management_report": _extract_management_report(trace),
            # Include full trace for now (frontend will paginate)
            "decisions": [d.as_dict() for d in trace.decisions],
        }

    return SimulateResponse(
        graph_id=sim_result.graph_id,
        seed=sim_result.seed,
        manager_types=body.manager_types,
        results=results_summary,
        errors=sim_result.errors,
        real_elapsed_s=sim_result.real_elapsed_s,
        experiments={
            manager: experiment.summary_dict()
            for manager, experiment in sim_result.experiments.items()
        },
        termination_reasons=sim_result.termination_reasons,
        resource_usage=sim_result.resource_usage,
        max_wall_ms=body.max_wall_ms,
    )


# ---------------------------------------------------------------------------
# Step-wise session endpoints
# ---------------------------------------------------------------------------


class SessionCreateRequest(BaseModel):
    """
    Payload to create a step-wise simulation session.

    Identical fields to SimulateRequest so the frontend can share one form.
    Either ``graph`` or ``workload_config`` must be provided.
    """

    graph: TaskGraph | None = None
    workload_config: WorkloadGeneratorConfig | None = None
    agents: list[Agent] | None = None
    manager_types: list[str] = Field(
        default_factory=lambda: ["conventional", "posdcorb"]
    )
    rng_seed: int = Field(42, ge=0)
    max_decisions: int = Field(10_000, ge=1)
    max_wall_ms: float = Field(300_000.0, ge=1.0)
    max_agents: int | None = Field(None, ge=1)
    failure_probability: float | None = Field(None, ge=0.0, le=1.0)
    experiment_id: str | None = None


class SessionCreateResponse(BaseModel):
    session_id: str
    status: str
    pending_managers: list[str]
    seed: int


@router.post("/simulate/session", response_model=SessionCreateResponse, status_code=201)
def create_session(body: SessionCreateRequest) -> SessionCreateResponse:
    """
    Create a step-wise simulation session.

    Returns a ``session_id`` that can be used to poll status, advance one step
    at a time, or stop the session early.  The session is ephemeral â€” it lives
    only in the current process and is not persisted to the database.
    """

    # -- Resolve TaskGraph ---------------------------------------------------
    graph: TaskGraph
    if body.graph is not None:
        graph = body.graph
    elif body.workload_config is not None:
        graph = generate_workload(body.workload_config)
    else:
        raise HTTPException(
            status_code=422,
            detail="Provide either 'graph' or 'workload_config' in the request body.",
        )

    # -- Resolve agent pool --------------------------------------------------
    agents: list[Agent]
    if body.agents is not None:
        agents = body.agents
    else:
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT agent_json FROM agents ORDER BY created_at ASC"
            ).fetchall()
        finally:
            conn.close()

        if not rows:
            raise HTTPException(
                status_code=422,
                detail=(
                    "No agents found in the pool. "
                    "Add agents via POST /api/agents or include 'agents' in the request."
                ),
            )
        agents = [Agent(**json.loads(r["agent_json"])) for r in rows]

    # -- Validate manager types ----------------------------------------------
    available = registered_types()
    unknown = [m for m in body.manager_types if m not in available]
    if unknown:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown manager type(s): {unknown}. Available: {available}",
        )

    # -- Build session -------------------------------------------------------
    from app.simulation.engine import SimulationSession  # local to avoid circular

    sim_config = SimulationConfig(
        manager_types=body.manager_types,
        rng_seed=body.rng_seed,
        max_decisions=body.max_decisions,
        max_wall_ms=body.max_wall_ms,
        max_agents=body.max_agents,
        failure_probability=body.failure_probability,
        experiment_id=body.experiment_id,
    )
    engine = SimulationEngine()
    session: SimulationSession = engine.create_session(graph, agents, sim_config)

    session_id = str(_uuid.uuid4())
    _store_session(session_id, session)

    state = session.get_state()
    return SessionCreateResponse(
        session_id=session_id,
        status=state["status"],
        pending_managers=state["pending_managers"],
        seed=state["seed"],
    )


@router.get("/simulate/status/{session_id}")
def get_session_status(session_id: str) -> dict:
    """Return the current state snapshot of a simulation session."""
    session = _get_session(session_id)
    if session is None:
        raise HTTPException(
            status_code=404, detail=f"Session '{session_id}' not found."
        )
    return session.get_state()


@router.post("/simulate/status/{session_id}/step")
def step_session(session_id: str) -> dict:
    """
    Execute the next pending manager in the session.

    Returns ``{"stepped": true, "state": ...}`` when a manager was executed,
    or ``{"stepped": false, "state": ...}`` when the session is already done.
    """
    session = _get_session(session_id)
    if session is None:
        raise HTTPException(
            status_code=404, detail=f"Session '{session_id}' not found."
        )
    result = session.step()
    return {"stepped": result is not None, "state": session.get_state()}


@router.delete("/simulate/status/{session_id}")
def stop_session(session_id: str) -> dict:
    """Stop a running session early.  Completed results are preserved."""
    session = _get_session(session_id)
    if session is None:
        raise HTTPException(
            status_code=404, detail=f"Session '{session_id}' not found."
        )
    session.stop()
    return {"stopped": True, "state": session.get_state()}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _count_decisions(trace) -> dict[str, int]:
    counts: dict[str, int] = {}
    for d in trace.decisions:
        counts[d.manager_decision] = counts.get(d.manager_decision, 0) + 1
    return counts


def _extract_management_report(trace) -> dict | None:
    """
    Extract the ManagementReport embedded in the last execution-trace decision.

    The POSDCORB Manager attaches its ManagementReport.as_dict() snapshot to
    the metadata of the final DecisionRecord under the key "management_report".
    The engine's _instrument_trace() copies it forward to the
    "engine:EXPERIMENT_COMPLETED" envelope event so this helper always finds it
    in the very last decision regardless of trace length.

    Returns None for Conventional Manager traces (no management report present).
    """
    for record in reversed(trace.decisions):
        report = record.metadata.get("management_report")
        if report is not None:
            return report
    return None


def _persist_run_result(experiment_id: str, result_dict: dict) -> bool:
    """
    Store simulation result JSON against the experiment record.

    Returns True when a row was updated.  Failures are logged instead of
    being swallowed silently: a run that is not persisted must not look
    persisted to the caller or to a later analysis.
    """
    import json as _json
    conn = get_connection()
    try:
        cursor = conn.execute(
            "UPDATE experiments SET status='completed', result_json=? WHERE id=?",
            (_json.dumps(result_dict), experiment_id),
        )
        conn.commit()
        if cursor.rowcount == 0:
            logger.warning(
                "Experiment '%s' does not exist; simulation result was not persisted.",
                experiment_id,
            )
            return False
        return True
    except Exception:  # noqa: BLE001 - persistence must not lose the run result
        logger.exception(
            "Failed to persist simulation result for experiment '%s'.",
            experiment_id,
        )
        return False
    finally:
        conn.close()
