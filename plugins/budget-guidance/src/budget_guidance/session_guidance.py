"""Bounded launch-time posture data, delivered only to the requesting session."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import DecimalException
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
from typing import Any

from . import __version__
from .models import (
    ModelError, _strict_object, default_config_path, load_json, parse_config, without_reader_grants,
)
from .posture import build_posture, unavailable_posture

MAX_INPUT_BYTES = 65536
MAX_GUIDANCE_BYTES = 4096
SESSION_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")


def startup_posture(cwd: Path, at: datetime) -> dict[str, Any]:
    """Read bounded inert configuration without provider I/O or reader grants."""
    try:
        path = default_config_path()
        if not path.is_absolute():
            path = cwd / path
        if not stat.S_ISREG(path.stat().st_mode):
            return unavailable_posture(at, "configuration is not a regular file")
        config = parse_config(load_json(path, max_bytes=MAX_INPUT_BYTES))
        return build_posture(without_reader_grants(config, at), at)
    except FileNotFoundError:
        return unavailable_posture(at, "configuration not found")
    except (ModelError, OSError, DecimalException, RecursionError, RuntimeError, ValueError):
        return unavailable_posture(at, "configuration could not provide a valid budget posture")


def cue(posture: dict[str, Any]) -> dict[str, Any]:
    """Project selected evidence; detailed contradictions and opaque errors stay out."""
    return {
        "schema": "copilot-extensions.budget-session-cue",
        "version": 1,
        "evaluated_at": posture["evaluated_at"],
        "availability": posture["availability"],
        "error": posture["error"],
        "missing_fields": posture["missing_fields"],
        "adapters": [
            {key: adapter[key] for key in (
                "id", "source", "availability", "captured_at", "freshness", "freshness_seconds",
            )}
            for adapter in posture["adapters"]
        ],
        "fields": {
            name: {
                "selected": {
                    key: value for key, value in field["selected"].items()
                    if key != "value" or name != "trailing_rates"
                },
                "contradiction_count": len(field["contradictions"]),
            }
            for name, field in posture["fields"].items()
        },
        "calculated": posture["calculated"],
    }


def render_guidance(posture: dict[str, Any]) -> str:
    """Enforce the complete UTF-8 file budget without truncating numeric evidence."""
    header = f"[owner: budget-guidance@{__version__}]\n"
    content = header + json.dumps(cue(posture), ensure_ascii=True, separators=(",", ":")) + "\n"
    if len(content.encode("utf-8")) <= MAX_GUIDANCE_BYTES:
        return content
    omitted = unavailable_posture(
        datetime.fromisoformat(posture["evaluated_at"].replace("Z", "+00:00")),
        "session cue exceeded its byte budget; budget evidence is unavailable",
    )
    return header + json.dumps(cue(omitted), separators=(",", ":")) + "\n"


def _plain_directory(path: Path) -> None:
    path.mkdir(exist_ok=True)
    metadata = path.lstat()
    if not stat.S_ISDIR(metadata.st_mode) or (
        getattr(metadata, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    ):
        raise ModelError("session guidance directory is not a plain directory")


def write_session_guidance(
    payload: dict[str, Any], *, home: Path | None = None, at: datetime | None = None,
) -> Path:
    """Atomically replace this session's cue on fresh and resumed launches."""
    session_id = payload.get("sessionId")
    if not isinstance(session_id, str) or not SESSION_IDENTIFIER.fullmatch(session_id):
        raise ModelError("session guidance requires a valid session identifier")
    raw_cwd = payload.get("cwd")
    evaluated_at = at or datetime.now(timezone.utc)
    cwd = Path(raw_cwd) if isinstance(raw_cwd, str) and raw_cwd else None
    if cwd is None or not cwd.is_absolute() or not cwd.is_dir():
        posture = unavailable_posture(evaluated_at, "session directory is unavailable")
    else:
        posture = startup_posture(cwd, evaluated_at)
    content = render_guidance(posture)
    directory = home or Path.home()
    for part in (".copilot", "session-state", session_id, "instructions", "budget-guidance"):
        directory = directory / part
        _plain_directory(directory)
    target = directory / "session-guidance.instructions.md"
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n", dir=directory,
            prefix=".session-guidance.", suffix=".tmp", delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(content)
        os.replace(temporary, target)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink()
    return target


def main() -> int:
    """Always emit the output-free hook envelope; keep failures explicit and safe."""
    result = 0
    try:
        raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
        if len(raw) > MAX_INPUT_BYTES:
            raise ModelError("session guidance input exceeded its byte budget")
        payload = json.loads(raw, object_pairs_hook=_strict_object)
        if not isinstance(payload, dict):
            raise ModelError("session guidance input must be an object")
        write_session_guidance(payload)
    except (ModelError, OSError, UnicodeError, ValueError, RecursionError):
        print("budget-guidance: session cue could not be written", file=sys.stderr)
        result = 1
    finally:
        sys.stdout.write("{}")
    return result


if __name__ == "__main__":
    raise SystemExit(main())
