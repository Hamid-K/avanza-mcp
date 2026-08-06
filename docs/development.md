# Development Workflow

Every meaningful change should be versioned in git and pass tests before it is committed.

## Setup

```bash
uv sync --dev
```

If `uv` is missing:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

## Test

```bash
uv run scripts/verify.sh
```

The verification script includes read-only checks for the 65-instrument/107
account-position strategy master, the buy-back freshness/authority contract,
and the authoritative factor/order/displacement/risk overlays. A stamped
buy-back artifact is not treated as current broker state; both exact account
snapshots must be refreshed before any action proposal. The gate also checks
that the objective-level audit remains explicitly open while live refresh,
event windows, and forward KPI evidence remain incomplete. The forward KPI
audit preserves all 12 scorecard definitions but records zero completed
forward measures until aligned account/benchmark observations and the frozen
starting-holdings replay are available. Each strategy-master
account row must also carry exact tenant/account/orderbook scope. Each daily
buy-back candidate must include explicit promotion and rejection/hold evidence,
including rows classified as ledger-only. The clean-sheet, factor,
pending-order, displacement, risk, and live-reconciliation overlays must carry
the same exact scope and fail-closed authority metadata. Transaction coverage is
also checked independently: historical summary rows must reconcile to both
exact accounts, manual exits must remain attributable, and missing raw or recent
transaction evidence must keep same-day BUY attribution explicitly open.
Those portfolio overlays are stamped with `STAMPED_ANALYSIS_SNAPSHOT`,
`live_state_current=false`, `live_refresh_verified=false`, and
`requires_new_scoped_live_refresh_before_action=true`; these fields are
validated both directly and through the completion audit.
The scheduler ledger is also parsed independently: the canonical Approval C
queue must remain exactly 18 rows, every non-terminal row must have a next
check, and terminal rows left in the active section must remain an explicit
archive-gap blocker.
Catalyst evidence is also checked independently: every sourced upcoming row
must retain a defined status, unverified dates must remain
`WAITING_OFFICIAL_DATE`, and due issuer rows must remain release/reversal gated.
Completion-audit enrichment is deterministic and idempotent so repeated
verification cannot duplicate requirement evidence clauses.
When live position audits retain intentional holding-only drift, the
completion-audit link also requires owner, reason, review timing,
`allowed_mismatches=[holding]`, and `rebaseline_authorized=false` metadata for
every acknowledged exception; zero unresolved mismatches alone is insufficient.
When linked coverage requires a new scoped refresh, the completion audit must
also mark the previous broker checkpoint as historical and set an explicit
refresh-before-action state.
The completion audit also links structural strategy-master and portfolio-control
counts; those links are not a substitute for current scoped broker evidence.

## Mandatory Quality Gates

Install local git hooks once per clone:

```bash
git config core.hooksPath .githooks
chmod +x .githooks/pre-commit .githooks/pre-push scripts/verify.sh
```

After this, both `git commit` and `git push` will fail unless `scripts/verify.sh` passes.

## Versioning Discipline

Use small commits. A commit should describe one coherent change and include matching documentation or tests when behavior changes.

Every behavior-changing code commit must update `CHANGELOG.md` in the same
tracked change. Do not leave source or test changes only in the working tree;
after the quality gates pass, commit and push them unless the user explicitly
requests a local-only change.

### Change Approval

Agents must obtain explicit current-thread user approval before modifying
repository code, tests, scripts, configuration, documentation, or automation.
Analysis, monitoring, diagnosis, test execution, committing, and a standing
task objective do not grant edit approval. Before editing, state the intended
files and scope; do not infer approval from a prior task or from the fact that
the repository is already being reviewed.

Version source of truth is `pyproject.toml` (`project.version`). The runtime surfaces this version in CLI/TUI/MCP.

For each release:

1. Bump `project.version` in `pyproject.toml`.
2. Add a dated release section in `CHANGELOG.md`.
3. Run `uv run scripts/verify.sh`.
4. Commit with release-focused message and tag/push from git.

Before committing:

```bash
uv run scripts/verify.sh
git status --short
git diff --check
```

Do not commit `.env`, credentials, generated caches, or local virtual environments.

## Security Scanning

GitHub Actions run automatically on the repository:

- **CodeQL** (`.github/workflows/codeql.yml`) — static analysis of the
  Python backend and the JavaScript frontend with the security-extended
  query pack, on pushes/PRs to `main` and weekly.
- **Dependency vulnerability audit** (`.github/workflows/security-audit.yml`)
  — `pip-audit` over the exported uv lockfile plus `osv-scanner` over
  `uv.lock` directly, on dependency changes and daily (new advisories hit
  the daily run). OSV results land in the repository Security tab as SARIF.
- **Dependency review** (`.github/workflows/dependency-review.yml`) — PRs
  that introduce dependencies with known high-severity vulnerabilities fail
  and get an explanatory comment.
- **Dependabot** (`.github/dependabot.yml`) — weekly grouped update PRs for
  Python packages and the workflow actions themselves; security updates are
  enabled at the repository level.

Frontend third-party code is not package-managed: the two CDN files (Vue,
lightweight-charts) are version- and SRI-pinned in
`avanza_mcp/web/static/index.html`, with committed fallback copies under
`web/static/vendor/`. Bumping them means updating the URL, the integrity
hash, and the vendor copy together.
