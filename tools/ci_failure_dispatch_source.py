#!/usr/bin/env python3
"""Deprecated checkout entrypoint; use the installed agent-dispatch runtime."""

import sys

if __name__ == "__main__":
    print("Use the managed agent-dispatch interpreter: python -m agent_dispatch.producers.ci_failure_source", file=sys.stderr)
    raise SystemExit(2)
