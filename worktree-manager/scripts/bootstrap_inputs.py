"""Retain the complete bootstrap/application closure before queue admission."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
import uuid

import installer_engine as engine
import versioned_runtime as runtime


def retain(
    payload: Path, execution_root: Path, *, include_sdk: bool = True,
) -> tuple[Path, bool]:
    from worktree_manager.runtime_source import _git
    from worktree_manager.runtime_source_config import configured_source

    payload = payload.resolve(strict=True)
    source = payload.parent
    sdk = source / "plugins" / "agent-worktrees"
    applications = (payload, sdk) if include_sdk else (payload,)
    roots = tuple(sorted({
        package for application in applications for package in engine._package_roots(application)
    }))
    common = Path(os.path.commonpath(roots))
    digest = runtime.fingerprint_source(engine._source_files(roots))
    revision = _git(source, "rev-parse", "--verify", "HEAD") if (source / ".git").is_dir() else None
    share = include_sdk and revision is not None and configured_source().channel == "marketplace"
    key = hashlib.sha256(json.dumps(
        {"inputs": digest, "revision": revision, "unshared": None if share else uuid.uuid4().hex},
        sort_keys=True,
    ).encode()).hexdigest()
    directory = execution_root / "bootstrap-inputs"
    if directory.resolve() != directory:
        raise RuntimeError("Bootstrap inputs must not traverse an indirect path")
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    destination = directory / key
    relative = tuple(application.relative_to(common) for application in applications)
    with runtime.construction_lease(directory, key):
        if destination.resolve() != destination:
            raise RuntimeError("Bootstrap snapshot must not be indirect")
        if not destination.exists():
            with tempfile.TemporaryDirectory(prefix=".prepare-", dir=directory) as temporary:
                prepared = Path(temporary) / "inputs"
                frozen = engine.freeze_inputs(
                    payload, prepared, additional_plugins=(sdk,) if include_sdk else (),
                )
                if frozen.payload_hash != digest:
                    raise RuntimeError("Bootstrap source changed before retention")
                if revision is not None and _git(source, "rev-parse", "--verify", "HEAD") != revision:
                    raise RuntimeError("Bootstrap revision changed during retention")
                prepared.rename(destination)
        if destination.resolve() != destination:
            raise RuntimeError("Bootstrap snapshot must not be indirect")
        retained = tuple(destination / path for path in relative)
        retained_roots = tuple(sorted({
            package for application in retained for package in engine._package_roots(application)
        }))
        if runtime.fingerprint_source(engine._source_files(retained_roots)) != digest:
            raise RuntimeError("Retained bootstrap inputs differ from their addressed source")
    return retained[0], share
