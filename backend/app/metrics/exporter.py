"""
Module 8 — Metrics Exporter
============================

Exports DerivedMetrics, AggregatedMetrics, ComparisonResult to JSON and CSV.

No external dependencies beyond the standard library.
"""
from __future__ import annotations

import csv
import io
import json
from typing import Any

from app.metrics.models import (
    AggregatedMetrics,
    ComparisonResult,
    DerivedMetrics,
)


class MetricsExporter:
    """
    Export metric objects to JSON strings or CSV strings.

    All methods return strings (not file handles) so callers can choose
    whether to write to disk, return in an HTTP response, or process further.
    """

    # ------------------------------------------------------------------
    # JSON
    # ------------------------------------------------------------------

    @staticmethod
    def to_json(obj: Any, indent: int = 2) -> str:
        """
        Serialise any metric object that has an ``as_dict()`` method to JSON.

        Handles DerivedMetrics, AggregatedMetrics, ComparisonResult, or a
        plain dict / list.
        """
        if hasattr(obj, "as_dict"):
            data = obj.as_dict()
        elif isinstance(obj, (dict, list)):
            data = obj
        else:
            raise TypeError(f"Cannot serialise {type(obj).__name__} to JSON.")
        return json.dumps(data, indent=indent, default=str)

    @staticmethod
    def derived_to_json(metrics: DerivedMetrics, indent: int = 2) -> str:
        return json.dumps(metrics.as_dict(), indent=indent, default=str)

    @staticmethod
    def aggregated_to_json(agg: AggregatedMetrics, indent: int = 2) -> str:
        return json.dumps(agg.as_dict(), indent=indent, default=str)

    @staticmethod
    def comparison_to_json(result: ComparisonResult, indent: int = 2) -> str:
        return json.dumps(result.as_dict(), indent=indent, default=str)

    # ------------------------------------------------------------------
    # CSV — comparison table
    # ------------------------------------------------------------------

    @staticmethod
    def comparison_to_csv(result: ComparisonResult) -> str:
        """
        Export comparison rows to CSV.

        Columns: metric, conventional_value, posdcorb_value,
                 absolute_difference, pct_difference,
                 higher_is_better, better_manager, interpretation
        """
        out = io.StringIO()
        fieldnames = [
            "metric",
            "conventional_value",
            "posdcorb_value",
            "absolute_difference",
            "pct_difference",
            "higher_is_better",
            "better_manager",
            "interpretation",
        ]
        writer = csv.DictWriter(out, fieldnames=fieldnames)
        writer.writeheader()
        for row in result.rows:
            writer.writerow(row.as_dict())
        return out.getvalue()

    # ------------------------------------------------------------------
    # CSV — per-task metrics
    # ------------------------------------------------------------------

    @staticmethod
    def task_metrics_to_csv(metrics: DerivedMetrics) -> str:
        """Export per-task metric rows to CSV."""
        rows = metrics.tasks.as_rows()
        if not rows:
            return ""
        out = io.StringIO()
        fieldnames = list(rows[0].keys())
        writer = csv.DictWriter(out, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
        return out.getvalue()

    # ------------------------------------------------------------------
    # CSV — per-agent metrics
    # ------------------------------------------------------------------

    @staticmethod
    def agent_metrics_to_csv(metrics: DerivedMetrics) -> str:
        """Export per-agent metric rows to CSV."""
        rows = [a.as_dict() for a in metrics.agents.per_agent.values()]
        if not rows:
            return ""
        out = io.StringIO()
        fieldnames = list(rows[0].keys())
        writer = csv.DictWriter(out, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
        return out.getvalue()

    # ------------------------------------------------------------------
    # CSV — aggregated statistics
    # ------------------------------------------------------------------

    @staticmethod
    def aggregated_to_csv(agg: AggregatedMetrics) -> str:
        """
        Export aggregated statistics to CSV.

        One row per metric; columns: metric, mean, median, std, min, max, n.
        """
        d = agg.as_dict()
        stat_fields = ["metric", "mean", "median", "std", "min", "max", "n"]
        out = io.StringIO()
        writer = csv.DictWriter(out, fieldnames=stat_fields)
        writer.writeheader()
        skip = {"manager_type", "run_count"}
        for key, val in d.items():
            if key in skip:
                continue
            if isinstance(val, dict) and "mean" in val:
                writer.writerow({"metric": key, **val})
        return out.getvalue()

    # ------------------------------------------------------------------
    # CSV — summary (all managers in one table)
    # ------------------------------------------------------------------

    @staticmethod
    def summary_to_csv(metrics_list: list[DerivedMetrics]) -> str:
        """
        Export a flat summary CSV with one row per DerivedMetrics object.

        Useful for comparing multiple managers or multiple runs at a glance.
        """
        if not metrics_list:
            return ""

        rows: list[dict[str, Any]] = []
        for m in metrics_list:
            p = m.performance
            mg = m.management
            ag = m.agents
            re = m.reliability
            co = m.cost
            rows.append({
                "manager_type": m.manager_type,
                "experiment_id": m.experiment_id,
                "seed": m.seed,
                "workload_id": m.workload_id,
                "total_tasks": p.total_tasks,
                "completed_tasks": p.completed_tasks,
                "failed_tasks": p.failed_tasks,
                "pending_tasks": p.pending_tasks,
                "total_execution_latency_ms": p.total_execution_latency_ms,
                "avg_task_latency_ms": p.avg_task_latency_ms,
                "throughput_tasks_per_sec": p.throughput_tasks_per_sec,
                "task_success_rate": p.task_success_rate,
                "total_decisions": mg.total_decisions,
                "coordination_overhead_ratio": mg.coordination_overhead_ratio,
                "avg_agent_utilization": ag.avg_agent_utilization,
                "activation_count": ag.activation_count,
                "task_failure_count": re.task_failure_count,
                "recovery_count": re.recovery_count,
                "budget_decisions_utilization": co.budget_decisions_utilization,
            })

        out = io.StringIO()
        fieldnames = list(rows[0].keys())
        writer = csv.DictWriter(out, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
        return out.getvalue()
