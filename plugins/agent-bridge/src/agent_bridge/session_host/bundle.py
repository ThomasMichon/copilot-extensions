"""Build a content-hashed single-file zipapp of the **host-side** Session Host
code, so a remote far side (CodeSpace / machine-mesh) runs the host's **exact
bytes** rather than a separately-versioned artifact.

This is the concrete answer to "how do we keep the host and the CodeSpace on the
same version": we don't version the CodeSpace at all. The frontend ships its own
Session Host as one file, named by a hash of its source; the far side caches it
by that hash and re-fetches only when the hash changes. Version skew is
structurally impossible (same bytes), reconnects are cheap (cache hit), and the
seq/ack ``protocol_version`` handshake remains the backstop.

The bundle carries only the **host-role** closure -- the modules
``launcher.main`` needs to own a child and serve the reattach endpoint -- which
imports cleanly on Linux with **zero heavy dependencies** (no fastapi/uvicorn/acp).
Run it on the far side as::

    python3 session-host-<hash>.pyz --port 0 --state-file F -- copilot --acp --stdio

with ``AGENT_BRIDGE_SESSION_HOST_NONCE`` in the environment.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
import zipapp
from pathlib import Path

# The minimal host-role import closure (verified to import on Linux with no heavy
# deps). Deliberately excludes the frontend-only modules (client, acp_adapter,
# host_index, version_mux, spawner) and agent_runner (which pulls the full
# agent-bridge). launcher.main() owns a child from an explicit argv and serves.
_BUNDLE_MODULES = (
    "__init__.py",
    "winjob.py",
    "session_host/__init__.py",
    "session_host/protocol.py",
    "session_host/host.py",
    "session_host/osutil.py",
    "session_host/launcher.py",
)
_MAIN = "agent_bridge.session_host.launcher:main"


def _pkg_root() -> Path:
    """The ``agent_bridge`` package dir (parent of ``session_host``)."""
    return Path(__file__).resolve().parents[1]


def bundle_source_hash() -> str:
    """A stable sha256 over the staged **source** (not the zip, whose metadata
    varies) -- the cache key that guarantees byte-identity across host and CS."""
    root = _pkg_root()
    h = hashlib.sha256()
    for rel in _BUNDLE_MODULES:
        h.update(rel.encode("utf-8"))
        h.update(b"\0")
        h.update((root / rel).read_bytes())
        h.update(b"\0")
    h.update(_MAIN.encode("utf-8"))
    return h.hexdigest()


def bundle_filename(source_hash: str | None = None) -> str:
    """The content-addressed bundle filename (the CS caches under this name)."""
    sha = source_hash or bundle_source_hash()
    return f"session-host-{sha[:16]}.pyz"


def build_session_host_bundle(
    dest_dir: str | os.PathLike[str] | None = None,
) -> tuple[Path, str]:
    """Build (or reuse) the host-side Session Host zipapp.

    Returns ``(path, source_hash)``. The file is named by the source hash and
    reused if already present, so repeated calls (every connect) are cheap and a
    reconnect can skip re-shipping when the CS already has this hash.
    """
    sha = bundle_source_hash()
    dest = (
        Path(dest_dir) if dest_dir
        else Path(tempfile.gettempdir()) / "agent-bridge-bundles"
    )
    dest.mkdir(parents=True, exist_ok=True)
    out = dest / bundle_filename(sha)
    if out.exists():
        return out, sha

    root = _pkg_root()
    with tempfile.TemporaryDirectory(prefix="agbridge-bundle-") as td:
        stage = Path(td) / "src"
        for rel in _BUNDLE_MODULES:
            dst = stage / "agent_bridge" / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes((root / rel).read_bytes())
        tmp_out = Path(td) / "bundle.pyz"
        # A shebang so the far side can also run ``./bundle.pyz`` directly.
        zipapp.create_archive(
            stage, target=str(tmp_out), main=_MAIN,
            interpreter="/usr/bin/env python3",
        )
        os.replace(tmp_out, out)
    return out, sha
