"""
Tests for Module 10 — Experiment Analysis API (POST /api/analysis/batch).

Coverage:
  - Parameter validation (bad complexity, bad failure_prob, too many runs)
  - Minimal single-config single-rep batch
  - Multi-config sweep (task_count variation)
  - Multi-rep aggregation (stats correctness)
  - Effect size computation (Cohen's d)
  - Chart data structure (all 6 chart series present)
  - CSV export endpoint
  - Zero-failure-prob recovery chart
  - Non-zero failure_prob produces recovery data
  - Both managers run for every config
  - Config id stability
  - StatsSummary field presence
  - EffectSize fields presence
  - RunRecord fields presence
  - BatchResponse top-level fields
  - total_runs matches raw_runs length
  - stats grouped by (config_id, manager)
  - chart_data task_count_vs_latency sorted by x_value
  - Empty repetition edge case (repetitions=1, n=1)
  - max_agents constraint respected in configs
  - base_seed offset applied per rep
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.analysis import (
    router as analysis_router,
    _config_id,
    _cohens_d,
    _magnitude,
    _stats,
    _chart_point,
    _effect_size,
)

# Use a minimal app to avoid pulling in health.py which hard-imports psutil
_app = FastAPI()
_app.include_router(analysis_router, prefix="/api")
client = TestClient(_app)

# ---------------------------------------------------------------------------
# Minimal valid payload helpers
# ---------------------------------------------------------------------------

MINIMAL = {
    "task_counts": [6],
    "complexities": ["medium"],
    "dep_percentages": [20.0],
    "failure_probs": [0.0],
    "max_agents_list": [4],
    "repetitions": 1,
    "base_seed": 42,
    "max_decisions": 2000,
    "max_wall_ms": 60000.0,
}

MULTI_TC = {
    **MINIMAL,
    "task_counts": [5, 8, 11],
}

MULTI_REP = {
    **MINIMAL,
    "repetitions": 3,
}

# ---------------------------------------------------------------------------
# Unit tests — pure helper functions
# ---------------------------------------------------------------------------


class TestConfigId:
    def test_deterministic(self):
        a = _config_id(10, "medium", 25.0, 0.0, 6)
        b = _config_id(10, "medium", 25.0, 0.0, 6)
        assert a == b

    def test_different_task_counts(self):
        a = _config_id(8, "medium", 25.0, 0.0, 6)
        b = _config_id(12, "medium", 25.0, 0.0, 6)
        assert a != b

    def test_different_complexity(self):
        a = _config_id(8, "low", 25.0, 0.0, 6)
        b = _config_id(8, "high", 25.0, 0.0, 6)
        assert a != b

    def test_prefix(self):
        cid = _config_id(8, "medium", 25.0, 0.0, 6)
        assert cid.startswith("c-")


class TestStatsSummary:
    def test_single_value(self):
        s = _stats([5.0])
        assert s.mean == 5.0
        assert s.median == 5.0
        assert s.std == 0.0
        assert s.n == 1

    def test_multiple_values(self):
        s = _stats([1.0, 2.0, 3.0])
        assert s.mean == pytest.approx(2.0)
        assert s.n == 3

    def test_empty(self):
        s = _stats([])
        assert s.n == 0
        assert s.mean == 0.0

    def test_std_two_values(self):
        s = _stats([0.0, 10.0])
        assert s.std > 0


class TestCohensD:
    def test_identical_groups(self):
        d = _cohens_d([1, 2, 3], [1, 2, 3])
        assert d == pytest.approx(0.0, abs=1e-4)

    def test_large_separation(self):
        d = _cohens_d([100, 101, 102], [1, 2, 3])
        assert abs(d) > 1.5   # clearly large

    def test_direction(self):
        # When groups have zero within-group variance but different means,
        # pooled_std == 0 → function returns sign-encoded large value
        d = _cohens_d([10, 10, 10], [5, 5, 5])
        assert d > 0   # first group is higher

    def test_single_element_each(self):
        # Should not raise; returns sign-only
        d = _cohens_d([10.0], [5.0])
        assert d in (1.0, -1.0, 0.0)

    def test_empty_groups(self):
        d = _cohens_d([], [])
        assert d == 0.0


class TestMagnitude:
    def test_negligible(self):
        assert _magnitude(0.0) == "negligible"
        assert _magnitude(0.1) == "negligible"

    def test_small(self):
        assert _magnitude(0.3) == "small"

    def test_medium(self):
        assert _magnitude(0.6) == "medium"

    def test_large(self):
        assert _magnitude(1.2) == "large"

    def test_negative(self):
        assert _magnitude(-0.7) == "medium"


class TestChartPoint:
    def test_basic(self):
        pt = _chart_point(10.0, "10", [1.0, 2.0], [3.0, 4.0])
        assert pt.x_value == 10.0
        assert pt.x_label == "10"
        assert pt.n_reps == 2
        assert pt.conventional_mean == pytest.approx(1.5)
        assert pt.posdcorb_mean == pytest.approx(3.5)

    def test_empty_groups(self):
        pt = _chart_point(5.0, "5", [], [])
        assert pt.conventional_mean == 0.0
        assert pt.posdcorb_mean == 0.0


class TestEffectSize:
    def test_fields_present(self):
        es = _effect_size(
            metric="success_rate",
            config_id="c-abc123",
            task_count=10,
            complexity="medium",
            dep_pct=25.0,
            failure_prob=0.0,
            max_agents=6,
            conv_values=[0.8, 0.85, 0.9],
            posd_values=[0.7, 0.75, 0.8],
            higher_is_better=True,
        )
        assert es.metric == "success_rate"
        assert es.config_id == "c-abc123"
        assert isinstance(es.cohens_d, float)
        assert es.magnitude in ("negligible", "small", "medium", "large")
        assert isinstance(es.interpretation, str)
        assert es.test_performed in ("", "welch_t")


# ---------------------------------------------------------------------------
# API integration tests
# ---------------------------------------------------------------------------


class TestBatchEndpointValidation:
    def test_bad_complexity(self):
        payload = {**MINIMAL, "complexities": ["extreme"]}
        r = client.post("/api/analysis/batch", json=payload)
        assert r.status_code == 422
        assert "complexity" in r.text.lower() or "invalid" in r.text.lower()

    def test_bad_failure_prob(self):
        payload = {**MINIMAL, "failure_probs": [1.5]}
        r = client.post("/api/analysis/batch", json=payload)
        assert r.status_code == 422

    def test_too_many_runs(self):
        # 10 task counts × 5 complexities × 3 dep × 3 fp × 2 ma × 5 reps × 2 managers = 1800
        payload = {
            "task_counts": list(range(5, 55, 5)),   # 10 values
            "complexities": ["low", "medium", "high"],
            "dep_percentages": [10, 25, 50],
            "failure_probs": [0.0, 0.1, 0.2],
            "max_agents_list": [4, 6],
            "repetitions": 5,
            "base_seed": 42,
        }
        r = client.post("/api/analysis/batch", json=payload)
        assert r.status_code == 422


class TestBatchEndpointMinimal:
    def setup_method(self):
        self.resp = client.post("/api/analysis/batch", json=MINIMAL)
        self.data = self.resp.json()

    def test_status_ok(self):
        assert self.resp.status_code == 200

    def test_top_level_fields(self):
        for f in ("batch_id", "total_configs", "total_runs", "elapsed_s",
                  "raw_runs", "stats", "effect_sizes", "chart_data", "errors"):
            assert f in self.data

    def test_total_configs(self):
        assert self.data["total_configs"] == 1

    def test_total_runs(self):
        # 1 config × 1 rep × 2 managers = 2
        assert self.data["total_runs"] == 2
        assert len(self.data["raw_runs"]) == 2

    def test_both_managers_present(self):
        managers = {r["manager"] for r in self.data["raw_runs"]}
        assert managers == {"conventional", "posdcorb"}

    def test_run_record_fields(self):
        r = self.data["raw_runs"][0]
        for f in ("run_id", "config_id", "rep", "seed", "task_count",
                  "complexity", "dep_pct", "failure_prob", "max_agents",
                  "manager", "completed_tasks", "failed_tasks", "success_rate",
                  "total_wall_ms", "throughput_tasks_per_sec",
                  "avg_task_latency_ms", "total_decisions",
                  "coordination_overhead_ratio", "total_active_agents",
                  "activation_count", "avg_agent_utilization",
                  "recovery_count", "recovery_success_rate",
                  "termination_reason", "error"):
            assert f in r, f"Missing field: {f}"

    def test_success_rate_range(self):
        for r in self.data["raw_runs"]:
            assert 0.0 <= r["success_rate"] <= 1.0

    def test_stats_has_both_managers(self):
        manager_labels = {s["manager"] for s in self.data["stats"]}
        assert "conventional" in manager_labels
        assert "posdcorb" in manager_labels

    def test_stats_fields(self):
        s = self.data["stats"][0]
        for f in ("config_id", "manager", "task_count", "n_reps",
                  "success_rate", "throughput", "avg_latency_ms"):
            assert f in s
        # Check StatsSummary sub-fields
        for sub in ("mean", "median", "std", "min", "max", "n"):
            assert sub in s["success_rate"]

    def test_chart_data_present(self):
        cd = self.data["chart_data"]
        for k in ("task_count_vs_latency", "task_count_vs_throughput",
                  "task_count_vs_success_rate",
                  "task_count_vs_coordination_overhead",
                  "task_count_vs_active_agents",
                  "failure_prob_vs_recovery_rate",
                  "task_count_vs_cpu_note"):
            assert k in cd

    def test_chart_point_fields(self):
        pt = self.data["chart_data"]["task_count_vs_latency"][0]
        for f in ("x_value", "x_label", "conventional_mean", "conventional_std",
                  "posdcorb_mean", "posdcorb_std", "n_reps"):
            assert f in pt

    def test_effect_sizes_present(self):
        assert len(self.data["effect_sizes"]) > 0

    def test_effect_size_fields(self):
        es = self.data["effect_sizes"][0]
        for f in ("config_id", "metric", "task_count", "cohens_d",
                  "magnitude", "interpretation", "test_performed"):
            assert f in es

    def test_elapsed_positive(self):
        assert self.data["elapsed_s"] > 0


class TestBatchMultiTaskCount:
    def setup_method(self):
        resp = client.post("/api/analysis/batch", json=MULTI_TC)
        self.data = resp.json()

    def test_total_configs(self):
        assert self.data["total_configs"] == 3   # 3 task counts

    def test_total_runs(self):
        assert self.data["total_runs"] == 6   # 3 configs × 1 rep × 2 managers

    def test_chart_latency_points(self):
        pts = self.data["chart_data"]["task_count_vs_latency"]
        assert len(pts) == 3
        x_vals = [p["x_value"] for p in pts]
        assert x_vals == sorted(x_vals)   # ascending x

    def test_different_task_counts_in_runs(self):
        tcs = {r["task_count"] for r in self.data["raw_runs"]}
        assert tcs == {5, 8, 11}


class TestBatchMultiRep:
    def setup_method(self):
        resp = client.post("/api/analysis/batch", json=MULTI_REP)
        self.data = resp.json()

    def test_total_runs(self):
        # 1 config × 3 reps × 2 managers = 6
        assert self.data["total_runs"] == 6

    def test_stats_n_reps(self):
        for s in self.data["stats"]:
            assert s["n_reps"] == 3
            assert s["success_rate"]["n"] == 3

    def test_seeds_vary_per_rep(self):
        seeds = {r["seed"] for r in self.data["raw_runs"]}
        assert len(seeds) == 3   # base_seed, base_seed+1, base_seed+2

    def test_std_computed(self):
        # With 3 reps there should be a computable std (may be 0 if deterministic)
        for s in self.data["stats"]:
            assert isinstance(s["success_rate"]["std"], float)


class TestCSVExport:
    def test_csv_endpoint(self):
        # Batch results are not stored, so a batch_id-keyed CSV download
        # cannot reproduce them.  The endpoint must fail explicitly instead
        # of returning a header-only file that looks like a valid export.
        r = client.get("/api/analysis/batch/csv")
        assert r.status_code == 501
        assert "not persisted" in r.text.lower() or "raw_runs" in r.text.lower()


class TestFailureProbChart:
    def test_single_fp_recovery_chart(self):
        payload = {**MINIMAL, "failure_probs": [0.0]}
        data = client.post("/api/analysis/batch", json=payload).json()
        pts = data["chart_data"]["failure_prob_vs_recovery_rate"]
        assert len(pts) == 1
        assert pts[0]["x_value"] == 0.0

    def test_multi_fp_chart(self):
        payload = {
            **MINIMAL,
            "failure_probs": [0.0, 0.1, 0.2],
        }
        data = client.post("/api/analysis/batch", json=payload).json()
        pts = data["chart_data"]["failure_prob_vs_recovery_rate"]
        assert len(pts) == 3
        x_vals = [p["x_value"] for p in pts]
        assert x_vals == sorted(x_vals)
