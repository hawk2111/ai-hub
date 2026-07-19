"""Configuration: TOML on disk, `RUNBREAKER_*` on top.

Env wins over both files so an unattended launcher can tighten a per-run ceiling
without editing anything. Files are read with `tomllib` and never written back.

Nothing here shells out. Resolving the project directory with `git rev-parse`
would cost a subprocess on every single tool call.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CONFIG_NAME = "runbreaker.toml"
DEFAULT_GC_DAYS = 7
DEFAULT_MAX_SESSIONS = 50

DEFAULT_CONDITIONS: tuple[dict[str, Any], ...] = (
    {"id": "step_budget", "max_steps": 250},
    {"id": "gate_failures", "threshold": 3},
)


def project_dir() -> Path:
    for var in ("RUNBREAKER_PROJECT_DIR", "CLAUDE_PROJECT_DIR"):
        value = os.environ.get(var)
        if value:
            return Path(value)
    return Path.cwd()


def home_dir(project: Path | None = None) -> Path:
    value = os.environ.get("RUNBREAKER_HOME")
    if value:
        return Path(value)
    return (project or project_dir()) / ".runbreaker"


@dataclass(frozen=True)
class GateCheck:
    name: str
    command: tuple[str, ...]
    fail_records_breaker: bool = True
    timeout: int | None = None


@dataclass(frozen=True)
class GateConfig:
    watch: tuple[str, ...] = ()
    tail_lines: int = 40
    checks: tuple[GateCheck, ...] = ()
    #: Loop guard for providers without Claude's `stop_hook_active` flag.
    max_stop_blocks: int = 3

    @property
    def enabled(self) -> bool:
        return bool(self.checks)


@dataclass(frozen=True)
class BudgetConfig:
    token_source: str = "auto"
    recompute_every: int = 5


@dataclass(frozen=True)
class Config:
    home: Path
    project: Path
    budget: BudgetConfig = field(default_factory=BudgetConfig)
    gate: GateConfig = field(default_factory=GateConfig)
    conditions: tuple[dict[str, Any], ...] = DEFAULT_CONDITIONS
    skip_budget: bool = False
    skip_gate: bool = False
    #: "warn" mode: conditions and the gate still evaluate and audit what they
    #: *would* have done, but nothing is denied or blocked. An adoption on-ramp
    #: for tuning thresholds against real runs before turning enforcement on.
    warn_only: bool = False
    gc_days: int = DEFAULT_GC_DAYS
    max_sessions: int = DEFAULT_MAX_SESSIONS

    @property
    def state_dir(self) -> Path:
        return self.home / "state"

    @property
    def conditions_dir(self) -> Path:
        return self.home / "conditions"

    @property
    def audit_path(self) -> Path:
        return self.state_dir / "audit.jsonl"


# -- loading ----------------------------------------------------------------


def _read_toml(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as fh:
            loaded = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    return loaded


def _merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _env_int(name: str) -> int | None:
    raw = os.environ.get(name)
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _pick_int(env_name: str, raw: dict[str, Any], key: str, default: int) -> int:
    """Env beats file beats default — and `0` is a value, not an absence.

    `_env_int(...) or file_value` would quietly ignore `RUNBREAKER_GC_DAYS=0`, which
    is precisely how a user disables garbage collection.
    """
    from_env = _env_int(env_name)
    if from_env is not None:
        return from_env
    value = raw.get(key)
    return int(value) if isinstance(value, int) and not isinstance(value, bool) else default


def _env_float(name: str) -> float | None:
    raw = os.environ.get(name)
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _upsert(conditions: list[dict[str, Any]], cond_id: str, key: str, value: Any) -> None:
    for cond in conditions:
        if cond.get("id") == cond_id:
            cond[key] = value
            return
    conditions.append({"id": cond_id, key: value})


def _apply_env(conditions: list[dict[str, Any]]) -> None:
    overrides = (
        ("RUNBREAKER_MAX_STEPS", "step_budget", "max_steps", _env_int),
        ("RUNBREAKER_MAX_TOKENS", "token_budget", "max_tokens", _env_int),
        ("RUNBREAKER_MAX_MINUTES", "time_budget", "max_minutes", _env_float),
        ("RUNBREAKER_MAX_USD", "cost_budget", "max_usd", _env_float),
        ("RUNBREAKER_MAX_REPEATS", "repeat_loop", "threshold", _env_int),
        ("RUNBREAKER_BREAKER_THRESHOLD", "gate_failures", "threshold", _env_int),
        ("RUNBREAKER_MAX_RATE_PERCENT", "rate_limit_pressure", "max_percent", _env_float),
    )
    for var, cond_id, key, parse in overrides:
        value = parse(var)
        if value is not None:
            _upsert(conditions, cond_id, key, value)


def _parse_gate(raw: dict[str, Any]) -> GateConfig:
    checks: list[GateCheck] = []
    for entry in raw.get("checks", []) or []:
        if not isinstance(entry, dict):
            continue
        command = entry.get("command")
        if not isinstance(command, list) or not command:
            continue
        checks.append(
            GateCheck(
                name=str(entry.get("name") or command[0]),
                command=tuple(str(part) for part in command),
                fail_records_breaker=bool(entry.get("fail_records_breaker", True)),
                timeout=entry.get("timeout") if isinstance(entry.get("timeout"), int) else None,
            )
        )
    watch = raw.get("watch", []) or []
    return GateConfig(
        watch=tuple(str(w) for w in watch if isinstance(w, str)),
        tail_lines=int(raw.get("tail_lines", 40) or 40),
        checks=tuple(checks),
        max_stop_blocks=int(raw.get("max_stop_blocks", 3) or 3),
    )


def load() -> Config:
    project = project_dir()
    home = home_dir(project)

    data: dict[str, Any] = {}
    data = _merge(data, _read_toml(Path.home() / ".runbreaker" / CONFIG_NAME))
    data = _merge(data, _read_toml(home / CONFIG_NAME))

    raw_conditions = data.get("conditions")
    conditions: list[dict[str, Any]] = (
        [dict(c) for c in raw_conditions if isinstance(c, dict)]
        if isinstance(raw_conditions, list)
        else [dict(c) for c in DEFAULT_CONDITIONS]
    )
    _apply_env(conditions)

    budget_raw = data.get("budget", {}) if isinstance(data.get("budget"), dict) else {}
    budget = BudgetConfig(
        token_source=os.environ.get("RUNBREAKER_TOKEN_SOURCE")
        or str(budget_raw.get("token_source", "auto")),
        recompute_every=_pick_int("RUNBREAKER_RECOMPUTE_EVERY", budget_raw, "recompute_every", 5),
    )

    gate_raw = data.get("gate", {}) if isinstance(data.get("gate"), dict) else {}

    breaker_raw = data.get("breaker", {}) if isinstance(data.get("breaker"), dict) else {}
    mode = os.environ.get("RUNBREAKER_ENFORCE") or str(breaker_raw.get("mode", "block"))

    return Config(
        home=home,
        project=project,
        budget=budget,
        gate=_parse_gate(gate_raw),
        conditions=tuple(conditions),
        skip_budget=os.environ.get("RUNBREAKER_SKIP_BUDGET") == "1",
        skip_gate=os.environ.get("RUNBREAKER_SKIP_GATE") == "1",
        warn_only=mode.strip().lower() == "warn",
        gc_days=_pick_int("RUNBREAKER_GC_DAYS", data, "gc_days", DEFAULT_GC_DAYS),
        max_sessions=_pick_int(
            "RUNBREAKER_MAX_SESSIONS", data, "max_sessions", DEFAULT_MAX_SESSIONS
        ),
    )
