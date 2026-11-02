"""POSDCORB ablation study package.

The package exposes:

* :mod:`app.ablation.configs`   — cumulative configuration ladder (P → P+O → …)
* :mod:`app.ablation.defaults`  — deterministic fallback agent pool
* :mod:`app.ablation.runner`    — executes every configuration under identical
  experimental conditions and computes the comparison metrics
"""
from app.ablation.configs import (
    AblationConfiguration,
    build_ablation_ladder,
    build_custom_configurations,
)
from app.ablation.defaults import default_agents
from app.ablation.runner import run_ablation_study

__all__ = [
    "AblationConfiguration",
    "build_ablation_ladder",
    "build_custom_configurations",
    "default_agents",
    "run_ablation_study",
]
