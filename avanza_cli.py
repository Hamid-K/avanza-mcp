#!/usr/bin/env python3
"""Thin executable shim; the implementation lives in the avanza_mcp package."""
from avanza_mcp.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
