"""Installer/readiness contract adapter for agent-codespaces."""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence
from typing import Any

MODULE_ID = "agent-codespaces/runtime"


def _result(state: str, detail: str) -> dict[str, Any]:
    return {
        "schema": "copilot-extensions.module-readiness",
        "version": 1,
        "module": MODULE_ID,
        "state": state,
        "detail": detail,
    }


def evaluate(
    *,
    auth_findings: Sequence[str],
    registry_findings: Sequence[str],
    config_issues: Sequence[str],
    configured: bool,
) -> dict[str, Any]:
    """Map existing doctor/config checks without requiring a live CodeSpace."""
    failures = [*auth_findings, *registry_findings, *config_issues]
    if failures:
        return _result(
            "failed",
            "CodeSpace runtime prerequisites or configuration are invalid: "
            + "; ".join(failures)
            + ". Run `agent-codespaces doctor` for complete owner diagnostics.",
        )
    if not configured:
        return _result(
            "configuration-empty",
            "The runtime and GitHub authentication are healthy, but no adopted "
            "repository or config.d contribution is configured. A live CodeSpace "
            "is not required for runtime readiness.",
        )
    return _result(
        "ready",
        "The runtime, GitHub authentication, and configured CodeSpace inputs are "
        "healthy. A live CodeSpace instance is not required.",
    )


def emit(result: dict[str, Any]) -> int:
    """Write one readiness result and map failed to a nonzero exit."""
    json.dump(result, sys.stdout, sort_keys=True)
    sys.stdout.write("\n")
    return 1 if result["state"] == "failed" else 0
