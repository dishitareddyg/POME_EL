"""
Tests for the POSDCORB ablation study module.

Coverage
--------
1.  Configuration ladder — nine cumulative rungs, baseline/full semantics
2.  Modular principle switches — gated events really disappear
3.  Deterministic fallback pool — capability coverage and reproducibility
4.  Runner — identical conditions, metric extraction, improvements
5.  Custom configurations — baseline is always present
6.  API — direct endpoint invocation without TestClient
"""
from __future__ import annotations

import pytest

from app.ablation.configs import (
    build_ablation_ladder,
    build_custom_configurations,
)
from app.ablation.defaults import default_agents
from app.ablation.runner import (
    METRIC_SPECS,
    _improvement,
    run_ablation_study,
)
from app.api.ablation import AblationStudyRequest, list_configurations, run_study
from app.managers.posdcorb import POSDCORBConfig, POSDCORBManager
from app.managers.principles import (
    ALL_PRINCIPLES,
    PRINCIPLE_ORDER,
    cumulative_principles,
    normalize_principles,
)
from app.models.workload import TASK_TYPE_CAPABILITY, WorkloadGeneratorConfig
from app.simulation.workload_generator import generate_workload

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SMALL_WORKLOAD = WorkloadGeneratorConfig(
    num_tasks=8,
    complexity="low",
    dependency_percentage=20.0,
)


def _events(trace, event_type: str) -> list:
    return [
        d for d in trace.decisions
        if d.manager_decision == f"posdcorb:{event_type}"
    ]


def _run_posdcorb(principles, adaptive: bool = False, n_tasks: int = 6):
    graph = generate_workload(
        WorkloadGeneratorConfig(
            num_tasks=n_tasks, complexity="low", dependency_percentage=10.0
        )
    )
    manager = POSDCORBManager(
        rng_seed=42,
        config=POSDCORBConfig(
            enabled_principles=frozenset(principles),
            adaptive_mechanisms=adaptive,
        ),
    )
    return manager.run(graph, default_agents())


# ---------------------------------------------------------------------------
# 1. Configuration ladder
# ---------------------------------------------------------------------------


class TestConfigurationLadder:

    def test_exactly_nine_configurations(self):
        ladder = build_ablation_ladder()
        assert len(ladder) == 9

    def test_baseline_first_and_full_last(self):
        ladder = build_ablation_ladder()
        assert ladder[0].is_baseline
        assert ladder[0].manager_type == "conventional"
        assert ladder[0].enabled_principles == frozenset()
        assert ladder[-1].is_full
        assert ladder[-1].manager_type == "posdcorb"
        assert ladder[-1].enabled_principles == ALL_PRINCIPLES
        assert ladder[-1].adaptive_mechanisms is True

    def test_rungs_are_cumulative(self):
        ladder = build_ablation_ladder()
        for index, config in enumerate(ladder[1:8], start=1):
            assert config.enabled_principles == cumulative_principles(index)

    def test_only_full_enables_adaptive_layer(self):
        ladder = build_ablation_ladder()
        assert all(not c.adaptive_mechanisms for c in ladder[:-1])
        assert ladder[-1].adaptive_mechanisms is True

    def test_expected_labels(self):
        labels = [c.label for c in build_ablation_ladder()]
        assert labels[0] == "Conventional Manager"
        assert labels[1] == "Planning only"
        assert labels[2] == "Planning + Organizing"
        assert labels[-1] == "Full POSDCORB"
        assert "Staffing" in labels[3]

    def test_principle_order_is_stable(self):
        assert PRINCIPLE_ORDER == (
            "planning",
            "organizing",
            "staffing",
            "directing",
            "coordinating",
            "reporting",
            "budgeting",
        )
        assert normalize_principles(None) == ALL_PRINCIPLES


# ---------------------------------------------------------------------------
# 2. Modular principle switches
# ---------------------------------------------------------------------------


class TestPrincipleSwitches:

    def test_planning_only_emits_plan_but_nothing_else(self):
        trace = _run_posdcorb({"planning"})
        assert len(_events(trace, "PLAN_CREATED")) == 1
        assert _events(trace, "TASK_GROUP_CREATED") == []
        assert _events(trace, "TASK_DIRECTED") == []
        assert _events(trace, "TASK_SCHEDULED") == []
        assert _events(trace, "STATUS_UPDATED") == []
        assert _events(trace, "AGENT_ACTIVATED") == []

    def test_no_principles_emits_no_posdcorb_events(self):
        trace = _run_posdcorb(set())
        assert [d for d in trace.decisions
                if d.manager_decision.startswith("posdcorb:")] == []

    def test_all_principles_without_adaptive_still_reports(self):
        trace = _run_posdcorb(ALL_PRINCIPLES, adaptive=False)
        assert _events(trace, "PLAN_CREATED")
        assert _events(trace, "TASK_GROUP_CREATED")
        assert _events(trace, "TASK_DIRECTED")
        assert _events(trace, "TASK_SCHEDULED")
        assert _events(trace, "STATUS_UPDATED")
        assert _events(trace, "RESOURCE_CHECK")

    def test_unknown_principle_is_rejected(self):
        with pytest.raises(ValueError):
            POSDCORBConfig(enabled_principles=frozenset({"teleportation"}))

    def test_disabling_staffing_prevents_activation(self):
        graph = generate_workload(
            WorkloadGeneratorConfig(
                num_tasks=20,
                complexity="medium",
                dependency_percentage=20.0,
            )
        )
        agents = default_agents()
        no_staff = POSDCORBManager(
            rng_seed=42,
            config=POSDCORBConfig(
                enabled_principles=frozenset(
                    {"planning", "organizing", "directing",
                     "coordinating", "reporting", "budgeting"}
                ),
                adaptive_mechanisms=False,
            ),
        )
        trace = no_staff.run(graph, agents)
        assert _events(trace, "AGENT_ACTIVATED") == []


# ---------------------------------------------------------------------------
# 3. Deterministic fallback pool
# ---------------------------------------------------------------------------


class TestDefaultAgents:

    def test_pool_is_four_initial_and_four_dynamic(self):
        agents = default_agents()
        assert len(agents) == 8
        assert sum(a.origin == "initial" for a in agents) == 4
        assert sum(a.origin == "dynamic" for a in agents) == 4

    def test_pool_covers_every_task_capability(self):
        covered = set()
        for agent in default_agents():
            covered.update(agent.capabilities)
        assert set(TASK_TYPE_CAPABILITY.values()) <= covered

    def test_pool_is_deterministic(self):
        first = [a.model_dump() for a in default_agents()]
        second = [a.model_dump() for a in default_agents()]
        assert first == second

    def test_pool_ids_are_unique(self):
        ids = [a.id for a in default_agents()]
        assert len(ids) == len(set(ids))


# ---------------------------------------------------------------------------
# 4. Runner
# ---------------------------------------------------------------------------


class TestRunner:

    @pytest.fixture(scope="class")
    def study(self):
        return run_ablation_study(
            agents=default_agents(),
            workload_config=SMALL_WORKLOAD,
            repetitions=2,
            base_seed=13,
            max_decisions=2_000,
            max_wall_ms=30_000.0,
        ).as_dict()

    def test_all_configurations_present(self, study):
        assert len(study["configurations"]) == 9
        assert study["total_runs"] == 18

    def test_every_required_metric_is_reported(self, study):
        expected = {
            "latency_ms",
            "throughput_tasks_per_sec",
            "success_rate",
            "cpu_utilization",
            "coordination_overhead",
            "agent_utilization",
            "activation_count",
        }
        for config in study["configurations"]:
            assert set(config["metrics"]) == expected
            for metric in config["metrics"].values():
                assert "value" in metric
                assert "std" in metric
                assert metric["n"] == 2

    def test_conditions_are_identical_for_every_configuration(self, study):
        conditions = study["conditions"]
        assert conditions["identical_inputs"] is True
        assert conditions["simulation"]["seeds"] == [13, 14]
        # Every config carries the same fingerprint and pool signature.
        assert conditions["fingerprint"]
        for config in study["configurations"]:
            assert config["raw_runs"][0]["seed"] == 13
            assert config["raw_runs"][1]["seed"] == 14

    def test_baseline_improvements_are_zero(self, study):
        baseline = study["configurations"][0]
        assert baseline["is_baseline"] is True
        for improvement in baseline["improvements_vs_baseline"].values():
            if improvement["higher_is_better"] is None:
                assert improvement["improvement_pct"] is None
            else:
                assert improvement["improvement_pct"] == 0.0
                assert improvement["direction"] == "equal"

    def test_activation_count_is_context_dependent(self, study):
        baseline = study["configurations"][0]
        entry = baseline["improvements_vs_baseline"]["activation_count"]
        assert entry["higher_is_better"] is None
        assert entry["improvement_pct"] is None

    def test_ranking_lists_every_config_once(self, study):
        ranked = [row["config_id"] for row in study["ranking"]]
        assert len(ranked) == 9
        assert set(ranked) == {c["id"] for c in study["configurations"]}

    def test_principle_contributions_cover_every_rung(self, study):
        principles = [c["principle"] for c in study["principle_contributions"]]
        assert set(principles) == set(PRINCIPLE_ORDER) | {"adaptive"}

    def test_raw_runs_are_traceable(self, study):
        for config in study["configurations"]:
            assert len(config["raw_runs"]) == 2
            for raw in config["raw_runs"]:
                assert raw["config_id"] == config["id"]
                assert raw["termination_reason"]
                assert raw["total_wall_ms"] >= 0.0


class TestImprovementDirection:

    def test_latency_lower_is_better(self):
        imp = _improvement("latency_ms", 100.0, 80.0)
        assert imp.improvement_pct == pytest.approx(20.0)
        assert imp.direction == "better"

    def test_throughput_higher_is_better(self):
        imp = _improvement("throughput_tasks_per_sec", 10.0, 12.0)
        assert imp.improvement_pct == pytest.approx(20.0)
        assert imp.direction == "better"

    def test_overhead_higher_is_worse(self):
        imp = _improvement("coordination_overhead", 10.0, 12.0)
        assert imp.improvement_pct == pytest.approx(-20.0)
        assert imp.direction == "worse"

    def test_zero_baseline_is_handled(self):
        imp = _improvement("success_rate", 0.0, 0.0)
        assert imp.improvement_pct == 0.0
        assert imp.direction == "equal"

    def test_metric_specs_are_complete(self):
        keys = {spec.key for spec in METRIC_SPECS}
        assert keys == {
            "latency_ms",
            "throughput_tasks_per_sec",
            "success_rate",
            "cpu_utilization",
            "coordination_overhead",
            "agent_utilization",
            "activation_count",
        }


# ---------------------------------------------------------------------------
# 5. Custom configurations
# ---------------------------------------------------------------------------


class TestCustomConfigurations:

    def test_baseline_is_prepended_when_absent(self):
        configs = build_custom_configurations([
            {
                "id": "planning-staffing",
                "label": "P + S",
                "enabled_principles": ["planning", "staffing"],
            }
        ])
        assert configs[0].is_baseline
        assert configs[1].id == "planning-staffing"

    def test_conventional_spec_is_recognised_as_baseline(self):
        configs = build_custom_configurations([
            {"id": "conv", "enabled_principles": []},
            {"id": "p", "enabled_principles": ["planning"]},
        ])
        assert len(configs) == 2
        assert configs[0].is_baseline

    def test_unknown_added_principle_is_rejected(self):
        with pytest.raises(ValueError):
            build_custom_configurations([
                {"id": "x", "enabled_principles": ["planning"],
                 "added_principle": "nonsense"}
            ])


# ---------------------------------------------------------------------------
# 6. API (direct invocation; TestClient is unavailable in this environment)
# ---------------------------------------------------------------------------


class TestAblationApi:

    def test_list_configurations(self):
        payload = list_configurations()
        assert len(payload["configurations"]) == 9
        assert payload["baseline_config_id"] == "conventional"
        assert len(payload["metric_specs"]) == 7

    def test_study_endpoint_returns_full_report(self):
        response = run_study(
            AblationStudyRequest(
                workload=WorkloadGeneratorConfig(
                    num_tasks=8,
                    complexity="low",
                    dependency_percentage=15.0,
                ),
                repetitions=1,
                base_seed=5,
                max_decisions=2_000,
                max_wall_ms=30_000.0,
            )
        )
        assert response["total_runs"] == 9
        assert len(response["configurations"]) == 9
        assert response["baseline_config_id"] == "conventional"
        assert response["agent_source"] == "default"

    def test_custom_configurations_are_honoured(self):
        response = run_study(
            AblationStudyRequest(
                workload=WorkloadGeneratorConfig(
                    num_tasks=6,
                    complexity="low",
                    dependency_percentage=0.0,
                ),
                repetitions=1,
                max_decisions=2_000,
                max_wall_ms=30_000.0,
                configurations=[
                    {"id": "conv-custom", "enabled_principles": []},
                    {"id": "p-only", "enabled_principles": ["planning"]},
                ],
            )
        )
        ids = [c["id"] for c in response["configurations"]]
        assert ids == ["conv-custom", "p-only"]
