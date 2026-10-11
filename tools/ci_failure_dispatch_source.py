#!/usr/bin/env python3
"""Checkout convenience wrapper for the packaged CI failure source adapter."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "agent-dispatch" / "src"))
from agent_dispatch.producers.ci_failure_source import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
