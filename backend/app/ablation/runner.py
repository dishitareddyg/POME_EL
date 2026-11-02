"""
POSDCORB ablation study runner.

Runs the nine manager configurations from
:func:`app.ablation.configs.build_ablation_ladder` against an identical
workload, agent pool and resource envelope, then reports:

    latency, throughput, success rate, CPU utilisation,
    coordination overhead, agent utilisation, activation count

Everything the study needs to stay fair is centralised here:

* one workload graph per repetition, shared by every configuration
* one agent pool per repetition, deep-copied by the engine for every run
* one seed per repetition, identical for every configuration
* one resource envelope (wall clock, decisions, agents, failure probability)

Improvements are *directional*: a positive number always means "better than
the reference", regardless of whether the metric is better when higher
(throughput) or lower (latency).  Activation count has no inherent direction
and is reported as a signed delta only.
"""
from __future__ import annotations

import hashlib
import json
import statistics
import time
from dataclasses import dataclass, field
from typing import Any, Iterable

from app.ablation.configs import (
    AblationConfiguration,
    build_ablation_ladder,
)
from app.metrics.engine import MetricsEngine
from app.metrics.models import DerivedMetrics
from app.models.agent import Agent
from app.models.workload import TaskGraph, WorkloadGeneratorConfig
from app.simulation.engine import (
    ExperimentResult,
    SimulationConfig,
    SimulationEngine,
)
from app.simulation.workload_generator import generate_workload

# ---------------------------------------------------------------------------
# Metric definitions
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MetricSpec:
    key: str
    label: str
    unit: str
    # True  → higher is better (throughput, success, utilisation)
    # False → lower is better (latency, overhead)
    # None  → context-dependent / descriptive (activation count)
    higher_is_better: bool | None
    description: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "unit": self.unit,
            "higher_is_better": self.higher_is_better,
            "description": self.description,
        }


METRIC_SPECS: tuple[MetricSpec, ...] = (
    MetricSpec(
        key="latency_ms",
        label="Avg task latency",
        unit="ms",
        higher_is_better=False,
        description=(
            "Mean queue + execution time of completed tasks. Lower is better."
        ),
    ),
    MetricSpec(
        key="throughput_tasks_per_sec",
        label="Throughput",
        unit="tasks/s",
        higher_is_better=True,
        description="Completed tasks per second of simulated wall time.",
    ),
    MetricSpec(
        key="success_rate",
        label="Success rate",
        unit="%",
        higher_is_better=True,
        description="Completed tasks as a percentage of all tasks.",
    ),
    MetricSpec(
        key="cpu_utilization",
        label="CPU utilisation",
        unit="%",
        higher_is_better=True,
        description=(
            "Simulated task-execution CPU fraction: execution time divided "
            "by total wall time, capped at 100%. Higher means less idle CPU."
        ),
    ),
    MetricSpec(
        key="coordination_overhead",
        label="Coordination overhead",
        unit="%",
        higher_is_better=False,
        description=(
            "Manager decision time as a percentage of total wall time. "
            "Lower is better."
        ),
    ),
    MetricSpec(
        key="agent_utilization",
        label="Agent utilisation",
        unit="%",
        higher_is_better=True,
        description="Mean fraction of wall time that agents spent executing.",
    ),
    MetricSpec(
        key="activation_count",
        label="Agent activations",
        unit="count",
        higher_is_better=None,
        description=(
            "Number of dynamic agents activated by Staffing. Context-"
            "dependent: useful only if the extra capacity offsets the "
            "activation cost."
        ),
    ),
)

METRIC_BY_KEY: dict[str, MetricSpec] = {m.key: m for m in METRIC_SPECS}

# Trace event types that prove a mechanism actually ran.  These are
# diagnostics only: they show which principles were active even when their
# performance delta is zero under the tested workload.
MECHANISM_EVENTS: dict[str, frozenset[str]] = {
    "planning": frozenset({"posdcorb:PLAN_CREATED", "posdcorb:PLAN_UPDATED"}),
    "organizing": frozenset(
        {"posdcorb:TASK_GROUP_CREATED", "posdcorb:TASK_REORGANIZED"}
    ),
    "staffing": frozenset(
        {"posdcorb:AGENT_ACTIVATED", "posdcorb:AGENT_REASSIGNED"}
    ),
    "directing": frozenset({"posdcorb:TASK_DIRECTED"}),
    "coordinating": frozenset(
        {
            "posdcorb:TASK_SCHEDULED",
            "posdcorb:TASK_STARTED",
            "posdcorb:TASK_COMPLETED",
            "posdcorb:TASK_REASSIGNED",
            "posdcorb:DEPENDENCY_WAIT",
        }
    ),
    "reporting": frozenset({"posdcorb:STATUS_UPDATED"}),
    "budgeting": frozenset(
        {"posdcorb:RESOURCE_CHECK", "posdcorb:BUDGET_WARNING"}
    ),
    "adaptive": frozenset(
        {
            "posdcorb:PLAN_UPDATED",
            "posdcorb:RECOVERY_COMPLETED",
            "posdcorb:AGENT_REASSIGNED",
        }
    ),
}

# Activation count is descriptive; excluding it from the composite avoids
# rewarding activation for its own sake.
_COMPOSITE_KEYS: tuple[str, ...] = tuple(
    m.key for m in METRIC_SPECS if m.higher_is_better is not None
)


# ---------------------------------------------------------------------------
# Result containers
# ---------------------------------------------------------------------------


@dataclass
class MetricSummary:
    """Mean / spread of one metric across repetitions."""

    mean: float = 0.0
    std: float = 0.0
    min: float = 0.0
    max: float = 0.0
    n: int = 0

    @classmethod
    def from_values(cls, values: Iterable[float]) -> "MetricSummary":
        vals = [float(v) for v in values]
        if not vals:
            return cls()
        return cls(
            mean=statistics.mean(vals),
            std=statistics.stdev(vals) if len(vals) >= 2 else 0.0,
            min=min(vals),
            max=max(vals),
            n=len(vals),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "mean": round(self.mean, 4),
            "std": round(self.std, 4),
            "min": round(self.min, 4),
            "max": round(self.max, 4),
            "n": self.n,
        }


@dataclass
class Improvement:
    """Directional improvement of one metric relative to a reference."""

    metric: str
    reference_value: float
    value: float
    absolute_delta: float
    pct_change: float | None
    improvement_pct: float | None
    higher_is_better: bool | None
    direction: str  # "better" | "worse" | "equal" | "n/a"

    def as_dict(self) -> dict[str, Any]:
        def _r(v: float | None) -> float | None:
            return None if v is None else round(v, 4)

        return {
            "metric": self.metric,
            "reference_value": _r(self.reference_value),
            "value": _r(self.value),
            "absolute_delta": _r(self.absolute_delta),
            "pct_change": _r(self.pct_change),
            "improvement_pct": _r(self.improvement_pct),
            "higher_is_better": self.higher_is_better,
            "direction": self.direction,
        }


@dataclass
class RawRun:
    """One (configuration × repetition) execution, kept for auditability."""

    config_id: str
    repetition: int
    seed: int
    metrics: dict[str, float]
    completed_tasks: int
    failed_tasks: int
    pending_tasks: int
    total_wall_ms: float
    total_decisions: int
    termination_reason: str
    error: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "config_id": self.config_id,
            "repetition": self.repetition,
            "seed": self.seed,
            "metrics": {k: round(v, 4) for k, v in self.metrics.items()},
            "completed_tasks": self.completed_tasks,
            "failed_tasks": self.failed_tasks,
            "pending_tasks": self.pending_tasks,
            "total_wall_ms": round(self.total_wall_ms, 2),
            "total_decisions": self.total_decisions,
            "termination_reason": self.termination_reason,
            "error": self.error,
        }


@dataclass
class ConfigurationResult:
    """Aggregated result for one configuration across all repetitions."""

    configuration: AblationConfiguration
    summaries: dict[str, MetricSummary]
    raw_runs: list[RawRun] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    # Mean mechanism-event counts across repetitions (diagnostic evidence).
    mechanism_activity: dict[str, float] = field(default_factory=dict)
    # Mean of the directional improvement across non-activation metrics.
    composite_improvement: float | None = None
    rank: int | None = None
    improvements: dict[str, Improvement] = field(default_factory=dict)
    marginal: dict[str, Improvement] = field(default_factory=dict)
    marginal_composite: float | None = None

    @property
    def id(self) -> str:
        return self.configuration.id

    def mean(self, key: str) -> float:
        return self.summaries[key].mean if key in self.summaries else 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            **self.configuration.as_dict(),
            "metrics": {
                key: {
                    "value": round(self.mean(key), 4),
                    **self.summaries[key].as_dict(),
                }
                for key in METRIC_BY_KEY
            },
            "improvements_vs_baseline": {
                k: v.as_dict() for k, v in self.improvements.items()
            },
            "mechanism_activity": {
                k: round(v, 2) for k, v in self.mechanism_activity.items()
            },
            "marginal_vs_previous": {
                k: v.as_dict() for k, v in self.marginal.items()
            },
            "marginal_composite_improvement": (
                None
                if self.marginal_composite is None
                else round(self.marginal_composite, 4)
            ),
            "composite_improvement": (
                None
                if self.composite_improvement is None
                else round(self.composite_improvement, 4)
            ),
            "rank": self.rank,
            "raw_runs": [r.as_dict() for r in self.raw_runs],
            "errors": self.errors,
        }


@dataclass
class AblationStudyResult:
    study_id: str
    conditions: dict[str, Any]
    configurations: list[ConfigurationResult]
    ranking: list[dict[str, Any]]
    principle_contributions: list[dict[str, Any]]
    metric_specs: list[dict[str, Any]] = field(
        default_factory=lambda: [m.as_dict() for m in METRIC_SPECS]
    )
    total_runs: int = 0
    elapsed_s: float = 0.0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "study_id": self.study_id,
            "conditions": self.conditions,
            "configurations": [c.as_dict() for c in self.configurations],
            "ranking": self.ranking,
            "principle_contributions": self.principle_contributions,
            "metric_specs": self.metric_specs,
            "total_runs": self.total_runs,
            "elapsed_s": round(self.elapsed_s, 4),
            "errors": self.errors,
        }


# ---------------------------------------------------------------------------
# Metric extraction / comparison helpers
# ---------------------------------------------------------------------------


def _extract_metrics(result: ExperimentResult, metrics: DerivedMetrics) -> dict[str, float]:
    """Map a completed run onto the study's seven reported metrics."""
    wall_ms = max(float(result.execution_trace.total_wall_ms), 0.0)
    exec_ms = float(result.manager_statistics.get("execution_time_ms", 0.0) or 0.0)
    cpu = min(100.0, (exec_ms / wall_ms) * 100.0) if wall_ms > 0 else 0.0

    return {
        "latency_ms": float(metrics.performance.avg_task_latency_ms),
        "throughput_tasks_per_sec": float(
            metrics.performance.throughput_tasks_per_sec
        ),
        "success_rate": float(metrics.performance.task_success_rate) * 100.0,
        "cpu_utilization": cpu,
        "coordination_overhead": float(
            metrics.management.coordination_overhead_ratio
        ) * 100.0,
        "agent_utilization": float(metrics.agents.avg_agent_utilization) * 100.0,
        "activation_count": float(metrics.agents.activation_count),
    }


def _improvement(
    key: str,
    reference_value: float,
    value: float,
) -> Improvement:
    spec = METRIC_BY_KEY[key]
    absolute_delta = value - reference_value
    if abs(reference_value) > 1e-12:
        pct_change: float | None = absolute_delta / abs(reference_value) * 100.0
    else:
        pct_change = 0.0 if abs(value) <= 1e-12 else None

    if spec.higher_is_better is None:
        improvement_pct = None
        direction = (
            "equal" if abs(absolute_delta) <= 1e-9
            else ("higher" if absolute_delta > 0 else "lower")
        )
    elif pct_change is None:
        improvement_pct = None
        direction = "n/a"
    else:
        improvement_pct = (
            pct_change if spec.higher_is_better else -pct_change
        )
        if abs(improvement_pct) <= 1e-9:
            direction = "equal"
        elif improvement_pct > 0:
            direction = "better"
        else:
            direction = "worse"

    return Improvement(
        metric=key,
        reference_value=reference_value,
        value=value,
        absolute_delta=absolute_delta,
        pct_change=pct_change,
        improvement_pct=improvement_pct,
        higher_is_better=spec.higher_is_better,
        direction=direction,
    )


def _build_improvements(
    means: dict[str, float],
    reference: dict[str, float],
) -> dict[str, Improvement]:
    return {
        key: _improvement(key, reference[key], means[key])
        for key in METRIC_BY_KEY
    }


def _composite(improvements: dict[str, Improvement]) -> float | None:
    values = [
        improvements[key].improvement_pct
        for key in _COMPOSITE_KEYS
        if improvements.get(key) is not None
        and improvements[key].improvement_pct is not None
    ]
    if not values:
        return None
    return statistics.mean(values)


def _ranking_key(result: ConfigurationResult) -> float:
    # Configurations without a composite score sort last.
    return (
        result.composite_improvement
        if result.composite_improvement is not None
        else float("-inf")
    )


# ---------------------------------------------------------------------------
# Conditions fingerprint
# ---------------------------------------------------------------------------


def _fingerprint(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _agent_signature(agents: list[Agent]) -> list[dict[str, Any]]:
    return [
        {
            "id": a.id,
            "name": a.name,
            "role": a.role,
            "capabilities": sorted(a.capabilities),
            "processing_speed": a.processing_speed,
            "reliability": a.reliability,
            "max_concurrent_tasks": a.max_concurrent_tasks,
            "origin": a.origin,
        }
        for a in agents
    ]


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def _manager_options(
    configuration: AblationConfiguration,
    max_decisions: int,
) -> dict[str, Any]:
    """Build the constructor options injected through the manager registry."""
    if configuration.manager_type != "posdcorb":
        return {}

    # Imported lazily so the ablation package never forces the engine to know
    # about POSDCORB.
    from app.managers.posdcorb import POSDCORBConfig

    return {
        "config": POSDCORBConfig(
            enabled_principles=configuration.enabled_principles,
            adaptive_mechanisms=configuration.adaptive_mechanisms,
            max_manager_decisions=max_decisions,
        )
    }


def run_ablation_study(
    *,
    agents: list[Agent],
    workload_config: WorkloadGeneratorConfig,
    configurations: list[AblationConfiguration] | None = None,
    repetitions: int = 1,
    base_seed: int = 42,
    max_decisions: int = 5_000,
    max_wall_ms: float = 120_000.0,
    max_agents: int | None = None,
    failure_probability: float | None = 0.0,
    study_id: str = "ablation",
) -> AblationStudyResult:
    """
    Execute every configuration under identical experimental conditions.

    Parameters
    ----------
    agents:
        Full pool (initial + dynamic).  Deep-copied for every run.
    workload_config:
        Workload generator settings.  For repetition ``r`` the seed is
        ``base_seed + r`` and that graph is shared by *all* configurations.
    configurations:
        Defaults to the canonical nine-rung ladder.
    repetitions:
        Number of independent workloads.  All configurations run on each.
    """
    ladder = configurations or build_ablation_ladder()
    if not ladder:
        raise ValueError("At least one configuration is required.")
    if not any(c.is_baseline for c in ladder):
        raise ValueError("A baseline (conventional) configuration is required.")

    repetitions = max(1, int(repetitions))
    engine = SimulationEngine()
    metrics_engine = MetricsEngine()

    # One fingerprint covers inputs that must not vary between configurations.
    agent_sig = _agent_signature(agents)
    conditions: dict[str, Any] = {
        "workload": workload_config.model_dump(mode="json"),
        "agent_pool": {
            "total": len(agents),
            "initial": sum(1 for a in agents if a.origin == "initial"),
            "dynamic": sum(1 for a in agents if a.origin == "dynamic"),
            "signature": agent_sig,
        },
        "simulation": {
            "base_seed": base_seed,
            "repetitions": repetitions,
            "seeds": [base_seed + r for r in range(repetitions)],
            "max_decisions": max_decisions,
            "max_wall_ms": max_wall_ms,
            "max_agents": max_agents,
            "failure_probability": failure_probability,
        },
    }
    conditions["fingerprint"] = _fingerprint(conditions)
    conditions["note"] = (
        "Every configuration runs on the same generated graph, the same agent "
        "pool and the same resource envelope for each repetition. Only the "
        "manager strategy and enabled POSDCORB principles differ."
    )

    # Pre-generate the graphs so no configuration can observe a different one.
    graphs: list[TaskGraph] = [
        generate_workload(
            workload_config.model_copy(
                update={"seed": base_seed + rep}
            )
        )
        for rep in range(repetitions)
    ]

    # Record the identity of every run's inputs so ``identical_inputs`` is a
    # verified observation rather than a hard-coded claim.
    run_inputs: dict[int, set[tuple[Any, ...]]] = {}

    elapsed_s = 0.0
    total_runs = 0
    study_errors: list[str] = []
    results: list[ConfigurationResult] = []

    for configuration in ladder:
        raw_runs: list[RawRun] = []
        config_errors: list[str] = []
        values: dict[str, list[float]] = {k: [] for k in METRIC_BY_KEY}
        activity_values: dict[str, list[float]] = {
            key: [] for key in MECHANISM_EVENTS
        }

        for rep, graph in enumerate(graphs):
            seed = base_seed + rep
            sim_config = SimulationConfig(
                manager_types=[configuration.manager_type],
                rng_seed=seed,
                max_wall_ms=max_wall_ms,
                max_decisions=max_decisions,
                max_agents=max_agents,
                failure_probability=failure_probability,
                experiment_id=f"ablation::{configuration.id}::rep{rep}",
                manager_options=_manager_options(configuration, max_decisions),
            )

            run_elapsed_start = time.perf_counter()
            result = engine.run_experiment(
                graph,
                agents,
                sim_config,
                manager_type=configuration.manager_type,
            )
            elapsed_s += time.perf_counter() - run_elapsed_start
            total_runs += 1

            run_inputs.setdefault(rep, set()).add(
                (
                    result.workload_id,
                    seed,
                    int(max_decisions),
                    float(max_wall_ms),
                    max_agents,
                    failure_probability,
                )
            )

            if result.error:
                message = (
                    f"{configuration.id} rep {rep}: {result.error}"
                )
                config_errors.append(message)
                study_errors.append(message)

            metrics = metrics_engine.compute(result)
            extracted = _extract_metrics(result, metrics)
            for key, value in extracted.items():
                values[key].append(value)

            # Diagnostic mechanism activity (mean events per repetition).
            counts: dict[str, int] = {key: 0 for key in MECHANISM_EVENTS}
            for decision in result.execution_trace.decisions:
                for mechanism, event_types in MECHANISM_EVENTS.items():
                    if decision.manager_decision in event_types:
                        counts[mechanism] += 1
            for key, count in counts.items():
                activity_values[key].append(float(count))

            raw_runs.append(
                RawRun(
                    config_id=configuration.id,
                    repetition=rep,
                    seed=seed,
                    metrics=extracted,
                    completed_tasks=result.completed_tasks,
                    failed_tasks=result.failed_tasks,
                    pending_tasks=result.pending_tasks,
                    total_wall_ms=float(result.total_duration_ms),
                    total_decisions=int(
                        result.resource_usage.get("decisions_used", 0)
                        or sum(
                            result.manager_statistics.get(
                                "decisions_by_type", {}
                            ).values()
                        )
                    ),
                    termination_reason=result.termination_reason,
                    error=result.error,
                )
            )

        summaries = {
            key: MetricSummary.from_values(vals)
            for key, vals in values.items()
        }
        activity = {
            key: (
                statistics.mean(vals) if vals else 0.0
            )
            for key, vals in activity_values.items()
        }
        results.append(
            ConfigurationResult(
                configuration=configuration,
                summaries=summaries,
                raw_runs=raw_runs,
                errors=config_errors,
                mechanism_activity=activity,
            )
        )

    # -- Improvements vs the baseline -----------------------------------
    baseline = next(r for r in results if r.configuration.is_baseline)
    baseline_means = {k: baseline.mean(k) for k in METRIC_BY_KEY}
    for result in results:
        means = {k: result.mean(k) for k in METRIC_BY_KEY}
        result.improvements = _build_improvements(means, baseline_means)
        result.composite_improvement = _composite(result.improvements)

    # -- Marginal contribution vs the previous cumulative rung ----------
    for index, result in enumerate(results):
        if index == 0:
            continue
        previous = results[index - 1]
        prev_means = {k: previous.mean(k) for k in METRIC_BY_KEY}
        means = {k: result.mean(k) for k in METRIC_BY_KEY}
        result.marginal = _build_improvements(means, prev_means)
        result.marginal_composite = _composite(result.marginal)

    # -- Ranking --------------------------------------------------------
    ranked = sorted(results, key=_ranking_key, reverse=True)
    for position, result in enumerate(ranked, start=1):
        result.rank = position

    ranking = [
        {
            "rank": result.rank,
            "config_id": result.id,
            "label": result.configuration.label,
            "short_label": result.configuration.short_label,
            "composite_improvement": (
                None
                if result.composite_improvement is None
                else round(result.composite_improvement, 4)
            ),
            "latency_improvement": _rounded(
                result.improvements["latency_ms"].improvement_pct
            ),
            "throughput_improvement": _rounded(
                result.improvements["throughput_tasks_per_sec"].improvement_pct
            ),
            "success_improvement": _rounded(
                result.improvements["success_rate"].improvement_pct
            ),
            "cpu_improvement": _rounded(
                result.improvements["cpu_utilization"].improvement_pct
            ),
            "overhead_improvement": _rounded(
                result.improvements["coordination_overhead"].improvement_pct
            ),
            "agent_utilization_improvement": _rounded(
                result.improvements["agent_utilization"].improvement_pct
            ),
        }
        for result in ranked
    ]

    # -- Per-principle contributions ------------------------------------
    contributions: list[dict[str, Any]] = []
    for index, result in enumerate(results):
        cfg = result.configuration
        if cfg.is_baseline or cfg.added_principle is None or index == 0:
            continue
        contributions.append(
            {
                "principle": cfg.added_principle,
                "label": cfg.added_label,
                "config_id": cfg.id,
                "config_label": cfg.label,
                "compared_to": results[index - 1].id,
                "composite_improvement": _rounded(result.marginal_composite),
                "effects": {
                    key: {
                        "absolute_delta": round(imp.absolute_delta, 4),
                        "improvement_pct": _rounded(imp.improvement_pct),
                        "direction": imp.direction,
                    }
                    for key, imp in result.marginal.items()
                },
            }
        )
    contributions.sort(
        key=lambda c: (
            c["composite_improvement"]
            if c["composite_improvement"] is not None
            else float("-inf")
        ),
        reverse=True,
    )

    # -- Verify that every configuration really saw identical inputs ------
    graph_inputs_identical = (
        len(run_inputs) == repetitions
        and all(len(seen) == 1 for seen in run_inputs.values())
    )
    agent_pool_unchanged = _agent_signature(agents) == agent_sig
    conditions["input_verification"] = {
        "graphs_identical_per_repetition": graph_inputs_identical,
        "agent_pool_unchanged_after_runs": agent_pool_unchanged,
    }
    conditions["identical_inputs"] = (
        graph_inputs_identical and agent_pool_unchanged
    )

    return AblationStudyResult(
        study_id=study_id,
        conditions=conditions,
        configurations=results,
        ranking=ranking,
        principle_contributions=contributions,
        total_runs=total_runs,
        elapsed_s=elapsed_s,
        errors=study_errors,
    )


def _rounded(value: float | None) -> float | None:
    return None if value is None else round(value, 4)
