"""Condition registry.

Custom conditions are plain `.py` files under `.agentguard/conditions/`, loaded by
path. Entry points would need setuptools at runtime, which hook processes do not
have.

    from agentguard.conditions import Condition, register
    from agentguard.events import EventType

    @register
    class WallClock(Condition):
        id = "wall_clock"
        phases = frozenset({EventType.PRE_TOOL_USE})
        ...
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from agentguard.conditions.base import (
    REGISTRY,
    Condition,
    EvalContext,
    Trip,
    build,
    build_all,
    evaluate,
    register,
)

# Importing these populates REGISTRY via @register.
from agentguard.conditions.gate_failures import GateFailures
from agentguard.conditions.rate_limit_pressure import RateLimitPressure
from agentguard.conditions.step_budget import StepBudget
from agentguard.conditions.token_budget import TokenBudget

__all__ = [
    "REGISTRY",
    "Condition",
    "EvalContext",
    "GateFailures",
    "RateLimitPressure",
    "StepBudget",
    "TokenBudget",
    "Trip",
    "build",
    "build_all",
    "evaluate",
    "load_custom",
    "register",
]


def load_custom(directory: Path) -> list[str]:
    """Import every `.py` in `directory`, returning the ids they registered."""
    if not directory.is_dir():
        return []
    before = set(REGISTRY)
    for path in sorted(directory.glob("*.py")):
        if path.name.startswith("_"):
            continue
        module_name = f"agentguard._custom_conditions.{path.stem}"
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            continue
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        try:
            spec.loader.exec_module(module)
        except Exception:
            # A broken custom condition must not take the hook down with it.
            sys.modules.pop(module_name, None)
            continue
    return sorted(set(REGISTRY) - before)
