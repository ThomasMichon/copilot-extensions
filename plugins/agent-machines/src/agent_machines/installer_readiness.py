"""Installer/readiness contract adapter for agent-machines."""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence
from typing import Any

from .layout import LayoutReport

MODULE_ID = "agent-machines/runtime"


def _result(state: str, detail: str) -> dict[str, Any]:
    return {
        "schema": "copilot-extensions.module-readiness",
        "version": 1,
        "module": MODULE_ID,
        "state": state,
        "detail": detail,
    }


def evaluate(reports: Sequence[LayoutReport]) -> dict[str, Any]:
    """Map the existing layout doctor model to installer readiness."""
    errors = [
        finding.message
        for report in reports
        for finding in report.findings
        if finding.level == "error"
    ]
    if errors:
        return _result(
            "failed",
            "Machine-state configuration is invalid: "
            + "; ".join(errors)
            + ". Run `agent-machines doctor` for the owning paths and remedies.",
        )

    package_count = sum(report.package_count for report in reports)
    unavailable_count = sum(report.status == "unavailable" for report in reports)
    if package_count == 0:
        unavailable = (
            f" {unavailable_count} adopted repository path(s) are unavailable;"
            if unavailable_count
            else ""
        )
        return _result(
            "configuration-empty",
            "The runtime is healthy, but no applicable machine requirement "
            f"packages are configured.{unavailable} Add or restore a package "
            "only when this machine should manage declared state.",
        )

    suffix = (
        f" {unavailable_count} optional adopted repository path(s) are unavailable."
        if unavailable_count
        else ""
    )
    return _result(
        "ready",
        f"The runtime found {package_count} applicable machine requirement "
        f"package(s).{suffix}",
    )


def emit(result: dict[str, Any]) -> int:
    """Write one readiness result and map failed to a nonzero exit."""
    json.dump(result, sys.stdout, sort_keys=True)
    sys.stdout.write("\n")
    return 1 if result["state"] == "failed" else 0
