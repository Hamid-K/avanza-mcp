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
pytest
python3 -m py_compile avanza_cli.py
```

## Versioning Discipline

Use small commits. A commit should describe one coherent change and include matching documentation or tests when behavior changes.

Before committing:

```bash
pytest
git status --short
git diff --check
```

Do not commit `.env`, credentials, generated caches, or local virtual environments.
