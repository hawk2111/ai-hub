# ai-hub

A monorepo of small, self-contained tools for working with AI coding agents.

Each tool lives under [`tools/`](tools/) and is fully independent: its own build
config, dependencies, tests, README and release cadence. Nothing here is a shared
library — pick a tool, `cd` into it, and it stands on its own.

## Tools

| Tool | What it does | Language | Status |
|---|---|---|---|
| [runbreaker](tools/runbreaker/) | Circuit breaker + budget guard for unattended AI coding agents (Claude Code, Codex CLI, Copilot CLI) — trips on step / time / token / cost budgets or a repeatedly red quality gate. | Python | active |

## Repository layout

```
ai-hub/
├── tools/                 # the products — one self-contained directory per tool
│   └── runbreaker/
│       ├── pyproject.toml # own build + dependencies
│       ├── README.md      # own docs
│       ├── src/           # source
│       ├── tests/         # own test suite
│       └── scripts/       # tool-specific dev scripts
├── .github/workflows/     # one path-filtered workflow per tool
└── README.md              # this index
```

## Working on a tool

Everything is scoped to the tool's own directory:

```bash
cd tools/runbreaker
pip install -e '.[dev]'
pytest
```

## Adding a new tool

1. Create `tools/<name>/` with its own build config (`pyproject.toml`,
   `package.json`, …), `README.md` and tests. Keep it self-contained.
2. Add a path-filtered CI workflow at `.github/workflows/<name>.yml`
   (`paths: ["tools/<name>/**"]`) so it only runs when that tool changes.
3. Add a row to the **Tools** table above.

Tools version and release independently; tag releases as `<name>-vX.Y.Z`.
