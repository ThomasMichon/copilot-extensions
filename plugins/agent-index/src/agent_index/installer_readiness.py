"""Installer/readiness contract adapter for agent-index."""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping, Sequence
from typing import Any

MODULE_ID = "agent-index/runtime"


def _result(state: str, detail: str) -> dict[str, Any]:
    return {
        "schema": "copilot-extensions.module-readiness",
        "version": 1,
        "module": MODULE_ID,
        "state": state,
        "detail": detail,
    }


def evaluate(
    status: Mapping[str, Any],
    configured_sources: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Map service and corpus status without starting a service or reindexing."""
    if not status.get("running"):
        detail = status.get("error") or "no healthy endpoint is discoverable"
        return _result(
            "failed",
            "The agent-index service is unavailable: "
            f"{detail}. Re-run the installer and inspect `agent-index status`.",
        )
    index = status.get("index")
    if not isinstance(index, Mapping) or index.get("chunks") is None:
        return _result(
            "failed",
            "The agent-index service is running, but corpus state is unknown. "
            "Inspect `agent-index status`; do not assume an empty index.",
        )
    chunks = index.get("chunks")
    if not isinstance(chunks, int) or isinstance(chunks, bool) or chunks < 0:
        return _result(
            "failed",
            "The agent-index service returned an invalid corpus count. Inspect "
            "`agent-index status` before reindexing.",
        )
    if not configured_sources:
        return _result(
            "configuration-empty",
            "The agent-index service is healthy, but no corpus sources are "
            "configured. No corpus was created or indexed.",
        )
    if chunks == 0:
        return _result(
            "configuration-empty",
            f"{len(configured_sources)} corpus source(s) are configured, but the "
            "measured corpus contains no indexed chunks. Run reindex explicitly "
            "only if content should exist.",
        )
    return _result(
        "ready",
        f"The service is healthy with {chunks} indexed chunk(s) from "
        f"{len(configured_sources)} configured source(s).",
    )


def emit(result: dict[str, Any]) -> int:
    """Write one readiness result and map failed to a nonzero exit."""
    json.dump(result, sys.stdout, sort_keys=True)
    sys.stdout.write("\n")
    return 1 if result["state"] == "failed" else 0
