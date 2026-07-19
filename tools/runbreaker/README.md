# runbreaker

A circuit breaker and budget guard for unattended AI coding agents, working across
**Claude Code**, **OpenAI Codex CLI**, **GitHub Copilot CLI**, and **Copilot in VS Code**.

Leave an agent running on its own and it can loop on a broken task or quietly burn
hours and money. runbreaker watches the run and, when it crosses a limit you set —
too many steps, too much wall-clock time, too many tokens or dollars, the same call
repeated over and over, or a test suite that stays red — it **opens the breaker**:
file edits are denied, the agent drops to read-only, and the run surfaces to a human
instead of grinding on. Reads, tests and `runbreaker reset` stay available, so
nothing deadlocks.

## Quickstart

1. **Install the package** (once per machine):

   ```bash
   cd tools/runbreaker
   pip install -e .
   ```

2. **Wire it into a project** (once per repository, run from the repo root):

   ```bash
   cd /path/to/your/project
   runbreaker install
   ```

   This creates a `.runbreaker/` folder holding `runbreaker.toml` (your config) and a
   `.py` shim per host, and registers hooks in `.claude/settings.json`,
   `.codex/hooks.json` and `.github/hooks/runbreaker.json`. Existing configs are
   merged, never overwritten, so it is safe to re-run.

3. **Check it took:**

   ```bash
   runbreaker doctor
   ```

   Every line should read `[ok]`. A `[FAIL]` says exactly what to fix.

4. **Run your agent as usual.** With the defaults, runbreaker only steps in when a run
   makes more than 250 tool calls or the quality gate stays red — otherwise you will
   not notice it. Adjust the limits in `.runbreaker/runbreaker.toml` (see
   [Conditions](#conditions)).

5. **When the breaker opens,** the agent drops to read-only and every write is refused
   with a message pointing here. See what happened, then reopen the door yourself — a
   passing test never does it for you:

   ```bash
   runbreaker status     # what tripped, as JSON
   runbreaker report     # a summary of recent trips and denials
   runbreaker reset      # close the breaker and clear the run budget
   ```

**Not sure your limits are right?** Start in **warn mode**: runbreaker records what it
*would* have tripped on, but blocks nothing. Tune, then switch to enforcing.

```bash
RUNBREAKER_ENFORCE=warn   # or set [breaker] mode = "warn" in the config
```

## How it works

Each host CLI spawns one hook process per tool call. A thin **provider adapter**
translates that CLI's payload into a normalized event; a **condition registry**
decides whether to open the breaker; the open breaker is the single point that
denies anything.

```
host CLI ──▶ shim ──▶ hook.main ──▶ adapter.parse ──▶ handler ──▶ conditions
                                          ▲                          │
                                          └──── adapter.emit ◀── breaker
```

Adding a fourth CLI is one adapter module. Adding a trip condition is one class —
including your own, dropped into `.runbreaker/conditions/`.

## Concepts

New to the tool? These five words carry the whole model:

- **Run** — one agent session working on a task, identified by a session id. Budgets
  are counted per run, so each fresh session starts from zero.
- **Hook** — a small process each host CLI runs *before every tool call* and *when the
  agent tries to finish*. That is runbreaker's only window to see — and veto — what the
  agent does.
- **Condition** — a rule that watches one signal (a budget, a repeated call, a failing
  test) and decides whether to trip. You choose which conditions run in the config.
- **Trip / open the breaker** — when a condition fires, the breaker *opens*: file edits
  are denied and the agent goes read-only until a human runs `runbreaker reset`.
  Closing it is always a human decision — a passing test never reopens the door.
- **Quality gate** — commands (usually your tests) that runbreaker runs when the agent
  tries to finish. A red gate is handed back so the agent fixes its own mess; stay red
  too often and the breaker opens.

## Conditions

A condition trips the breaker; the open breaker is what actually denies anything. You
enable conditions by listing them in `runbreaker.toml`. **Out of the box only
`step_budget` and `gate_failures` are on** — the rest are opt-in, so add the ones you
want. Set any threshold to `0` to disable that condition; the first condition to trip
wins.

| id | Trips when… | Checked | Works on | Key settings (default) |
|---|---|---|---|---|
| `step_budget` | the run makes more than `max_steps` tool calls | each call | all | `max_steps` (250) |
| `time_budget` | the run has run longer than `max_minutes` of wall-clock time | each call + finish | all | `max_minutes` (off) |
| `token_budget` | token use passes `trip_at_fraction × max_tokens` | each call + finish | Claude, Codex ¹ | `max_tokens` (off), `trip_at_fraction` (0.9) |
| `cost_budget` | estimated spend (tokens × price) passes `max_usd` | each call + finish | Claude, Codex ¹ | `max_usd` (off), `price_per_mtok` |
| `rate_limit_pressure` | the provider reports more than `max_percent` of its rate limit used | each call + finish | Codex only ¹ | `max_percent` (80) |
| `repeat_loop` | the same call — or an A-B-A-B cycle — repeats `threshold` times | each call | all | `threshold` (5) |
| `gate_failures` | the quality gate comes back red `threshold` times in a row | finish | all | `threshold` (3) |

¹ **Abstains** (never trips) when it cannot read the number — an unknown token count is
never treated as zero. Copilot does not expose tokens, so on Copilot only the
provider-agnostic conditions apply: `step_budget`, `time_budget`, `repeat_loop`,
`gate_failures`.

`repeat_loop` matches calls exactly (tool name + a hash of the input), so an ordinary
edit → test → edit cycle that changes the file each time is *not* a loop and never
trips — only genuinely identical, stuck repetition does.

## Configuration

`.runbreaker/runbreaker.toml`, overridden by `RUNBREAKER_*` environment variables so
a launcher can tighten a per-run ceiling without editing files. The block below lists
**every** condition for reference; the file `runbreaker install` scaffolds turns on
`step_budget` and `gate_failures` and leaves the rest commented out — uncomment what
you need.

```toml
[budget]
token_source = "auto"       # follows the provider; or "claude" / "codex" / "null"
recompute_every = 5         # re-read the token file every Nth step

[[conditions]]
id = "step_budget"
max_steps = 250             # 0 disables

[[conditions]]
id = "time_budget"          # wall-clock; provider-agnostic like step_budget
max_minutes = 45            # 0 disables

[[conditions]]
id = "token_budget"
max_tokens = 2_000_000
trip_at_fraction = 0.9      # trip with margin — token ledgers are approximate

[[conditions]]
id = "cost_budget"          # a blended-price layer over token_budget; abstains when tokens unknown
max_usd = 10
price_per_mtok = 15         # blended $/1M tokens; coarse by design

[[conditions]]
id = "rate_limit_pressure"  # Codex only; abstains elsewhere
max_percent = 80

[[conditions]]
id = "repeat_loop"          # stuck agent: same call (or A-B-A-B) repeated
threshold = 5               # 0 disables

[[conditions]]
id = "gate_failures"
threshold = 3

# Observe without enforcing: conditions and the gate still evaluate and audit what
# they *would* have done, but nothing is denied or blocked. Tune thresholds first,
# then turn enforcement on. Overridden by RUNBREAKER_ENFORCE.
[breaker]
mode = "block"              # or "warn"

# The quality gate is entirely config-driven. No checks => the Stop hook is a no-op.
[gate]
watch = ["src/**/*.py"]     # empty => run on every stop
tail_lines = 40

[[gate.checks]]
name = "pytest"
command = ["python3", "-m", "pytest", "-q"]
fail_records_breaker = true
```

Env: `RUNBREAKER_MAX_STEPS`, `RUNBREAKER_MAX_TOKENS`, `RUNBREAKER_MAX_MINUTES`,
`RUNBREAKER_MAX_USD`, `RUNBREAKER_MAX_REPEATS`, `RUNBREAKER_BREAKER_THRESHOLD`,
`RUNBREAKER_MAX_RATE_PERCENT`, `RUNBREAKER_ENFORCE` (`block` / `warn`),
`RUNBREAKER_SKIP_BUDGET`, `RUNBREAKER_SKIP_GATE`, `RUNBREAKER_HOME`,
`RUNBREAKER_PROJECT_DIR`, `RUNBREAKER_GC_DAYS`.

## Design decisions worth knowing

**An internal exception exits 0.** Copilot treats any non-zero exit other than 2 as a
*deny*, while Claude treats it as a harmless error. A crash in runbreaker would not
merely fail to guard a Copilot run — it would lock the user out of every tool. Exit 2
is reserved for a decision we deliberately made.

**Unknown token usage abstains; it never reads as zero.** Providers move their token
fields around. A parse failure returns `None`, and the `token_budget` condition sits
it out rather than concluding that a runaway run consumed nothing.

**A corrupt breaker file reads as OPEN.** A truncated safety file most likely came
from a crash during exactly the kind of chaotic run the breaker exists to stop.
Reading it as "closed" would be a fail-open hole in a safety device. Recover with
`runbreaker reset`. The budget ledger, which only ever *informs* the breaker, starts
fresh instead.

**A green gate never closes an open breaker.** One passing check is not evidence that
whatever tripped it is fixed.

**Warn mode never opens the breaker.** In `mode = "warn"` a condition that trips is
audited (`decision: "warn"`) but the breaker stays closed, so the gate keeps running
and observation continues turn after turn. Opening it and merely declining to deny
would silently stop the gate on the next Stop — the opposite of what an
observe-only run wants. A *manual* `runbreaker trip` still denies, even in warn mode:
that is a human decision, not an automatic guard.

**A shim whose package vanished exits 0.** The import sits inside a guard, because a
deleted venv or a moved checkout would otherwise exit 1 — and on Copilot CLI that is a
deny. An unguarded run is the lesser failure.

**A check that cannot be launched is a config error, not a red gate.** It is audited
and surfaced, but it neither counts toward `gate_failures` nor blocks the agent from
finishing. Otherwise a typo in `runbreaker.toml` would open the breaker after three
turns.

**Token files are read incrementally.** Session transcripts are append-only, so the
ledger caches a byte offset and a running total. Re-reading a 3.7 MB transcript costs
0.1 ms instead of 33 ms, and that read happens while the state lock is held.

## Provider notes

| | Claude Code | Codex CLI | Copilot CLI | Copilot in VS Code |
|---|---|---|---|---|
| config | `.claude/settings.json` | `.codex/hooks.json` | `.github/hooks/runbreaker.json` | reuses the other two |
| write tools gated | `Write` `Edit` `MultiEdit` `NotebookEdit` | `apply_patch` | `create` `edit` | `create_file`, `apply_patch`, `replace_string_in_file`, … (and legacy `copilot_*`) |
| step budget | yes | yes | yes | yes |
| token budget | yes | yes | **no** | no |
| rate-limit pressure | no | yes | no | no |
| non-zero exit ≠ 2 | fails open | error | **fails closed** | fails open |

- **Claude** transcripts can be missing the final `message_stop`
  ([#27361](https://github.com/anthropics/claude-code/issues/27361)), so output tokens
  are a lower bound. Trip with margin.
- **Codex** only fires `PreToolUse` for `apply_patch` since
  [#18391](https://github.com/openai/codex/pull/18391) (April 2026, after v0.118). On
  older builds only `Bash` is gated and file edits slip past. `runbreaker install`
  reports your version. Some builds also need `[features] hooks = true` in
  `~/.codex/config.toml`.
- **Copilot CLI** tracks tokens (`/usage` prints per-model totals) but exposes them to
  neither the hook payload nor any documented file
  ([#2947](https://github.com/github/copilot-cli/issues/2947) is open). Hooks are
  registered under PascalCase event names so Copilot emits VS Code-compatible
  snake_case payloads. Only `step_budget` applies.
- **Copilot in VS Code** is a different product with a different tool vocabulary, and
  its `chat.hookFilesLocations` reads `.claude/settings.json` and `.github/hooks/*.json`
  by default — so a shim installed for another host ends up handling VS Code tool calls.
  runbreaker routes those by tool name, overriding the baked `--provider` flag; without
  that it would meter a VS Code session and then deny nothing. VS Code is renaming its
  tools from the legacy `copilot_*` prefix to prefix-less snake_case (`create_file`,
  `apply_patch`, `replace_string_in_file`, …); runbreaker gates **both** schemes so a
  rename cannot silently let a write slip past. Agent hooks there are still Preview.

## No shell scripts

Everything runbreaker runs is Python. It ships no `.sh`, no `.ps1`, and no shell
logic — the generated artifacts are `.py` shims and JSON config, nothing else.

How far that goes depends on what each host's hook schema allows:

| host | how runbreaker is invoked | shell involved |
|---|---|---|
| Claude Code | exec form: `command` + `args` argv | **none** |
| Codex CLI | `command` string (no exec form exists) | the host's |
| Copilot CLI | `command` string — the shell-neutral field, never `bash` | the host's |
| VS Code | `command` string | the host's |

Only Claude offers an exec form, so only there can the shell be eliminated outright.
Elsewhere the host insists on a command string; what runbreaker puts in it is a bare
interpreter invocation — no pipes, no redirects, no builtins, nothing that needs a
shell to mean anything.

One exception is unavoidable. When a path contains a space the string must be quoted,
and PowerShell reads a quoted executable path as a *string literal* unless it is
prefixed with the call operator `&` — which bash rejects outright. No single string
satisfies both, so `install` then adds a `powershell` override. It is still one
interpreter invocation, not a script. On space-free paths the override is absent.

## Windows

- **Locking** uses `fcntl.flock` on POSIX and `msvcrt.locking` on Windows. `msvcrt`
  has no shared-read mode, so readers take the exclusive lock too. That is also what
  keeps the atomic `os.replace` from raising: on Windows a rename fails if another
  process holds the destination open.
- **The interpreter** is `sys.executable` — the one that ran `install` — not a bare
  `python3`, which does not exist on a default Windows PATH. A hook that cannot start
  is worse than none: Claude proceeds unguarded, Copilot CLI reads the exit code as a
  deny and locks the user out of every tool.
- **Gate checks** are launched without a shell, so `command = ["npm", "run", "test"]`
  will not find `npm.cmd` on Windows. Use `["cmd", "/c", "npm", "run", "test"]`, or
  give the full path. A check that cannot be launched is recorded as a *config error*:
  it is surfaced and audited, but never counted toward `gate_failures`, so a typo
  cannot open the breaker.

`mypy --platform win32 src` is part of the checks below for exactly this reason.

## What this is not

**The agent's own shell tool is not blocked.** It cannot be — `runbreaker reset` has to
stay reachable — so `bash -c 'cat > file'` walks straight past the breaker on every
host. In VS Code the equivalents are its terminal and notebook-cell runners (e.g.
`run_notebook_cell`, and legacy `copilot_runVscodeCommand` / `copilot_runNotebookCell`).

This is a brake against runaway loops and burned budget, from an agent that is not
trying to escape. It is **not** a security boundary against one that is. For that you
need a read-only bind mount, a container, or a sandbox.

**Where this fits.** runbreaker governs *how much* a run may do — steps, tokens, cost,
wall-clock time, consecutive red gates. A pattern-based command blocker (e.g. a
preToolUse hook that refuses `rm -rf /`, `DROP TABLE`, or `git push --force`) governs
*what* a single command may do. The two are complementary layers, not substitutes:
run runbreaker as the runaway/cost brake and a command blocker — or your host's native
permission rules — as the destructive-action guard.

## Development

```bash
pip install -e '.[dev]'
pytest
ruff check src tests scripts
mypy src
mypy --platform win32 src            # the locking path differs per platform
python scripts/check_stdlib_only.py
```

Hooks have **zero runtime dependencies** and must keep it that way: the host CLI
spawns them as a bare `python3`, with no venv active. `scripts/check_stdlib_only.py`
enforces it, and CI runs the suite on Linux and Windows because the locking backend,
the interpreter path and the shell quoting all differ between them.
