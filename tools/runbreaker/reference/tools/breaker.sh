#!/usr/bin/env bash
# Circuit-breaker control for unattended agent runs (Phase 5).
#
#   tools/agent/breaker.sh status        # show breaker state
#   tools/agent/breaker.sh trip [reason] # force read-only (block file mutations)
#   tools/agent/breaker.sh reset         # close the breaker, resume writes
#
# When OPEN, the pre-breaker-guard hook blocks Write/Edit/MultiEdit so the agent
# cannot keep changing code; reads, tests, and this script stay available.
set -euo pipefail
cd "$(dirname "$0")/../.."
H=".claude/hooks"

case "${1:-status}" in
  status)
    python3 -c "import sys,json;sys.path.insert(0,'$H');import breaker,_budget;print(json.dumps({'breaker':breaker.status(),'budget':_budget._load()},indent=2))"
    ;;
  trip)
    python3 -c "import sys;sys.path.insert(0,'$H');import breaker;breaker.trip('''${2:-manual trip}''')"
    echo "circuit breaker: OPEN (read-only)"
    ;;
  reset)
    python3 -c "import sys;sys.path.insert(0,'$H');import breaker,_budget;breaker.reset();_budget.reset()"
    echo "circuit breaker: CLOSED (writes enabled); run budget cleared"
    ;;
  *)
    echo "usage: $0 {status|trip [reason]|reset}" >&2
    exit 1
    ;;
esac
