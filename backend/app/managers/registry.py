"""
Manager registry — factory for all manager implementations.

The SimulationEngine uses this to instantiate the right manager by name,
so no calling code ever imports ConventionalManager or POSDCORBManager
directly.  Adding a new manager requires only:
  1. Implement the class (satisfying ManagerProtocol).
  2. Add one line to _REGISTRY below.

Generic resource limits
-----------------------
The engine never knows manager-specific configuration types.  It forwards
generic limits (``max_wall_ms``, ``max_agent_count``) and this factory passes
them to whichever managers declare support for them.  Managers that do not
accept a limit simply ignore it — preserving backward compatibility with
existing constructors and call sites.

Usage
-----
    from app.managers.registry import get_manager

    mgr = get_manager("conventional", rng_seed=42)
    trace = mgr.run(graph, agents)
"""
from __future__ import annotations

import inspect

from app.managers.base import BaseManager, ManagerProtocol
from app.managers.conventional import ConventionalManager
from app.managers.posdcorb import POSDCORBManager

# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_REGISTRY: dict[str, type[BaseManager]] = {
    "conventional": ConventionalManager,
    "posdcorb": POSDCORBManager,
}


def get_manager(
    manager_type: str,
    rng_seed: int = 42,
    max_wall_ms: float | None = None,
    max_agent_count: int | None = None,
    max_decisions: int | None = None,
    manager_options: dict[str, object] | None = None,
) -> BaseManager:
    """
    Return an instantiated manager for the given type string.

    Parameters
    ----------
    manager_type:
        One of the registered manager identifiers (e.g. 'conventional').
    rng_seed:
        Seed forwarded to the manager's internal RNG for reproducibility.
    max_wall_ms:
        Generic simulated wall-clock cap.  Forwarded only to managers whose
        constructor accepts it; ignored otherwise.
    max_agent_count:
        Generic active-agent cap.  Forwarded only to managers that support
        dynamic staffing; ignored by fixed-pool managers.
    max_decisions:
        Generic manager-decision budget.  Forwarded only to managers whose
        constructor declares ``max_manager_decisions``.
    manager_options:
        Optional mapping of constructor parameters forwarded verbatim to a
        manager that declares them.  This keeps the factory manager-neutral
        while allowing callers (e.g. the ablation study) to inject a
        pre-built manager configuration.

    Raises
    ------
    ValueError
        If ``manager_type`` is not registered.
    """
    cls = _REGISTRY.get(manager_type)
    if cls is None:
        available = ", ".join(sorted(_REGISTRY))
        raise ValueError(
            f"Unknown manager type '{manager_type}'. Available: {available}"
        )

    # Pass generic limits only when the manager declares the corresponding
    # constructor parameter — keeps the factory manager-neutral and backward
    # compatible.
    accepted = set(inspect.signature(cls.__init__).parameters)
    kwargs: dict[str, object] = {"rng_seed": rng_seed}
    if max_wall_ms is not None and "max_wall_ms" in accepted:
        kwargs["max_wall_ms"] = max_wall_ms
    if max_agent_count is not None and "max_agent_count" in accepted:
        kwargs["max_agent_count"] = max_agent_count
    if max_decisions is not None and "max_manager_decisions" in accepted:
        kwargs["max_manager_decisions"] = max_decisions
    for key, value in (manager_options or {}).items():
        if key in accepted and value is not None:
            kwargs[key] = value

    return cls(**kwargs)  # type: ignore[arg-type]


def registered_types() -> list[str]:
    """Return the list of currently registered manager type strings."""
    return sorted(_REGISTRY.keys())
