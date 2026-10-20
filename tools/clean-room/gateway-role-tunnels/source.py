"""Closed-world Git materialization and owner-provided private results custody."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

PROOF = "tools/clean-room/gateway-role-tunnels/"
IMAGE = "fleet-manager/containers/gateway/"
PACKAGE_MODULES = frozenset("""
__init__ __main__ allocation artifact_staging artifacts client_diagnostics
gateway_auth gateway_broker gateway_client gateway_cloudflare gateway_contract gateway_controller
gateway_index gateway_manifest gateway_reconcile gateway_server
gateway_service_channel gateway_service_connector gateway_service_context
gateway_service_listener gateway_service_policy gateway_service_server
gateway_ssh_policy gateway_ssh_runtime gateway_transport gateway_worker ledger
node_journal observation_store observations operations planning release release_auth
release_selection runtime_admission runtime_identity staging_cleanup
""".split())
RESOURCES = frozenset("""
standalone_build.py versioned_runtime.py versioned_runtime_leases.py
versioned_runtime_construction.py versioned_runtime_publication.py versioned_runtime_source.py
""".split())
RUNNER_FILES = frozenset({
    "run.py", "source.py", "engine.py", "witness.py",
    "fixture_auth.py", "fixture_service.py", "probe.py",
})
REQUIRED = frozenset({
    "fleet-manager/pyproject.toml", "fleet-manager/setup.py", "fleet-manager/README.md",
    *(f"fleet-manager/src/fleet_manager/{name}.py" for name in PACKAGE_MODULES),
    *(f"libs/versioned-runtime/{name}" for name in RESOURCES),
    *(PROOF + name for name in RUNNER_FILES),
    IMAGE + "Dockerfile", IMAGE + "Dockerfile.dockerignore",
})


class ProofError(RuntimeError):
    """Value-free, classified proof failure."""

    def __init__(self, code: str, category: str = "runtime-install") -> None:
        super().__init__(code)
        self.category = category


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def save(path: Path, value: object) -> None:
    pending = path.with_suffix(".pending")
    pending.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    pending.replace(path)


def private_root(supplied: Path, repository: Path) -> Path:
    """Refuse links, shared roots, repository trees and managed session state."""
    if not supplied.is_absolute() or ".." in supplied.parts:
        raise ProofError("results-root-not-absolute", "repo-config")
    for path in (supplied, *supplied.parents):
        info = path.lstat()
        if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
            raise ProofError("results-root-indirection", "repo-config")
        if (path / ".git").exists() or (path / ".git").is_file():
            raise ProofError("results-root-in-repository", "repo-config")
    root = supplied.resolve(strict=True)
    info = root.stat()
    if (info.st_uid != os.getuid() or info.st_mode & 0o077
            or root in (Path("/"), Path.home()) or repository == root
            or repository in root.parents or "session-state" in root.parts):
        raise ProofError("results-root-not-private", "repo-config")
    if os.getuid() == 0:
        raise ProofError("nonroot-host-required", "repo-config")
    return root


def fresh_run(root: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return Path(tempfile.mkdtemp(prefix=f"gateway-role-{stamp}-", dir=root))


def git(repository: Path, *args: str) -> bytes:
    env = {"PATH": os.environ.get("PATH", os.defpath), "HOME": str(repository),
           "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
           "GIT_TERMINAL_PROMPT": "0", "GIT_NO_REPLACE_OBJECTS": "1",
           "GIT_NO_LAZY_FETCH": "1", "GIT_ALLOW_PROTOCOL": ""}
    try:
        result = subprocess.run(
            ["git", "-C", str(repository), *args], capture_output=True, env=env,
            timeout=15, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise ProofError("git-source-unavailable", "repo-config") from None
    if result.returncode:
        raise ProofError("git-source-unavailable", "repo-config")
    return result.stdout


def materialize(repository: Path, ref: str, run: Path) -> dict:
    """Only known regular blobs, never the index, live source, HOME or Git archive."""
    if not ref or ref.startswith("-") or not re.fullmatch(r"[A-Za-z0-9_./-]+", ref):
        raise ProofError("invalid-source-ref", "repo-config")
    sha = git(repository, "rev-parse", "--verify", ref + "^{commit}").decode().strip()
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ProofError("invalid-source-sha", "repo-config")
    listing = git(repository, "ls-tree", "-rz", "--full-tree", sha)
    entries = {}
    for entry in listing.split(b"\0"):
        if not entry:
            continue
        metadata, path = entry.split(b"\t", 1)
        name = path.decode("utf-8")
        mode, kind, oid = metadata.decode().split()
        if (name.startswith("fleet-manager/src/") and name not in REQUIRED):
            raise ProofError("unknown-package-source", "repo-config")
        if name in REQUIRED:
            if mode not in ("100644", "100755") or kind != "blob":
                raise ProofError("source-not-regular-blob", "repo-config")
            entries[name] = oid
    if set(entries) != REQUIRED:
        raise ProofError("source-closure-unavailable", "repo-config")
    context, fixtures = run / "context", run / "proof"
    context.mkdir(mode=0o700)
    fixtures.mkdir(mode=0o700)
    hashes = {}
    for name in sorted(REQUIRED):
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts:
            raise ProofError("unsafe-source-path", "repo-config")
        data = git(repository, "cat-file", "blob", entries[name])
        if len(data) > 2 * 1024 * 1024:
            raise ProofError("source-blob-limit", "repo-config")
        hashes[name] = digest(data)
        if name.startswith(PROOF):
            # The executing harness must also be the pinned harness, not a live overlay.
            if digest((Path(__file__).parent / path.name).read_bytes()) != digest(data):
                raise ProofError("runner-source-mismatch", "repo-config")
            destination = fixtures / path.name
        else:
            destination = context / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
    manifest = {"source_commit": sha, "blobs": hashes}
    save(run / "source-manifest.json", manifest)
    return {"source_commit": sha,
            "source_manifest_sha256": digest((run / "source-manifest.json").read_bytes())}
