# Development Workflow

Every meaningful change should be versioned in git and pass tests before it is committed.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Test

```bash
scripts/verify.sh
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

Before committing:

```bash
scripts/verify.sh
git status --short
git diff --check
```

Do not commit `.env`, credentials, generated caches, or local virtual environments.
