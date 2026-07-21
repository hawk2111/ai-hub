"""`runbreaker` — the human side of the breaker.

    runbreaker status          # breaker + budget (human-readable; --json for machine)
    runbreaker trip [reason]   # force read-only
    runbreaker reset           # close the breaker, clear the run budget
    runbreaker gc              # drop stale session ledgers
    runbreaker install         # wire hooks into Claude / Codex / Copilot
    runbreaker doctor          # verify the hooks are actually wired up
    runbreaker report          # summarize the audit trail

Closing an open breaker is deliberately a manual act. A green gate does not do it.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from typing import Any

from runbreaker import audit as audit_module
from runbreaker import config as config_module
from runbreaker import install as install_module
from runbreaker.breaker import Breaker, BreakerStatus
from runbreaker.budget import Budget
from runbreaker.state import Store

PROVIDERS = ("claude", "codex", "copilot")


def _parts(cfg: config_module.Config) -> tuple[Breaker, Budget]:
    store = Store(cfg.state_dir)
    return Breaker(store), Budget(store)


def _cmd_status(cfg: config_module.Config, args: argparse.Namespace) -> int:
    breaker, budget = _parts(cfg)
    status = breaker.status()
    sessions = budget.all_sessions()
    if args.json:
        payload = {
            "breaker": asdict(status),
            "budget": sessions,
            "home": str(cfg.home),
            "conditions": [dict(c) for c in cfg.conditions],
            "gate": {"enabled": cfg.gate.enabled, "checks": [c.name for c in cfg.gate.checks]},
        }
        print(json.dumps(payload, indent=2, default=str))
        return 0

    for line in _status_lines(cfg, status, sessions):
        print(line)
    return 0


def _status_lines(
    cfg: config_module.Config, status: BreakerStatus, sessions: dict[str, Any]
) -> list[str]:
    """A human-readable status: breaker state first, then limits and per-run budget."""
    lines: list[str] = []
    if status.corrupt:
        lines.append("breaker: OPEN — state file unreadable (treated as open)")
        lines.append("  reset with: runbreaker reset")
    elif status.is_open:
        lines.append(f"breaker: OPEN (read-only) — {status.reason}")
        if status.tripped_at:
            lines.append(f"  tripped at {status.tripped_at}")
        lines.append("  reset with: runbreaker reset")
    else:
        line = "breaker: CLOSED (writes enabled)"
        if status.fails:
            line += f" — {status.fails} consecutive gate failure(s)"
        lines.append(line)

    conditions = ", ".join(_fmt_condition(c) for c in cfg.conditions)
    lines.append(f"conditions: {conditions or 'none'}")

    if cfg.gate.enabled and cfg.gate.checks:
        lines.append(f"gate: {', '.join(c.name for c in cfg.gate.checks)}")
    else:
        lines.append("gate: disabled")

    if not sessions:
        lines.append("sessions: none tracked yet")
    else:
        lines.append(f"sessions ({len(sessions)}):")
        for sid, data in sessions.items():
            lines.append(f"  {_short_sid(sid)}: {_fmt_session(data)}")
    return lines


def _fmt_condition(cond: dict[str, Any]) -> str:
    settings = ", ".join(f"{k}={v}" for k, v in cond.items() if k != "id")
    return f"{cond.get('id', '?')}({settings})" if settings else str(cond.get("id", "?"))


def _short_sid(sid: str) -> str:
    return sid if len(sid) <= 12 else f"{sid[:8]}…{sid[-3:]}"


def _fmt_session(data: dict[str, Any]) -> str:
    parts = [f"steps={data.get('steps', 0)}"]
    tokens = data.get("tokens")
    if tokens is not None:
        parts.append(f"tokens={tokens}")
    return ", ".join(parts)


def _cmd_trip(cfg: config_module.Config, args: argparse.Namespace) -> int:
    breaker, _budget = _parts(cfg)
    status = breaker.trip(args.reason or "manual trip")
    # A manual trip is a real breaker event; record it so `report` accounts for it
    # the same way it does an automatic one (event="trip", decision="open"), marked
    # as human-initiated via provider="cli" and condition="manual".
    audit_module.record(
        cfg.audit_path,
        event="trip",
        decision="open",
        provider="cli",
        reason=status.reason,
        detail={"condition": "manual"},
    )
    print(f"circuit breaker: OPEN (read-only) — {status.reason}")
    return 0


def _cmd_reset(cfg: config_module.Config, _args: argparse.Namespace) -> int:
    breaker, budget = _parts(cfg)
    breaker.reset()
    budget.reset()
    # Record the close so the audit trail shows who reopened the door, and when.
    audit_module.record(
        cfg.audit_path,
        event="reset",
        decision="closed",
        provider="cli",
        reason="manual reset",
    )
    print("circuit breaker: CLOSED (writes enabled); run budget reset")
    return 0


def _cmd_gc(cfg: config_module.Config, _args: argparse.Namespace) -> int:
    _breaker, budget = _parts(cfg)
    removed = budget.collect(gc_days=cfg.gc_days, max_sessions=cfg.max_sessions)
    print(f"removed {removed} stale session(s)")
    return 0


def _cmd_install(cfg: config_module.Config, args: argparse.Namespace) -> int:
    providers = tuple(args.provider) if args.provider else PROVIDERS
    result = install_module.install(cfg, providers)
    for path in result.written:
        print(f"wrote {path}")
    if result.notes:
        print()
        for note in result.notes:
            print(f"note: {note}")
    return 0


def _cmd_doctor(cfg: config_module.Config, args: argparse.Namespace) -> int:
    providers = tuple(args.provider) if args.provider else PROVIDERS
    checks = install_module.diagnose(cfg, providers)
    if args.json:
        print(json.dumps([asdict(c) for c in checks], indent=2))
    else:
        for check in checks:
            mark = "ok  " if check.ok else "FAIL"
            line = f"[{mark}] {check.label}"
            print(f"{line}: {check.detail}" if check.detail else line)
    return 0 if all(c.ok for c in checks) else 1


def _cmd_report(cfg: config_module.Config, args: argparse.Namespace) -> int:
    entries = audit_module.read(cfg.audit_path)
    summary = audit_module.summarize(entries)
    if args.json:
        print(json.dumps(summary, indent=2))
        return 0

    print(f"runbreaker report — {cfg.audit_path}")
    print(f"{summary['total']} audit event(s)")
    if summary["by_outcome"]:
        print("\nby outcome:")
        for outcome, count in summary["by_outcome"].items():
            print(f"  {outcome}: {count}")
    if summary["by_provider"]:
        print("\nby provider:")
        for provider, count in summary["by_provider"].items():
            print(f"  {provider}: {count}")

    trips = [e for e in entries if e.get("event") == "trip"][-args.limit :]
    if trips:
        print(f"\nrecent trips (last {len(trips)}):")
        for entry in trips:
            ts = entry.get("ts", "")
            decision = entry.get("decision", "")
            print(f"  {ts} [{decision}] {entry.get('reason', '')}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="runbreaker", description=__doc__)
    sub = parser.add_subparsers(dest="command")

    status = sub.add_parser("status", help="show breaker and budget state")
    status.add_argument("--json", action="store_true", help="machine-readable output")
    status.set_defaults(fn=_cmd_status)

    trip = sub.add_parser("trip", help="force the run read-only")
    trip.add_argument("reason", nargs="?", default="")
    trip.set_defaults(fn=_cmd_trip)

    sub.add_parser("reset", help="close the breaker and clear the budget").set_defaults(
        fn=_cmd_reset
    )
    sub.add_parser("gc", help="drop stale session ledgers").set_defaults(fn=_cmd_gc)

    inst = sub.add_parser("install", help="wire hooks into the host CLIs")
    inst.add_argument("--provider", action="append", choices=PROVIDERS)
    inst.set_defaults(fn=_cmd_install)

    doctor = sub.add_parser("doctor", help="verify the hooks are wired up")
    doctor.add_argument("--provider", action="append", choices=PROVIDERS)
    doctor.add_argument("--json", action="store_true", help="machine-readable output")
    doctor.set_defaults(fn=_cmd_doctor)

    report = sub.add_parser("report", help="summarize the audit trail")
    report.add_argument("--json", action="store_true", help="machine-readable output")
    report.add_argument("--limit", type=int, default=10, help="how many recent trips to show")
    report.set_defaults(fn=_cmd_report)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "fn"):
        parser.print_help()
        return 1
    cfg = config_module.load()
    return int(args.fn(cfg, args))


if __name__ == "__main__":
    sys.exit(main())
