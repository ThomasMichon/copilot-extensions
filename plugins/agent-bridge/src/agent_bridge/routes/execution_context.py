"""Fresh reporter naming through the shared machine-transport resolver."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Annotated, Literal

import yaml
from fastapi import APIRouter, HTTPException, Query, Request
from machine_transport import MachineEntry, parse_machines_yaml_file, resolve_local_execution_context

from ..models import ServiceConfig

router = APIRouter(tags=["machines"])
SCHEMA = "agent-bridge.execution-context/v1"


def current_context(cfg: ServiceConfig, hostname: str, environment: str) -> dict:
    """Resolve current reporter observations without a persisted naming cache."""
    entries: dict[str, MachineEntry] = {}
    paths = {
        Path(profile.machines_yaml).expanduser()
        for profile in cfg.topologies.values() if profile.machines_yaml
    }
    for path in sorted(paths):
        for key, entry in parse_machines_yaml_file(path).items():
            if key in entries and entries[key] != entry:
                raise ValueError("conflicting machine definitions across topology profiles")
            entries[key] = entry
    context = resolve_local_execution_context(
        entries, hostname=hostname, environment=environment,
    )
    return {"schema": SCHEMA, "context": asdict(context)}


@router.get("/api/v1/execution-context")
def execution_context(
    request: Request,
    hostname: Annotated[str, Query(min_length=1, max_length=255)],
    environment: Literal["windows", "wsl", "linux"],
) -> dict:
    """Return naming facts for the authenticated reporter, not mutation authority."""
    try:
        return current_context(request.app.state.config, hostname, environment)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        raise HTTPException(
            status_code=503, detail="current execution context unavailable",
        ) from exc
