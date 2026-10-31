"""Historical execution proof must survive a squash without old PR objects."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

CONTRACT = Path(__file__).resolve().parents[1] / "contract"
SOURCES = (
    "plugins/agent-bridge/tests/test_http48_capture_probes.py",
    "tools/bridge_http48_evidence.py",
    "tools/capture_bridge_http48.py",
)


@pytest.mark.parametrize("source", SOURCES)
def test_http48_archives_preserve_exact_captured_source_bytes(source):
    packet = json.loads(
        (CONTRACT / "fixtures" / "http" / "current" / "http48-retained-inbox.json").read_text(
            encoding="utf-8",
        ),
    )
    identity = packet["execution_closure"][source]
    data = (CONTRACT / "source-blobs" / "http48" / (identity["blob"] + ".txt")).read_bytes()
    assert hashlib.sha256(data).hexdigest() == identity["sha256"]
    assert hashlib.sha1(
        b"blob " + str(len(data)).encode() + b"\0" + data, usedforsecurity=False,
    ).hexdigest() == identity["blob"]
