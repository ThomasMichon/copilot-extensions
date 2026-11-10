"""Native-generation-fenced I/O for the supervisor's optional progress record."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path


def write_runtime_status(scope: str, summary) -> None:
    from . import __version__

    path = runtime_status_path(scope)
    path.parent.mkdir(parents=True, exist_ok=True)
    previous, _error = read_status(path)
    payload = {
        "runtime_version": __version__,
        "updated_at": getattr(summary, "completed_at", time.time()),
        "cycle_started_at": (previous or {}).get("cycle_started_at"),
        "cycle_id": (previous or {}).get("cycle_id"),
        "generation": (previous or {}).get("generation"),
        "progress": getattr(summary, "progress", None),
        "running": summary.running,
        "backing_off": summary.backing_off,
        "dead": getattr(summary, "dead", []),
    }
    for key in ("owner", "lane_readiness"):
        if key in (previous or {}):
            payload[key] = previous[key]
    temporary = path.with_suffix(f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload), encoding="utf-8")
    os.replace(temporary, path)


def runtime_status_path(scope: str, *, directory: Path | None = None) -> Path:
    from .config import run_dir
    from .single_instance import lock_path_for

    return lock_path_for(run_dir() if directory is None else directory, scope).with_suffix(".status.json")


def read_status(path: Path) -> tuple[dict | None, str | None]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, None
    except OSError:
        return None, "status-read-unavailable"
    except ValueError:
        return None, "status-json-invalid"
    if not isinstance(payload, dict):
        return None, "status-object-required"
    return payload, None


def write_progress(path: Path, progress: dict, *, pid: int, start_token: str | None) -> None:
    payload, error = read_status(path)
    if error is not None or payload is None:
        raise RuntimeError("supervisor progress snapshot is unavailable")
    if payload.get("cycle_id") != progress.get("cycle_id"):
        raise RuntimeError("supervisor progress cycle is superseded")
    generation = payload.get("generation")
    if (
        not isinstance(generation, dict) or generation.get("pid") != pid
        or not isinstance(start_token, str) or not start_token
        or generation.get("start_token") != start_token
    ):
        raise RuntimeError("supervisor progress generation is superseded")
    payload["progress"] = {**progress, "generation": generation}
    temporary = path.with_suffix(f".{pid}.tmp")
    temporary.write_text(json.dumps(payload), encoding="utf-8")
    os.replace(temporary, path)
