"""
API router: POST /api/metrics/compute
         GET  /api/metrics/compare

Module 8 metrics endpoints.  All computation is done server-side from
ExperimentResult structures.  The frontend can call these after receiving
a SimulateResponse to get the full derived metric bundle.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.metrics.engine import Aggregator, ComparisonEngine, MetricsEngine

router = APIRouter(tags=["metrics"])

_metrics_engine = MetricsEngine()
_comparison_engine = ComparisonEngine()


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class ComputeMetricsRequest(BaseModel):
    """
    Compute DerivedMetrics for one or more ExperimentResults.

    experiment_results :
        List of experiment result dicts as returned by POST /api/simulate
        (the ``experiments`` field — one dict per manager).
    """

    experiment_results: list[dict] = Field(
        ...,
        description=(
            "List of ExperimentResult summary dicts from POST /api/simulate "
            "(the ``experiments`` key, one entry per manager)."
        ),
    )


class ComputeMetricsResponse(BaseModel):
    metrics: dict   # manager_type → DerivedMetrics.as_dict()


class CompareRequest(BaseModel):
    """
    Compare a conventional and a POSDCORB experiment result.

    Both result dicts must be present; the keys 'conventional' and 'posdcorb'
    must exist in ``experiments``.
    """

    experiments: dict = Field(
        ...,
        description=(
            "Dict keyed by manager_type ('conventional', 'posdcorb') "
            "mapping to ExperimentResult summary dicts."
        ),
    )
    # Full trace decisions for richer per-task metrics (optional)
    trace_decisions: dict = Field(
        default_factory=dict,
        description=(
            "Optional dict keyed by manager_type mapping to lists of "
            "DecisionRecord dicts for per-task analysis."
        ),
    )


class CompareResponse(BaseModel):
    workload_id: str
    seed: int
    rows: list[dict]
    conventional_metrics: dict | None = None
    posdcorb_metrics: dict | None = None


# ---------------------------------------------------------------------------
# Helper: rebuild ExperimentResult from dict
# ---------------------------------------------------------------------------


def _build_experiment_result(data: dict):
    """
    Reconstruct a lightweight ExperimentResult-compatible object from the
    summary dict returned by POST /api/simulate.

    We use a simple namespace-style object rather than importing
    SimulationEngine to keep metrics decoupled.
    """
    from app.managers.base import DecisionRecord, ExecutionTrace

    trace = ExecutionTrace(
        manager_type=data.get("manager_type", "unknown"),
        graph_id=data.get("workload_id", "unknown"),
        seed=int(data.get("seed", 0)),
    )
    trace.total_tasks = int(data.get("total_tasks", 0))
    trace.completed_tasks = int(data.get("completed_tasks", 0))
    trace.failed_tasks = int(data.get("failed_tasks", 0))
    trace.total_wall_ms = float(data.get("total_duration_ms", 0.0))
    trace.total_decisions = int(data.get("total_decisions", 0))

    # Reconstruct decision records if the caller supplied them
    raw_decisions = data.get("decisions", [])
    for rd in raw_decisions:
        trace.decisions.append(DecisionRecord(
            manager_decision=rd.get("manager_decision", ""),
            task_id=rd.get("task_id"),
            selected_agent_id=rd.get("selected_agent_id"),
            selected_agent_name=rd.get("selected_agent_name"),
            timestamp=rd.get("timestamp", ""),
            decision_latency_ms=float(rd.get("decision_latency_ms", 0.0)),
            reason=rd.get("reason", ""),
            result=rd.get("result"),
            success=rd.get("success"),
            metadata=rd.get("metadata", {}),
        ))

    # Build a duck-typed result compatible with MetricsEngine.compute()
    class _Result:
        manager_type = data.get("manager_type", "unknown")
        experiment_id = data.get("experiment_id", "unknown")
        seed = int(data.get("seed", 0))
        workload_id = data.get("workload_id", "unknown")
        execution_trace = trace
        resource_usage = data.get("resource_usage", {})
        termination_reason = data.get("termination_reason", "unknown")
        error = data.get("error")

    return _Result()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("/metrics/compute", response_model=ComputeMetricsResponse)
def compute_metrics(body: ComputeMetricsRequest) -> ComputeMetricsResponse:
    """
    Compute DerivedMetrics for each ExperimentResult in the request.

    Accepts the ``experiments`` dict from POST /api/simulate as input.
    Returns a dict keyed by manager_type with full DerivedMetrics.
    """
    if not body.experiment_results:
        raise HTTPException(status_code=422, detail="No experiment_results provided.")

    metrics_by_manager: dict = {}
    for exp_data in body.experiment_results:
        try:
            result_obj = _build_experiment_result(exp_data)
            derived = _metrics_engine.compute(result_obj)
            metrics_by_manager[derived.manager_type] = derived.as_dict()
        except Exception as exc:  # noqa: BLE001
            manager_type = exp_data.get("manager_type", "unknown")
            metrics_by_manager[manager_type] = {
                "error": str(exc),
                "manager_type": manager_type,
            }

    return ComputeMetricsResponse(metrics=metrics_by_manager)


@router.post("/metrics/compare", response_model=CompareResponse)
def compare_metrics(body: CompareRequest) -> CompareResponse:
    """
    Compare Conventional vs POSDCORB manager metrics.

    The request ``experiments`` dict must contain both 'conventional' and
    'posdcorb' keys.  Returns a ComparisonResult with per-metric rows.
    """
    if "conventional" not in body.experiments:
        raise HTTPException(
            status_code=422,
            detail="'conventional' key missing from experiments dict.",
        )
    if "posdcorb" not in body.experiments:
        raise HTTPException(
            status_code=422,
            detail="'posdcorb' key missing from experiments dict.",
        )

    # Merge any supplied trace decisions into the experiment dicts
    conv_data = dict(body.experiments["conventional"])
    posd_data = dict(body.experiments["posdcorb"])
    if "conventional" in body.trace_decisions:
        conv_data["decisions"] = body.trace_decisions["conventional"]
    if "posdcorb" in body.trace_decisions:
        posd_data["decisions"] = body.trace_decisions["posdcorb"]

    try:
        conv_result = _build_experiment_result(conv_data)
        posd_result = _build_experiment_result(posd_data)
        conv_metrics = _metrics_engine.compute(conv_result)
        posd_metrics = _metrics_engine.compute(posd_result)
        comparison = _comparison_engine.compare(conv_metrics, posd_metrics)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return CompareResponse(
        workload_id=comparison.workload_id,
        seed=comparison.seed,
        rows=[r.as_dict() for r in comparison.rows],
        conventional_metrics=comparison.conventional_metrics.as_dict()
        if comparison.conventional_metrics else None,
        posdcorb_metrics=comparison.posdcorb_metrics.as_dict()
        if comparison.posdcorb_metrics else None,
    )


@router.get("/metrics/compare/csv")
def compare_metrics_csv(workload_id: str = "", seed: int = 0) -> dict:
    """
    CSV export is not available server-side.

    The comparison result exists only for the duration of a POST
    /metrics/compare request and is not stored.  Returning a header-only file
    would look like a successful export; fail explicitly instead.  Clients can
    generate CSV from the POST response with
    ``MetricsExporter.comparison_to_csv()``.
    """
    raise HTTPException(
        status_code=501,
        detail=(
            "CSV export requires a stored comparison. Use POST /metrics/compare "
            "and serialise its response with MetricsExporter.comparison_to_csv()."
        ),
    )


@router.post("/metrics/aggregate")
def aggregate_metrics(body: dict) -> dict:
    """
    Aggregate DerivedMetrics dicts across multiple runs.

    Body: {"manager_type": "...", "runs": [<DerivedMetrics dict>, ...]}
    Returns: AggregatedMetrics.as_dict()
    """
    manager_type = body.get("manager_type", "unknown")
    runs_data = body.get("runs", [])

    if not runs_data:
        raise HTTPException(status_code=422, detail="No runs provided.")

    # We can only aggregate from ExperimentResult dicts (not raw DerivedMetrics)
    # because the API doesn't round-trip DerivedMetrics yet.  Return what we can.
    agg = Aggregator(manager_type)
    errors: list[str] = []

    for run_data in runs_data:
        try:
            result_obj = _build_experiment_result(run_data)
            derived = _metrics_engine.compute(result_obj)
            agg.add(derived)
        except Exception as exc:  # noqa: BLE001
            errors.append(str(exc))

    aggregated = agg.aggregate()
    result = aggregated.as_dict()
    if errors:
        result["errors"] = errors
    return result
