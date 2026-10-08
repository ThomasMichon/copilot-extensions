"""Report producer election separately from emitter process health."""

from __future__ import annotations

import json
import math
import time
from pathlib import Path
from typing import Any, Mapping

from .client import DispatchError
from .producers.emitter import lease_scope


def _timestamp(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def diagnose(
    spec: Mapping[str, Any],
    *,
    holder: str,
    lease: Mapping[str, Any] | None,
    health: Mapping[str, Any] | None = None,
    eligible: bool = True,
    lease_holder_eligible: bool | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    current = time.time() if now is None else now
    interval = float(spec["interval_seconds"])
    stale_after = 2 * interval + float(spec.get("timeout_seconds") or 0)
    renewed_at = _timestamp(lease.get("renewed_at")) if lease else None
    lease_age = current - renewed_at if renewed_at is not None else None
    observed_at = _timestamp(health.get("updated_at")) if health else None
    health_age = current - observed_at if observed_at is not None else None
    findings: list[str] = []
    if not eligible:
        findings.append("local-holder-ineligible")
    if lease_holder_eligible is False:
        findings.append("lease-holder-ineligible")
    if lease is None:
        state = "unheld"
    elif lease_age is None or lease_age < 0:
        state = "lease-renewal-unknown"
    elif lease_age > stale_after:
        state = "lease-stale"
    elif lease.get("holder") != holder:
        state = "remote-lease-fresh"
    elif health_age is None or health_age < 0:
        state = "production-unknown"
    elif health_age > stale_after:
        state = "emitter-health-stale"
    elif health.get("error") or health.get("ok") is False:
        state = "emitter-failed"
    elif health.get("held") is not True:
        state = "lease-not-held"
    elif (
        not isinstance(health.get("lease"), Mapping)
        or health["lease"].get("holder") != holder
    ):
        state = "health-holder-mismatch"
    elif health.get("returncode") != 0:
        state = "production-unknown"
    else:
        state = "tick-succeeded"
    return {
        "scope": lease_scope(dict(spec)),
        "local_holder": holder,
        "eligible": eligible,
        "lease_holder_eligible": lease_holder_eligible,
        "interval_seconds": interval,
        "stale_after_seconds": stale_after,
        "production_state": state,
        "findings": findings,
        "lease": dict(lease) if lease else None,
        "lease_age_seconds": lease_age,
        "health_age_seconds": health_age,
        "health": dict(health) if health else None,
        "recovery_candidate": (
            eligible
            and state == "lease-stale"
            and lease is not None
            and lease.get("holder") != holder
        ),
    }


def inspect(client: Any, spec_path: str, spec: dict, *, holder: str,
            declaration_path: str | None = None) -> dict:
    eligible = True
    pinned_eligible = None
    lease = client.get_schedule_lease(lease_scope(spec))
    if declaration_path is not None:
        from .registrar_discovery import read_declaration_file_set
        from .registrar_reconcile import runs_on_machine

        matches = [
            declaration
            for declaration in read_declaration_file_set(Path(declaration_path))
            if declaration.kind == "emitter" and declaration.spec.get("id") == spec["id"]
        ]
        if len(matches) != 1:
            raise ValueError("expected exactly one matching emitter declaration")
        eligible = runs_on_machine(matches[0], holder)
        if lease:
            pinned_eligible = runs_on_machine(matches[0], lease["holder"])
    health_path = Path(spec_path).with_suffix(".health.json")
    health = None
    error = None
    try:
        value = json.loads(health_path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("emitter health must be an object")
        health = value
    except FileNotFoundError:
        error = "emitter health is missing"
    except (OSError, ValueError) as exc:
        error = f"emitter health is unreadable: {exc}"
    result = diagnose(
        spec, holder=holder, lease=lease, health=health,
        eligible=eligible, lease_holder_eligible=pinned_eligible,
    )
    result["health_error"] = error
    return result


def recover(client: Any, diagnosis: dict, *, expected_holder: str) -> dict:
    """Explicit migration consent, fenced against holder changes and renewals."""
    lease = diagnosis["lease"]
    if not diagnosis["recovery_candidate"] or lease["holder"] != expected_holder:
        raise ValueError("recovery requires a stale foreign pin matching --expected-holder")
    result = client.release_schedule_lease(
        diagnosis["scope"], expected_holder,
        expected_renewed_at=lease["renewed_at"],
    )
    if not result.get("released"):
        raise DispatchError(409, "lease was not released; re-run the read-only diagnosis")
    return {
        **diagnosis,
        "recovery": "lease-released",
        "production_verified": False,
        "next": "Run the target emitter tick, then verify a later ordinary renewal.",
    }
