"""Read-only admission of a foreground terminal's finite launch offer."""

from __future__ import annotations

import json
import stat
import sys
from pathlib import Path


def validate(raw: str | None) -> tuple[Path, dict[str, object]] | None:
    if not raw:
        return None
    request = Path(raw)
    info = request.lstat()
    if (
        not request.is_absolute() or request.name != "request.json"
        or not stat.S_ISREG(info.st_mode)
        or getattr(info, "st_file_attributes", 0) & 0x400
        or info.st_size > 4096
    ):
        raise ValueError("Invalid terminal launch request")
    data = json.loads(request.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise ValueError("Invalid terminal launch identity")
    nonce = data.get("nonce")
    if (
        data.get("version") != 1 or type(data.get("version")) is not int
        or not isinstance(nonce, str) or len(nonce) != 32
        or any(char not in "0123456789abcdef" for char in nonce)
        or type(data.get("host_pid")) is not int or data["host_pid"] <= 0
    ):
        raise ValueError("Invalid terminal launch identity")
    if (request.parent / "result.json").exists():
        raise FileExistsError("Terminal launch request was already consumed")
    return request, data


def validate_or_exit(raw: str | None) -> None:
    try:
        validate(raw)
    except (OSError, ValueError) as error:
        print(f"Invalid terminal launch offer: {error}", file=sys.stderr)
        raise SystemExit(1) from error
