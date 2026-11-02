"""
Ablation configuration ladder.

The study compares nine manager configurations.  Eight of them are cumulative:
each rung enables exactly one additional POSDCORB principle, so the delta
between consecutive rungs isolates that principle's contribution.

    1. Conventional Manager          (no POSDCORB mechanisms)
    2. Planning only                 (P)
    3. Planning + Organizing         (P + O)
    4. … + Staffing                  (P + O + S)
    5. … + Directing                 (P + O + S + D)
    6. … + Coordinating              (P + O + S + D + CO)
    7. … + Reporting                  (P + O + S + D + CO + R)
    8. … + Budgeting                  (all seven principles)
    9. Full POSDCORB                  (all seven + adaptive mechanisms)

Why rung 9 exists
-----------------
The seven principles are exhausted at rung 8.  "Full POSDCORB" additionally
enables the cross-cutting adaptive mechanisms that are not single principles:
adaptive replanning on task/agent failure, slow-task replanning, failure
recovery, and deadlock detection.  All earlier rungs keep these disabled so
each cumulative delta measures one principle rather than the adaptive layer.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from app.managers.principles import (
    ALL_PRINCIPLES,
    PRINCIPLE_CODES,
    PRINCIPLE_DESCRIPTIONS,
    PRINCIPLE_LABELS,
    PRINCIPLE_ORDER,
    normalize_principles,
)

# Short codes used by charts (kept compact so 9 bars fit on an axis).
SHORT_LABELS: dict[str, str] = {
    "conventional": "Conv",
    "planning": "P",
    "planning-organizing": "P+O",
    "planning-organizing-staffing": "P+O+S",
    "planning-organizing-staffing-directing": "+D",
    "planning-organizing-staffing-directing-coordinating": "+CO",
    "planning-organizing-staffing-directing-coordinating-reporting": "+R",
    "planning-organizing-staffing-directing-coordinating-reporting-budgeting": "+B",
    "posdcorb-full": "Full",
}

FULL_PRINCIPLES_LABEL = "All seven principles"


@dataclass(frozen=True)
class AblationConfiguration:
    """One row of the ablation study."""

    id: str
    label: str
    short_label: str
    description: str
    manager_type: str                       # "conventional" | "posdcorb"
    enabled_principles: frozenset[str]
    adaptive_mechanisms: bool
    added_principle: str | None             # principle introduced at this rung
    added_label: str                        # human-readable delta label
    is_baseline: bool = False
    is_full: bool = False

    @property
    def principle_codes(self) -> str:
        if not self.enabled_principles:
            return "none"
        return " + ".join(
            PRINCIPLE_CODES[p]
            for p in PRINCIPLE_ORDER
            if p in self.enabled_principles
        )

    @property
    def principle_labels(self) -> list[str]:
        return [
            PRINCIPLE_LABELS[p]
            for p in PRINCIPLE_ORDER
            if p in self.enabled_principles
        ]

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "label": self.label,
            "short_label": self.short_label,
            "description": self.description,
            "manager_type": self.manager_type,
            "enabled_principles": [
                p for p in PRINCIPLE_ORDER if p in self.enabled_principles
            ],
            "principle_labels": self.principle_labels,
            "principle_codes": self.principle_codes,
            "adaptive_mechanisms": self.adaptive_mechanisms,
            "added_principle": self.added_principle,
            "added_label": self.added_label,
            "is_baseline": self.is_baseline,
            "is_full": self.is_full,
        }


def _cumulative_description(principles: frozenset[str]) -> str:
    if not principles:
        return (
            "Centralized fixed-pool supervisor. No POSDCORB mechanism is "
            "enabled; this is the baseline for every improvement calculation."
        )
    included = "; ".join(
        f"{PRINCIPLE_LABELS[p]}: {PRINCIPLE_DESCRIPTIONS[p]}"
        for p in PRINCIPLE_ORDER
        if p in principles
    )
    return included


def build_ablation_ladder() -> list[AblationConfiguration]:
    """Return the canonical nine-configuration ladder."""
    configs: list[AblationConfiguration] = [
        AblationConfiguration(
            id="conventional",
            label="Conventional Manager",
            short_label=SHORT_LABELS["conventional"],
            description=_cumulative_description(frozenset()),
            manager_type="conventional",
            enabled_principles=frozenset(),
            adaptive_mechanisms=False,
            added_principle=None,
            added_label="Baseline",
            is_baseline=True,
        )
    ]

    for index, principle in enumerate(PRINCIPLE_ORDER, start=1):
        principles = frozenset(PRINCIPLE_ORDER[:index])
        is_all_principles = principles == ALL_PRINCIPLES

        if index == 1:
            label = "Planning only"
        elif index == 2:
            label = "Planning + Organizing"
        elif is_all_principles:
            label = "Full POSDCORB (all principles)"
        else:
            label = f"+ {PRINCIPLE_LABELS[principle]}"

        config_id = "-".join(PRINCIPLE_ORDER[:index])
        configs.append(
            AblationConfiguration(
                id=config_id,
                label=label,
                short_label=SHORT_LABELS.get(
                    config_id, PRINCIPLE_CODES[principle]
                ),
                description=_cumulative_description(principles),
                manager_type="posdcorb",
                enabled_principles=principles,
                adaptive_mechanisms=False,
                added_principle=principle,
                added_label=PRINCIPLE_LABELS[principle],
                is_full=False,
            )
        )

    # Rung 9 — full manager, including the cross-cutting adaptive layer.
    configs.append(
        AblationConfiguration(
            id="posdcorb-full",
            label="Full POSDCORB",
            short_label=SHORT_LABELS["posdcorb-full"],
            description=(
                "All seven principles plus the adaptive layer: replanning on "
                "task/agent failure, slow-task detection, failure recovery and "
                "deadlock detection. This is the default POSDCORB manager."
            ),
            manager_type="posdcorb",
            enabled_principles=ALL_PRINCIPLES,
            adaptive_mechanisms=True,
            added_principle="adaptive",
            added_label="Adaptive replanning & recovery",
            is_full=True,
        )
    )

    return configs


def build_custom_configurations(
    specs: Iterable[dict],
) -> list[AblationConfiguration]:
    """
    Build configurations from user-provided specs.

    Each spec supports::

        {
          "id": "my-posdcorb",
          "label": "Planning + Staffing",
          "enabled_principles": ["planning", "staffing"],
          "adaptive_mechanisms": false
        }

    A conventional baseline is prepended automatically when the specs do not
    include one, because every improvement figure needs a reference.
    """
    built: list[AblationConfiguration] = []
    for raw in specs:
        principles = normalize_principles(raw.get("enabled_principles"))
        config_id = str(raw.get("id") or "-".join(
            p for p in PRINCIPLE_ORDER if p in principles
        ) or "custom")
        manager_type = (
            "conventional"
            if not principles and not raw.get("adaptive_mechanisms")
            else "posdcorb"
        )
        added = raw.get("added_principle")
        if added is not None:
            if added not in PRINCIPLE_ORDER and added != "adaptive":
                raise ValueError(
                    f"Unknown added_principle '{added}'. "
                    f"Expected one of {list(PRINCIPLE_ORDER)} or 'adaptive'."
                )
        labels = [
            PRINCIPLE_LABELS[p] for p in PRINCIPLE_ORDER if p in principles
        ]
        built.append(
            AblationConfiguration(
                id=config_id,
                label=str(raw.get("label") or ", ".join(labels) or "Custom"),
                short_label=str(
                    raw.get("short_label")
                    or ("Conv" if manager_type == "conventional" else "Custom")
                ),
                description=str(
                    raw.get("description") or _cumulative_description(principles)
                ),
                manager_type=manager_type,
                enabled_principles=principles,
                adaptive_mechanisms=bool(raw.get("adaptive_mechanisms", False)),
                added_principle=added,
                added_label=(
                    PRINCIPLE_LABELS.get(added, "Adaptive") if added else "Custom"
                ),
                is_baseline=manager_type == "conventional",
                is_full=principles == ALL_PRINCIPLES,
            )
        )

    if not any(c.is_baseline for c in built):
        built.insert(0, build_ablation_ladder()[0])
    return built
