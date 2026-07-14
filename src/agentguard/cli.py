"""`agentguard` — the human side of the breaker.

    agentguard status          # breaker + budget, as JSON
    agentguard trip [reason]   # force read-only
    agentguard reset           # close the breaker, clear the run budget
    agentguard gc              # drop stale session ledgers
    agentguard install         # wire hooks into Claude / Codex / Copilot

Closing an open breaker is deliberately a manual act. A green gate does not do it.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict

from agentguard import config as config_module
from agentguard import install as install_module
from agentguard.breaker import Breaker
from agentguard.budget import Budget
from agentguard.state import Store

PROVIDERS = ("claude", "codex", "copilot")


def _parts(cfg: config_module.Config) -> tuple[Breaker, Budget]:
    store = Store(cfg.state_dir)
    return Breaker(store), Budget(store)


def _cmd_status(cfg: config_module.Config, _args: argparse.Namespace) -> int:
    breaker, budget = _parts(cfg)
    payload = {
        "breaker": asdict(breaker.status()),
        "budget": budget.all_sessions(),
        "home": str(cfg.home),
        "conditions": [dict(c) for c in cfg.conditions],
        "gate": {"enabled": cfg.gate.enabled, "checks": [c.name for c in cfg.gate.checks]},
    }
    print(json.dumps(payload, indent=2, default=str))
    return 0


def _cmd_trip(cfg: config_module.Config, args: argparse.Namespace) -> int:
    breaker, _budget = _parts(cfg)
    status = breaker.trip(args.reason or "manual trip")
    print(f"circuit breaker: OPEN (read-only) — {status.reason}")
    return 0


def _cmd_reset(cfg: config_module.Config, _args: argparse.Namespace) -> int:
    breaker, budget = _parts(cfg)
    breaker.reset()
    budget.reset()
    print("circuit breaker: CLOSED (writes enabled); run budget cleared")
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agentguard", description=__doc__)
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("status", help="show breaker and budget state").set_defaults(fn=_cmd_status)

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
