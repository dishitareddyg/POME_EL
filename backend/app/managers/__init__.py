"""
Manager implementations.

Public surface:
  BaseManager     — abstract base class
  ManagerProtocol — runtime-checkable Protocol (structural typing)
  DecisionRecord  — one manager decision cycle
  ExecutionTrace  — full run audit trail
  ConventionalManager — baseline centralized manager (fixed pool)
  get_manager     — factory function
  registered_types — list available manager type strings
"""
from app.managers.base import (
    BaseManager,
    DecisionRecord,
    ExecutionTrace,
    ManagerProtocol,
)
from app.managers.conventional import ConventionalManager
from app.managers.registry import get_manager, registered_types

__all__ = [
    "BaseManager",
    "ManagerProtocol",
    "DecisionRecord",
    "ExecutionTrace",
    "ConventionalManager",
    "get_manager",
    "registered_types",
]
