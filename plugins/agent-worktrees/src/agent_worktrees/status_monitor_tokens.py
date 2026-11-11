"""Separate token observation from explicit service-owned initialization."""

from __future__ import annotations

import secrets
from pathlib import Path


def read_token(path: Path) -> str | None:
    try:
        token = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    return token or None


def load_or_create_token(path: Path) -> str:
    try:
        token = read_token(path)
        if token:
            return token
    except OSError:
        pass
    token = secrets.token_urlsafe(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(token + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return token
