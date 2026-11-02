"""
POSDCORB principle registry.

The seven POSDCORB mechanisms are addressable by stable string keys so that
any subset can be enabled or disabled through configuration.  The POSDCORB
manager reads these keys from :class:`app.managers.posdcorb.POSDCORBConfig`
and the ablation study module builds cumulative configurations from
:data:`PRINCIPLE_ORDER`.

Design rule
-----------
Nothing in the manager is hard-coded to "all principles on".  Every mechanism
is guarded by a membership check against ``enabled_principles``.  This keeps
the ablation study honest: the only difference between two configurations is
the set of enabled mechanisms.
"""
from __future__ import annotations

from typing import Iterable

# ---------------------------------------------------------------------------
# Canonical identifiers (order defines the cumulative ablation ladder)
# ---------------------------------------------------------------------------

PRINCIPLE_ORDER: tuple[str, ...] = (
    "planning",
    "organizing",
    "staffing",
    "directing",
    "coordinating",
    "reporting",
    "budgeting",
)

ALL_PRINCIPLES: frozenset[str] = frozenset(PRINCIPLE_ORDER)

PRINCIPLE_CODES: dict[str, str] = {
    "planning": "P",
    "organizing": "O",
    "staffing": "S",
    "directing": "D",
    "coordinating": "CO",
    "reporting": "R",
    "budgeting": "B",
}

PRINCIPLE_LABELS: dict[str, str] = {
    "planning": "Planning",
    "organizing": "Organizing",
    "staffing": "Staffing",
    "directing": "Directing",
    "coordinating": "Coordinating",
    "reporting": "Reporting",
    "budgeting": "Budgeting",
}

PRINCIPLE_DESCRIPTIONS: dict[str, str] = {
    "planning": (
        "Pre-execution ExecutionPlan (topological levels, priority order, "
        "bottlenecks) plus adaptive replanning."
    ),
    "organizing": (
        "Groups tasks into capability-aligned TaskGroups; emits "
        "TASK_GROUP_CREATED / TASK_REORGANIZED."
    ),
    "staffing": (
        "Activates reserve agents when pending parallel work justifies the "
        "activation cost; respects the active-agent cap."
    ),
    "directing": (
        "Emits a structured TaskDirective for every assignment "
        "(priority, capability, retry limit, dependency status)."
    ),
    "coordinating": (
        "Dependency-aware scheduling, explicit DEPENDENCY_WAIT / "
        "TASK_SCHEDULED events, reassignment and deadlock detection."
    ),
    "reporting": (
        "Continuously updates the ManagementReport and emits STATUS_UPDATED "
        "snapshots."
    ),
    "budgeting": (
        "Tracks and enforces wall-clock and manager-decision budgets; emits "
        "RESOURCE_CHECK / BUDGET_WARNING and gates activations."
    ),
}


# ---------------------------------------------------------------------------
# Validation / normalisation
# ---------------------------------------------------------------------------


def normalize_principles(names: Iterable[str] | None) -> frozenset[str]:
    """
    Return a validated frozenset of principle keys.

    ``None`` means "all principles" — the default POSDCORB behaviour.
    Unknown names raise ``ValueError`` so typos cannot silently change an
    experiment configuration.
    """
    if names is None:
        return ALL_PRINCIPLES
    requested = frozenset(str(n).strip().lower() for n in names if str(n).strip())
    unknown = sorted(requested - ALL_PRINCIPLES)
    if unknown:
        raise ValueError(
            f"Unknown POSDCORB principle(s): {unknown}. "
            f"Valid keys: {list(PRINCIPLE_ORDER)}"
        )
    return requested


def cumulative_principles(upto: int) -> frozenset[str]:
    """Return the first ``upto`` principles in canonical order."""
    bounded = max(0, min(int(upto), len(PRINCIPLE_ORDER)))
    return frozenset(PRINCIPLE_ORDER[:bounded])


def describe_principles(enabled: Iterable[str]) -> str:
    """Human-readable summary, e.g. ``"P + O + S"``."""
    enabled_set = set(enabled)
    return " + ".join(
        PRINCIPLE_CODES[p] for p in PRINCIPLE_ORDER if p in enabled_set
    ) or "none"
