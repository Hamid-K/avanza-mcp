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

## Mandatory Quality Gates

Install local git hooks once per clone:

```bash
git config core.hooksPath .githooks
chmod +x .githooks/pre-commit .githooks/pre-push scripts/verify.sh
```

After this, both `git commit` and `git push` will fail unless `scripts/verify.sh` passes.

## Versioning Discipline

Use small commits. A commit should describe one coherent change and include matching documentation or tests when behavior changes.

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
