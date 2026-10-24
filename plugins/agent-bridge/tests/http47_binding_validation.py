"""Source-only mode for validating the capture guard without publishing evidence."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile

import bridge_http47_evidence as evidence
import bridge_http47_runtime as runtime

_directory: tempfile.TemporaryDirectory | None = None
_proof: dict = {}


def start() -> None:
    global _directory, _proof
    selected = (
        "-k" in sys.argv
        and "http47_binding_portfolio" in sys.argv[sys.argv.index("-k") + 1]
    )
    if not selected or runtime.REQUEST in os.environ:
        return
    root = Path(__file__).resolve().parents[3]
    registry = json.loads((root / evidence.REGISTRY).read_bytes())
    _proof = {
        path: {"sha256": evidence.git_evidence.sha256_bytes((root / path).read_bytes())}
        for path in evidence.proof_paths(root, registry)
    }
    parent = root / ".test-venvs"
    parent.mkdir(exist_ok=True)
    _directory = tempfile.TemporaryDirectory(prefix="http47-capture-binding-test-", dir=parent)
    request = Path(_directory.name) / "request.json"
    data = evidence.encode({
        "root": str(root), "source_proof": _proof,
        "report": str(Path(_directory.name) / "observed.json"),
        "input_sha256": evidence.digest((root / evidence.INPUT).read_bytes()),
        "control_sha256": evidence.digest((root / evidence.CONTROL_INPUT).read_bytes()),
    })
    request.write_bytes(data)
    os.environ[runtime.REQUEST] = str(request)
    os.environ[runtime.REQUEST_SHA] = evidence.digest(data)


def finish() -> None:
    if _directory is None:
        return
    try:
        runtime.verify_interpreters(Path(_directory.name) / "interpreters", _proof)
    finally:
        _directory.cleanup()
