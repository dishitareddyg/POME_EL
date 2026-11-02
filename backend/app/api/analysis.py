"""
API router: POST /api/analysis/batch

Module 10 — Experiment Analysis (Parameter Sweep).

Runs a configurable parameter sweep comparing the Conventional and POSDCORB
managers across multiple experiment configurations and optional repetitions.

For every (config × repetition) pair the same workload is used for both
managers (same graph, same seed, same agents) so the manager strategy is
the only independent variable.

Response contains:
- raw_runs    — one record per (manager × config × rep)
- stats       — per-config aggregated stats (mean/median/std/min/max)
- effect_sizes— Cohen's d between managers for each key metric × config
- chart_data  — pre-binned series ready for frontend chart rendering
- errors      — non-fatal per-run error messages

Statistical note
----------------
Effect sizes are calculated using Cohen's d.
No p-value or significance test is performed here unless scipy is installed
and n_reps >= 3; when a t-test is performed the test name and p-value are
included in the EffectSize record.  Callers MUST check ``test_performed``
before drawing statistical conclusions.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import sqlite3
import statistics
import time
import uuid as _uuid
from dataclasses import dataclass, field, asdict
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from app.database.connection import get_connection
from app.managers.registry import registered_types
from app.metrics.engine import MetricsEngine
from app.models.agent import Agent
from app.models.workload import WorkloadGeneratorConfig
from app.simulation.engine import SimulationConfig, SimulationEngine
from app.simulation.workload_generator import generate_workload

router = APIRouter(tags=["analysis"])

logger = logging.getLogger(__name__)

_metrics_engine = MetricsEngine()

# ---------------------------------------------------------------------------
# Hard limits — prevent runaway requests
# ---------------------------------------------------------------------------

MAX_TOTAL_RUNS = 200   # configs × reps × 2 managers
MAX_REPETITIONS = 10
MAX_CONFIGS = 50

# ---------------------------------------------------------------------------
# Request model
# ---------------------------------------------------------------------------


class BatchRequest(BaseModel):
    """
    Parameter sweep specification.

    Each list defines the values to vary for that dimension.
    The cross-product of all lists defines the set of experiment configurations.

    At most MAX_TOTAL_RUNS (200) total manager-runs are allowed.
    """

    # ---- Parameter axes (cross-product) ----
    task_counts: list[int] = Field(
        default=[8, 12, 16],
        description="Task counts to sweep. Each value produces one config.",
        min_length=1,
    )
    complexities: list[str] = Field(
        default=["medium"],
        description="Task complexities: 'low' | 'medium' | 'high'.",
    )
    dep_percentages: list[float] = Field(
        default=[25.0],
        description="Dependency percentages [0..100].",
    )
    failure_probs: list[float] = Field(
        default=[0.0],
        description="Per-task failure probabilities [0.0..1.0].",
    )
    max_agents_list: list[int] = Field(
        default=[6],
        description="Max concurrent agent counts.",
    )

    # ---- Repetitions ----
    repetitions: int = Field(
        default=1,
        ge=1,
        le=MAX_REPETITIONS,
        description=(
            "Number of repetitions per configuration. "
            "Each repetition uses seed = base_seed + rep_idx."
        ),
    )
    base_seed: int = Field(
        default=42,
        ge=0,
        description="Base RNG seed. Rep k uses seed = base_seed + k.",
    )

    # ---- Simulation limits ----
    max_decisions: int = Field(default=5_000, ge=1)
    max_wall_ms: float = Field(default=120_000.0, ge=1.0)

    # ---- Optional explicit worker pool ----
    # When omitted, the saved agent pool is used, then the deterministic
    # built-in pool.  Passing the pool explicitly makes the worker identities
    # auditable and lets the same batch be replayed without DB state.
    agents: list[Agent] | None = Field(
        default=None,
        description=(
            "Optional explicit agent pool. Every configuration receives the "
            "same deep-copied pool. When omitted, saved agents are used, "
            "falling back to the deterministic built-in pool."
        ),
    )


# ---------------------------------------------------------------------------
# Result models
# ---------------------------------------------------------------------------


class StatsSummary(BaseModel):
    """Descriptive statistics for a numeric metric across N repetitions."""
    mean: float
    median: float
    std: float
    min: float
    max: float
    n: int


class EffectSize(BaseModel):
    """
    Cohen's d effect size between Conventional and POSDCORB on one metric
    for one configuration.

    cohens_d = (mean_conv - mean_posd) / pooled_std

    Magnitude thresholds (Cohen 1988):
        negligible : |d| < 0.2
        small      : 0.2 <= |d| < 0.5
        medium     : 0.5 <= |d| < 0.8
        large      : |d| >= 0.8
        not_estimable : fewer than two repetitions per manager; Cohen's d
                        cannot be computed from a single value per group.

    test_performed :
        Name of statistical test if one was computed (e.g. 'welch_t'), else ''.
    p_value :
        p-value from the test, or None if no test was performed.

    IMPORTANT: Do not interpret p_value as evidence of significance unless
    n_reps is sufficiently large.  This module sets test_performed='' and
    p_value=null unless n_reps >= 3 and scipy is available.
    """
    config_id: str
    metric: str
    task_count: int
    complexity: str
    dep_pct: float
    failure_prob: float
    max_agents: int
    mean_conventional: float
    mean_posdcorb: float
    cohens_d: float
    magnitude: str          # negligible | small | medium | large | not_estimable
    interpretation: str
    test_performed: str     # '' | 'welch_t'
    p_value: float | None


class RunRecord(BaseModel):
    """One manager run result within the batch."""
    run_id: str
    config_id: str
    rep: int
    seed: int
    # Config axes
    task_count: int
    complexity: str
    dep_pct: float
    failure_prob: float
    max_agents: int
    # Identity
    manager: str
    # Performance
    completed_tasks: int
    failed_tasks: int
    success_rate: float
    total_wall_ms: float
    throughput_tasks_per_sec: float
    avg_task_latency_ms: float
    # Management
    total_decisions: int
    coordination_overhead_ratio: float
    # Agents
    total_active_agents: int
    activation_count: int
    avg_agent_utilization: float
    # Reliability
    recovery_count: int
    recovery_success_rate: float
    # Meta
    termination_reason: str
    error: str


class ConfigStats(BaseModel):
    """Aggregated statistics across repetitions for one config × manager."""
    config_id: str
    manager: str
    task_count: int
    complexity: str
    dep_pct: float
    failure_prob: float
    max_agents: int
    n_reps: int
    success_rate: StatsSummary
    throughput: StatsSummary
    avg_latency_ms: StatsSummary
    total_wall_ms: StatsSummary
    coordination_overhead: StatsSummary
    active_agents: StatsSummary
    recovery_rate: StatsSummary
    avg_agent_utilization: StatsSummary


class ChartPoint(BaseModel):
    """One x-axis point on a comparison chart."""
    x_value: float        # task_count or failure_prob
    x_label: str          # human-readable label
    conventional_mean: float
    conventional_std: float
    conventional_min: float
    conventional_max: float
    posdcorb_mean: float
    posdcorb_std: float
    posdcorb_min: float
    posdcorb_max: float
    n_reps: int


class BatchChartData(BaseModel):
    """Pre-computed chart series for all 7 dashboard charts."""
    task_count_vs_latency: list[ChartPoint]
    task_count_vs_throughput: list[ChartPoint]
    task_count_vs_success_rate: list[ChartPoint]
    task_count_vs_coordination_overhead: list[ChartPoint]
    task_count_vs_active_agents: list[ChartPoint]
    failure_prob_vs_recovery_rate: list[ChartPoint]
    # CPU utilisation is simulated (execution / wall), so it is not plotted
    # alongside host-machine resource metrics.  The note explains that.
    task_count_vs_cpu_note: str


class BatchResponse(BaseModel):
    batch_id: str
    total_configs: int
    total_runs: int
    elapsed_s: float
    raw_runs: list[RunRecord]
    stats: list[ConfigStats]
    effect_sizes: list[EffectSize]
    chart_data: BatchChartData
    errors: list[str]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _config_id(task_count: int, complexity: str, dep_pct: float,
               failure_prob: float, max_agents: int) -> str:
    """Stable short identifier for a configuration combination."""
    raw = json.dumps(
        [task_count, complexity, dep_pct, round(failure_prob, 4), max_agents],
        sort_keys=True,
    )
    return "c-" + hashlib.md5(raw.encode()).hexdigest()[:8]


def _stats(values: list[float]) -> StatsSummary:
    n = len(values)
    if n == 0:
        return StatsSummary(mean=0, median=0, std=0, min=0, max=0, n=0)
    return StatsSummary(
        mean=round(statistics.mean(values), 6),
        median=round(statistics.median(values), 6),
        std=round(statistics.stdev(values) if n >= 2 else 0.0, 6),
        min=round(min(values), 6),
        max=round(max(values), 6),
        n=n,
    )


def _cohens_d(a: list[float], b: list[float]) -> float:
    """
    Cohen's d: (mean_a - mean_b) / pooled_std.

    When pooled_std == 0 (all values identical within each group) but the
    means differ, returns ±10.0 (a very large effect) to avoid division by
    zero while preserving directionality.
    Returns 0.0 when both groups are empty or means are equal with zero std.
    """
    if len(a) < 1 or len(b) < 1:
        return 0.0
    m_a = statistics.mean(a) if a else 0.0
    m_b = statistics.mean(b) if b else 0.0
    if len(a) < 2 and len(b) < 2:
        # Cannot compute std; return sign only
        return 1.0 if m_a > m_b else (-1.0 if m_a < m_b else 0.0)
    std_a = statistics.stdev(a) if len(a) >= 2 else 0.0
    std_b = statistics.stdev(b) if len(b) >= 2 else 0.0
    n_a, n_b = len(a), len(b)
    pooled = math.sqrt(
        ((n_a - 1) * std_a ** 2 + (n_b - 1) * std_b ** 2)
        / max(1, n_a + n_b - 2)
    )
    if pooled == 0:
        # Zero within-group variance: infinite effect if means differ
        if m_a > m_b:
            return 10.0
        if m_a < m_b:
            return -10.0
        return 0.0
    return round((m_a - m_b) / pooled, 4)


def _magnitude(d: float) -> str:
    abs_d = abs(d)
    if abs_d < 0.2:
        return "negligible"
    if abs_d < 0.5:
        return "small"
    if abs_d < 0.8:
        return "medium"
    return "large"


def _welch_t_test(a: list[float], b: list[float]) -> tuple[float, float] | None:
    """
    Welch's t-test.  Returns (t_stat, p_value) if scipy is available and
    both groups have n >= 3; otherwise returns None.
    """
    if len(a) < 3 or len(b) < 3:
        return None
    try:
        from scipy import stats as _sp  # type: ignore
        result = _sp.ttest_ind(a, b, equal_var=False)
        return float(result.statistic), float(result.pvalue)
    except ImportError:
        return None


def _effect_size(
    metric: str,
    config_id: str,
    task_count: int,
    complexity: str,
    dep_pct: float,
    failure_prob: float,
    max_agents: int,
    conv_values: list[float],
    posd_values: list[float],
    higher_is_better: bool,
) -> EffectSize:
    m_c = statistics.mean(conv_values) if conv_values else 0.0
    m_p = statistics.mean(posd_values) if posd_values else 0.0

    # Cohen's d requires within-group variance.  With one run per manager
    # (the default sweep) it is not estimable; reporting a synthetic "large"
    # effect from the sign of the difference would be statistically invalid.
    if len(conv_values) < 2 or len(posd_values) < 2:
        return EffectSize(
            config_id=config_id,
            metric=metric,
            task_count=task_count,
            complexity=complexity,
            dep_pct=dep_pct,
            failure_prob=failure_prob,
            max_agents=max_agents,
            mean_conventional=round(m_c, 6),
            mean_posdcorb=round(m_p, 6),
            cohens_d=0.0,
            magnitude="not_estimable",
            interpretation=(
                f"Effect size not estimable for {metric}: at least two "
                f"repetitions per manager are required "
                f"(n_conv={len(conv_values)}, n_posd={len(posd_values)})."
            ),
            test_performed="",
            p_value=None,
        )

    d = _cohens_d(conv_values, posd_values)
    mag = _magnitude(d)

    # Interpret direction relative to higher_is_better
    if abs(d) < 0.2:
        interp = f"No meaningful difference in {metric} (|d|={abs(d):.2f})."
    else:
        winner = "Conventional" if d > 0 else "POSDCORB"
        if higher_is_better:
            better = winner
        else:
            better = "POSDCORB" if d > 0 else "Conventional"
        interp = (
            f"{better} shows {mag} advantage in {metric} "
            f"(Cohen's d={d:.2f}, conv={m_c:.3f}, posd={m_p:.3f})."
        )

    test_result = _welch_t_test(conv_values, posd_values)
    test_name = "welch_t" if test_result else ""
    p_val = round(test_result[1], 6) if test_result else None

    return EffectSize(
        config_id=config_id,
        metric=metric,
        task_count=task_count,
        complexity=complexity,
        dep_pct=dep_pct,
        failure_prob=failure_prob,
        max_agents=max_agents,
        mean_conventional=round(m_c, 6),
        mean_posdcorb=round(m_p, 6),
        cohens_d=d,
        magnitude=mag,
        interpretation=interp,
        test_performed=test_name,
        p_value=p_val,
    )


def _chart_point(
    x_value: float,
    x_label: str,
    conv: list[float],
    posd: list[float],
) -> ChartPoint:
    def _s(vs: list[float]) -> tuple[float, float, float, float]:
        if not vs:
            return 0.0, 0.0, 0.0, 0.0
        return (
            round(statistics.mean(vs), 4),
            round(statistics.stdev(vs) if len(vs) >= 2 else 0.0, 4),
            round(min(vs), 4),
            round(max(vs), 4),
        )

    cm, cs, cmin, cmax = _s(conv)
    pm, ps, pmin, pmax = _s(posd)
    return ChartPoint(
        x_value=x_value,
        x_label=x_label,
        conventional_mean=cm,
        conventional_std=cs,
        conventional_min=cmin,
        conventional_max=cmax,
        posdcorb_mean=pm,
        posdcorb_std=ps,
        posdcorb_min=pmin,
        posdcorb_max=pmax,
        n_reps=max(len(conv), len(posd)),
    )


def _resolve_agents(agents_payload: list | None) -> list[Agent]:
    """
    Return the worker pool for the batch.

    Priority: explicit payload → saved agents → deterministic built-in pool.
    The built-in fallback mirrors the ablation study, so the analysis suite is
    runnable (and reproducible) without any prior DB setup.  A fresh database
    with no ``agents`` table must not surface a raw sqlite error.
    """
    if agents_payload is not None:
        return [Agent(**a) if isinstance(a, dict) else a for a in agents_payload]

    try:
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT agent_json FROM agents ORDER BY created_at ASC"
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        logger.warning(
            "Could not read the saved agent pool (%s); using the deterministic "
            "built-in pool for this batch.",
            exc,
        )
        rows = []

    if not rows:
        logger.warning(
            "No saved agents available; using the deterministic built-in pool "
            "for this batch. Pass 'agents' to make the pool explicit."
        )
        from app.ablation.defaults import default_agents

        return default_agents()

    return [Agent(**json.loads(r["agent_json"])) for r in rows]


def _run_single(
    *,
    task_count: int,
    complexity: str,
    dep_pct: float,
    failure_prob: float,
    max_agents: int,
    seed: int,
    rep: int,
    agents: list[Agent],
    max_decisions: int,
    max_wall_ms: float,
) -> tuple[RunRecord, RunRecord]:
    """
    Run both managers for one config × seed combination.

    Returns (conventional_record, posdcorb_record).
    Both managers see the exact same TaskGraph, agents, and seed.
    """
    config_id = _config_id(task_count, complexity, dep_pct, failure_prob, max_agents)

    wl_config = WorkloadGeneratorConfig(
        num_tasks=task_count,
        complexity=complexity,  # type: ignore[arg-type]
        dependency_percentage=dep_pct,
        seed=seed,
    )
    graph = generate_workload(wl_config)

    sim_config = SimulationConfig(
        manager_types=["conventional", "posdcorb"],
        rng_seed=seed,
        max_decisions=max_decisions,
        max_wall_ms=max_wall_ms,
        max_agents=max_agents,
        failure_probability=failure_prob if failure_prob > 0 else None,
    )

    engine = SimulationEngine()
    result = engine.run(graph, agents, sim_config)

    records: list[RunRecord] = []
    for manager in ("conventional", "posdcorb"):
        exp = result.experiments.get(manager)
        err = result.errors.get(manager, "")

        if exp is None:
            # Manager errored — create a zero record
            records.append(RunRecord(
                run_id=str(_uuid.uuid4()),
                config_id=config_id,
                rep=rep,
                seed=seed,
                task_count=task_count,
                complexity=complexity,
                dep_pct=dep_pct,
                failure_prob=failure_prob,
                max_agents=max_agents,
                manager=manager,
                completed_tasks=0,
                failed_tasks=task_count,
                success_rate=0.0,
                total_wall_ms=0.0,
                throughput_tasks_per_sec=0.0,
                avg_task_latency_ms=0.0,
                total_decisions=0,
                coordination_overhead_ratio=0.0,
                total_active_agents=0,
                activation_count=0,
                avg_agent_utilization=0.0,
                recovery_count=0,
                recovery_success_rate=0.0,
                termination_reason="error",
                error=err,
            ))
            continue

        # Compute derived metrics via MetricsEngine
        derived = _metrics_engine.compute(exp)
        perf = derived.performance
        mgmt = derived.management
        ag = derived.agents
        rel = derived.reliability

        records.append(RunRecord(
            run_id=str(_uuid.uuid4()),
            config_id=config_id,
            rep=rep,
            seed=seed,
            task_count=task_count,
            complexity=complexity,
            dep_pct=dep_pct,
            failure_prob=failure_prob,
            max_agents=max_agents,
            manager=manager,
            completed_tasks=perf.completed_tasks,
            failed_tasks=perf.failed_tasks,
            success_rate=round(perf.task_success_rate, 6),
            total_wall_ms=round(perf.total_execution_latency_ms, 2),
            throughput_tasks_per_sec=round(perf.throughput_tasks_per_sec, 6),
            avg_task_latency_ms=round(perf.avg_task_latency_ms, 4),
            total_decisions=mgmt.total_decisions,
            coordination_overhead_ratio=round(mgmt.coordination_overhead_ratio, 6),
            total_active_agents=ag.total_active_agents,
            activation_count=ag.activation_count,
            avg_agent_utilization=round(ag.avg_agent_utilization, 6),
            recovery_count=rel.recovery_count,
            recovery_success_rate=round(rel.recovery_success_rate, 6),
            termination_reason=exp.termination_reason,
            error=err,
        ))

    return records[0], records[1]


def _aggregate_runs(
    all_runs: list[RunRecord],
) -> tuple[list[ConfigStats], list[EffectSize]]:
    """
    Aggregate raw runs into per-config stats and effect sizes.
    """
    # Group by (config_id, manager)
    from collections import defaultdict
    groups: dict[tuple[str, str], list[RunRecord]] = defaultdict(list)
    for r in all_runs:
        groups[(r.config_id, r.manager)].append(r)

    stats_list: list[ConfigStats] = []
    effect_list: list[EffectSize] = []

    # Collect all unique config_ids
    config_ids = sorted({r.config_id for r in all_runs})

    for cid in config_ids:
        conv_runs = groups.get((cid, "conventional"), [])
        posd_runs = groups.get((cid, "posdcorb"), [])
        any_run = (conv_runs or posd_runs)[0] if (conv_runs or posd_runs) else None
        if any_run is None:
            continue

        tc = any_run.task_count
        cx = any_run.complexity
        dp = any_run.dep_pct
        fp = any_run.failure_prob
        ma = any_run.max_agents

        for manager_label, runs in (("conventional", conv_runs), ("posdcorb", posd_runs)):
            if not runs:
                continue
            stats_list.append(ConfigStats(
                config_id=cid,
                manager=manager_label,
                task_count=tc,
                complexity=cx,
                dep_pct=dp,
                failure_prob=fp,
                max_agents=ma,
                n_reps=len(runs),
                success_rate=_stats([r.success_rate for r in runs]),
                throughput=_stats([r.throughput_tasks_per_sec for r in runs]),
                avg_latency_ms=_stats([r.avg_task_latency_ms for r in runs]),
                total_wall_ms=_stats([r.total_wall_ms for r in runs]),
                coordination_overhead=_stats([r.coordination_overhead_ratio for r in runs]),
                active_agents=_stats([float(r.total_active_agents) for r in runs]),
                recovery_rate=_stats([r.recovery_success_rate for r in runs]),
                avg_agent_utilization=_stats([r.avg_agent_utilization for r in runs]),
            ))

        # Effect sizes (between managers for this config)
        _metric_defs = [
            ("success_rate",              [r.success_rate for r in conv_runs],              [r.success_rate for r in posd_runs],              True),
            ("throughput",                [r.throughput_tasks_per_sec for r in conv_runs],  [r.throughput_tasks_per_sec for r in posd_runs],  True),
            ("avg_task_latency_ms",       [r.avg_task_latency_ms for r in conv_runs],       [r.avg_task_latency_ms for r in posd_runs],       False),
            ("total_wall_ms",             [r.total_wall_ms for r in conv_runs],             [r.total_wall_ms for r in posd_runs],             False),
            ("coordination_overhead",     [r.coordination_overhead_ratio for r in conv_runs],[r.coordination_overhead_ratio for r in posd_runs],False),
            ("active_agents",             [float(r.total_active_agents) for r in conv_runs],[float(r.total_active_agents) for r in posd_runs],True),
            ("recovery_rate",             [r.recovery_success_rate for r in conv_runs],     [r.recovery_success_rate for r in posd_runs],     True),
        ]
        for m_name, c_vals, p_vals, higher_better in _metric_defs:
            if c_vals or p_vals:
                effect_list.append(_effect_size(
                    metric=m_name,
                    config_id=cid,
                    task_count=tc,
                    complexity=cx,
                    dep_pct=dp,
                    failure_prob=fp,
                    max_agents=ma,
                    conv_values=c_vals,
                    posd_values=p_vals,
                    higher_is_better=higher_better,
                ))

    return stats_list, effect_list


def _build_chart_data(all_runs: list[RunRecord]) -> BatchChartData:
    """Build pre-binned chart series grouped by task_count and failure_prob."""
    from collections import defaultdict

    # Group by (task_count, manager) for charts A-F
    tc_conv: dict[int, list[RunRecord]] = defaultdict(list)
    tc_posd: dict[int, list[RunRecord]] = defaultdict(list)
    # Group by (failure_prob, manager) for chart G
    fp_conv: dict[float, list[RunRecord]] = defaultdict(list)
    fp_posd: dict[float, list[RunRecord]] = defaultdict(list)

    for r in all_runs:
        if r.manager == "conventional":
            tc_conv[r.task_count].append(r)
            fp_conv[r.failure_prob].append(r)
        else:
            tc_posd[r.task_count].append(r)
            fp_posd[r.failure_prob].append(r)

    all_tcs = sorted(set(list(tc_conv.keys()) + list(tc_posd.keys())))
    all_fps = sorted(set(list(fp_conv.keys()) + list(fp_posd.keys())))

    def _tc_chart(metric_fn) -> list[ChartPoint]:
        pts = []
        for tc in all_tcs:
            c_vals = [metric_fn(r) for r in tc_conv.get(tc, [])]
            p_vals = [metric_fn(r) for r in tc_posd.get(tc, [])]
            pts.append(_chart_point(float(tc), str(tc), c_vals, p_vals))
        return pts

    def _fp_chart(metric_fn) -> list[ChartPoint]:
        pts = []
        for fp in all_fps:
            c_vals = [metric_fn(r) for r in fp_conv.get(fp, [])]
            p_vals = [metric_fn(r) for r in fp_posd.get(fp, [])]
            pts.append(_chart_point(fp, f"{fp:.2f}", c_vals, p_vals))
        return pts

    return BatchChartData(
        task_count_vs_latency=_tc_chart(lambda r: r.avg_task_latency_ms),
        task_count_vs_throughput=_tc_chart(lambda r: r.throughput_tasks_per_sec),
        task_count_vs_success_rate=_tc_chart(lambda r: r.success_rate),
        task_count_vs_coordination_overhead=_tc_chart(lambda r: r.coordination_overhead_ratio),
        task_count_vs_active_agents=_tc_chart(lambda r: float(r.total_active_agents)),
        failure_prob_vs_recovery_rate=_fp_chart(lambda r: r.recovery_success_rate),
        task_count_vs_cpu_note=(
            "Host CPU/memory usage is not plotted here. The simulator reports "
            "a simulated CPU utilisation (execution time / total wall time), "
            "not host-machine CPU."
        ),
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("/analysis/batch", response_model=BatchResponse)
def run_batch(body: BatchRequest) -> BatchResponse:
    """
    Run a parameter sweep comparing both managers.

    The cross-product of all axis lists defines the set of configurations.
    Each configuration is run ``repetitions`` times with seeds
    base_seed, base_seed+1, …, base_seed+repetitions-1.
    """
    # Validate complexities
    valid_cx = {"low", "medium", "high"}
    bad_cx = [c for c in body.complexities if c not in valid_cx]
    if bad_cx:
        raise HTTPException(422, f"Invalid complexity values: {bad_cx}. Must be one of {valid_cx}.")

    # Validate managers
    available = registered_types()
    for m in ("conventional", "posdcorb"):
        if m not in available:
            raise HTTPException(422, f"Manager '{m}' is not registered.")

    # Validate failure probs
    for fp in body.failure_probs:
        if not (0.0 <= fp <= 1.0):
            raise HTTPException(422, f"failure_prob {fp} out of range [0.0, 1.0].")

    # Enumerate configurations
    configs = [
        (tc, cx, dp, fp, ma)
        for tc in body.task_counts
        for cx in body.complexities
        for dp in body.dep_percentages
        for fp in body.failure_probs
        for ma in body.max_agents_list
    ]
    n_configs = len(configs)
    total_runs = n_configs * body.repetitions * 2  # × 2 managers

    if n_configs > MAX_CONFIGS:
        raise HTTPException(
            422,
            f"Too many configurations ({n_configs}). Reduce parameter lists to stay under {MAX_CONFIGS}.",
        )
    if total_runs > MAX_TOTAL_RUNS:
        raise HTTPException(
            422,
            f"Too many total runs ({total_runs}). Reduce parameter lists or repetitions to stay under {MAX_TOTAL_RUNS}.",
        )

    # Resolve agent pool
    agents = _resolve_agents(body.agents)

    batch_id = str(_uuid.uuid4())
    all_runs: list[RunRecord] = []
    errors: list[str] = []
    t0 = time.perf_counter()

    for tc, cx, dp, fp, ma in configs:
        for rep in range(body.repetitions):
            seed = body.base_seed + rep
            try:
                conv_rec, posd_rec = _run_single(
                    task_count=tc,
                    complexity=cx,
                    dep_pct=dp,
                    failure_prob=fp,
                    max_agents=ma,
                    seed=seed,
                    rep=rep,
                    agents=agents,
                    max_decisions=body.max_decisions,
                    max_wall_ms=body.max_wall_ms,
                )
                all_runs.append(conv_rec)
                all_runs.append(posd_rec)
                if conv_rec.error:
                    errors.append(f"[tc={tc} cx={cx} fp={fp} rep={rep}] conventional: {conv_rec.error}")
                if posd_rec.error:
                    errors.append(f"[tc={tc} cx={cx} fp={fp} rep={rep}] posdcorb: {posd_rec.error}")
            except Exception as exc:  # noqa: BLE001
                errors.append(f"[tc={tc} cx={cx} fp={fp} rep={rep}] fatal: {exc}")

    elapsed = round(time.perf_counter() - t0, 4)
    stats_list, effect_list = _aggregate_runs(all_runs)
    chart_data = _build_chart_data(all_runs)

    return BatchResponse(
        batch_id=batch_id,
        total_configs=n_configs,
        total_runs=len(all_runs),
        elapsed_s=elapsed,
        raw_runs=all_runs,
        stats=stats_list,
        effect_sizes=effect_list,
        chart_data=chart_data,
        errors=errors,
    )


@router.get("/analysis/batch/csv")
def batch_csv_export(batch_id: str = "") -> PlainTextResponse:
    """
    CSV export is not available server-side.

    Batch results are computed synchronously by POST /analysis/batch and are
    not stored, so a GET keyed only by ``batch_id`` cannot reproduce them.
    Returning a header-only file would look like a successful export; fail
    explicitly instead.  The frontend generates CSV from ``raw_runs``.
    """
    raise HTTPException(
        status_code=501,
        detail=(
            "Batch results are not persisted; export CSV from the POST "
            "/analysis/batch response (raw_runs) on the client."
        ),
    )
