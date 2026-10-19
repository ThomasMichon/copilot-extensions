"""Prepare immutable installer input using the canonical release materializer."""

from __future__ import annotations

import importlib.util
import os
import shutil
import sys
import tempfile
from pathlib import Path

PLUGIN = "agent-pull-requests"
LIBRARIES = ("agent-procutil", "single-instance-lease", "work-coalescing-singleton", "zdd")


def _ignore(_directory, names):
    return {
        name for name in names
        if name in {".git", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache", "build", "dist"}
        or name.endswith((".pyc", ".pyo"))
    }


def materialize(payload: Path, destination: Path, api) -> None:
    """A source checkout uses its release producer; published payloads are complete."""
    api._snapshot_content_sha256(payload)
    shutil.copytree(payload, destination, ignore=_ignore, symlinks=True)
    repository = payload.parent.parent
    producer = repository / "tools" / "materialize_main.py"
    if (
        payload == repository / "plugins" / PLUGIN
        and producer.is_file()
        and not api._is_link_or_junction(producer)
    ):
        before = list(sys.path)
        try:
            sys.path.insert(0, str(producer.parent))
            spec = importlib.util.spec_from_file_location("apr_release_materializer", producer)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            results = module.materialize_file_pointers(destination, canonical_root=repository)
            for name in (
                "materialize_uv_editable_ref_into", "materialize_installer_engine_ref_into",
                "materialize_launch_wrapper_assets_into",
            ):
                results.extend(getattr(module, name)(
                    source_consumer_dir=payload, dest_consumer_dir=destination,
                    canonical_root=repository,
                ))
            if any(line.startswith("SKIP ") for line in results):
                raise ValueError("Canonical release materialization refused installer input")
        finally:
            sys.path[:] = before
    for name in LIBRARIES:
        if not (destination / "libs" / name / "pyproject.toml").is_file():
            raise ValueError(f"Prepared payload lacks its materialized {name} library")
    for suffix in ("ps1", "sh"):
        if not (destination / "scripts" / f"installer-engine.{suffix}").is_file():
            raise ValueError("Prepared payload lacks its materialized installer engine")
    api._snapshot_content_sha256(destination)


def prepare(*, payload: Path, version: str, api, common: dict) -> dict:
    """Never overwrite an existing snapshot, including incomplete prior production."""
    resolved = api.resolve_context(
        **common, expected_payload_root=str(payload), environment={},
    )
    identity = api._payload_identity_from_install(resolved["installReceipt"])
    if identity["version"] != version:
        raise PermissionError("Management package version differs from its installation receipt")
    snapshots = Path(resolved["snapshotsRoot"])
    if api._is_link_or_junction(snapshots):
        raise PermissionError("Snapshot preparation root may not be linked")
    snapshots.mkdir(parents=True, exist_ok=True)
    if not api.paths_equal(snapshots, api.canonical_path(snapshots)):
        raise PermissionError("Snapshot preparation root changed")
    staging = Path(tempfile.mkdtemp(prefix=".prepare-", dir=snapshots))
    try:
        source_digest = api._snapshot_content_sha256(payload)
        candidate = staging / "payload"
        materialize(payload, candidate, api)
        if api._snapshot_content_sha256(payload) != source_digest:
            raise PermissionError("Management payload changed during snapshot preparation")
        digest = api._snapshot_content_sha256(candidate)
        snapshot_id = f"{version}-{digest}"
        api._assert_snapshot_id(snapshot_id)
        target = snapshots / snapshot_id
        fresh = api.resolve_context(
            **common, expected_payload_root=str(payload), environment={},
        )
        if fresh != resolved:
            raise PermissionError("Installation receipts changed during snapshot preparation")
        if os.path.lexists(target):
            result = api.validate_snapshot_provenance(
                **common, snapshot_id=snapshot_id, expected_payload_root=str(payload),
                expected_payload_version=version, environment={},
            )
            if api._snapshot_content_sha256(target) != digest:
                raise PermissionError("Existing immutable snapshot differs from prepared content")
            return result
        # Atomic mkdir owns production; provenance is the publication point.
        # Never expose a completed snapshot before its final bytes are checked.
        target.mkdir()
        shutil.copytree(candidate, target, dirs_exist_ok=True, symlinks=True)
        if api._snapshot_content_sha256(target) != digest:
            raise PermissionError("Snapshot content changed before provenance publication")
        return api.stamp_snapshot_provenance(
            **common, snapshot_id=snapshot_id,
            expected_namespace_generation=resolved["namespaceGeneration"],
            expected_install_generation=resolved["generation"],
        )
    finally:
        # Only this invocation's unexposed staging tree is disposable. An
        # interrupted published candidate remains explicit repair custody.
        shutil.rmtree(staging)
