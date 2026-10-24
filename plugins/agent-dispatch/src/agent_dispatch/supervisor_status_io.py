"""Native-generation-fenced I/O for the supervisor's optional progress record."""

from __future__ import annotations

import json
import os
from pathlib import Path


def runtime_status_path(scope: str) -> Path:
    from .config import run_dir
    from .single_instance import lock_path_for

    return lock_path_for(run_dir(), scope).with_suffix(".status.json")


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
