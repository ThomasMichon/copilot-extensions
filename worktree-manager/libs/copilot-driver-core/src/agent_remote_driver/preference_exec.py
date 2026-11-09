"""Trusted final-context reader followed by a PID-preserving target exec."""

from __future__ import annotations

import json
import os
import socket
from pathlib import Path

from .preference_attestation import (
    DIGEST_ENV, FD_ENV, MAX_FRAME, MODE_ENV, NONCE_ENV, TARGET_ENV, component_digest,
    namespace_context, path_digest, process_start,
)
from .session_preferences import execution_settings


def main(argv: list[str]) -> int:
    if not argv:
        raise ValueError("target preference exec requires a program")
    fd = int(os.environ.pop(FD_ENV))
    nonce = os.environ.pop(NONCE_ENV)
    expected_digest = os.environ.pop(DIGEST_ENV)
    mode = os.environ.pop(MODE_ENV)
    target = os.environ.pop(TARGET_ENV)
    digest = component_digest()
    if digest != expected_digest:
        raise ValueError("target preference component digest mismatch")
    receipt = execution_settings(argv, None, os.getpid())
    receipt["version"] = 2
    space = namespace_context()
    space["home"] = path_digest(Path.home())
    space["cwd"] = path_digest(Path.cwd())
    receipt["authority"] = {
        "kind": "wrapper-v1", "digest": digest, "mode": mode, "target": target,
        "space": space, "start": process_start(os.getpid()),
    }
    frame = json.dumps({"proof": {"nonce": nonce}, "receipt": receipt}).encode() + b"\n"
    if len(frame) > MAX_FRAME:
        raise ValueError("target preference attestation is too large")
    with socket.socket(fileno=fd) as channel:
        channel.settimeout(30)
        channel.sendall(frame)
        # No agent exec before the Host accepts the actual execution binding.
        if channel.recv(1) != b"\x01":
            return 78
    # Binding secrets and channel never reach Copilot or its descendants.
    os.execvpe(argv[0], argv, os.environ.copy())
    return 0
