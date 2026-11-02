"""
Module 8 — Metrics Engine
=========================

Public API for the metrics package.

Import surface
--------------
from app.metrics import MetricsEngine, ComparisonEngine
from app.metrics.models import (
    PerformanceMetrics, TaskMetrics, AgentMetrics,
    ManagementMetrics, ReliabilityMetrics, CostMetrics,
    ResourceMonitorMetrics, DerivedMetrics, AggregatedMetrics,
    ComparisonResult, ComparisonRow,
)
from app.metrics.aggregator import Aggregator
from app.metrics.exporter import MetricsExporter
"""

from app.metrics.engine import ComparisonEngine, MetricsEngine

__all__ = [
    "MetricsEngine",
    "ComparisonEngine",
]
