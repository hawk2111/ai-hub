"""Condition registry.

Custom conditions are plain `.py` files under `.runbreaker/conditions/`, loaded by
path. Entry points would need setuptools at runtime, which hook processes do not
have.

    from runbreaker.conditions import Condition, register
    from runbreaker.events import EventType

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

from runbreaker.conditions.base import (
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
from runbreaker.conditions.cost_budget import CostBudget
from runbreaker.conditions.gate_failures import GateFailures
from runbreaker.conditions.loop import LoopGuard
from runbreaker.conditions.rate_limit_pressure import RateLimitPressure
from runbreaker.conditions.step_budget import StepBudget
from runbreaker.conditions.time_budget import TimeBudget
from runbreaker.conditions.token_budget import TokenBudget

__all__ = [
    "REGISTRY",
    "Condition",
    "CostBudget",
    "EvalContext",
    "GateFailures",
    "LoopGuard",
    "RateLimitPressure",
    "StepBudget",
    "TimeBudget",
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
        module_name = f"runbreaker._custom_conditions.{path.stem}"
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
