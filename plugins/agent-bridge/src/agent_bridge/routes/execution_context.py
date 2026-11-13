"""Fresh reporter naming through the shared machine-transport resolver."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Annotated, Literal

import yaml
from fastapi import APIRouter, HTTPException, Query, Request
from machine_transport import MachineEntry, parse_machines_yaml, resolve_local_execution_context

from ..models import ServiceConfig

router = APIRouter(tags=["machines"])
SCHEMA = "agent-bridge.execution-context/v1"


def _machine_entries(path: Path) -> dict[str, MachineEntry]:
    raw = yaml.load(path.read_text(encoding="utf-8"), Loader=getattr(yaml, "CSafeLoader", yaml.SafeLoader))
    if not isinstance(raw, dict) or not isinstance(raw.get("machines"), dict):
        raise ValueError("configured machines must be a mapping")
    for entry in raw["machines"].values():
        if not isinstance(entry, dict):
            raise ValueError("configured machine declarations must be mappings")
        ssh = entry.get("ssh")
        if ssh is not None:
            if not isinstance(ssh, dict):
                raise ValueError("configured SSH declarations must be mappings")
            profiles = ssh.get("environments")
            if profiles is not None and (
                not isinstance(profiles, list) or any(not isinstance(profile, dict) for profile in profiles)
            ):
                raise ValueError("configured SSH environments must be a list of mappings")
    return parse_machines_yaml(
        raw, default_ssh_alias_to_key=False, default_ssh_shell="bash",
        keep_unnamed_environments=True, preserve_environment_values=True,
    )


def current_context(cfg: ServiceConfig, hostname: str, environment: str) -> dict:
    """Resolve current reporter observations without a persisted naming cache."""
    entries: dict[str, MachineEntry] = {}
    paths = {
        Path(profile.machines_yaml).expanduser()
        for profile in cfg.topologies.values() if profile.machines_yaml
    }
    for path in sorted(paths):
        for key, entry in _machine_entries(path).items():
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
