"""Locate an existing Worktrees enrollment namespace without opening it."""

import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlsplit, urlunsplit


def continuity_state_path(repository: str, context: str) -> Path:
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", context):
        raise ValueError("Invalid continuity context name.")
    if any(c.isspace() or ord(c) < 32 or 127 <= ord(c) <= 159 for c in repository):
        raise ValueError("Expected a credential-free HTTPS repository URL.")
    try:
        parts = urlsplit(repository)
        host, port = parts.hostname or "", parts.port
    except ValueError:
        raise ValueError("Invalid repository URL.") from None
    path = parts.path.rstrip("/")
    if (
        parts.scheme != "https" or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]*", host)
        or parts.username is not None or parts.password is not None
        or any(c in repository for c in ("?", "#", "\\"))
        or not path.startswith("/") or any(p in {"", ".", ".."} for p in path[1:].split("/"))
        or not host[-1:].isalnum()
    ):
        raise ValueError("Expected a credential-free HTTPS repository URL.")
    authority = host.lower() + (f":{port}" if port not in (443, None) else "")
    url = urlunsplit(("https", authority, path, "", ""))
    identity = json.dumps([url, context], separators=(",", ":")).encode("utf-8")
    return Path.home() / (".fleet-continuity-" + hashlib.sha256(identity).hexdigest())
