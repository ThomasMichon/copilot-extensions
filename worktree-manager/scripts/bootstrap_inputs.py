"""Retain the complete bootstrap/application closure before queue admission."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile

import installer_engine as engine
import versioned_runtime as runtime


def _preparation_asset_group(
    payload: Path, manifest_name: str, schema: str, canonical: str, destination: str,
    *, targets: set[Path] | None = None, directories: set[Path] | None = None,
) -> dict[Path, Path]:
    manifest = payload / manifest_name
    if not manifest.is_file():
        return {}
    declaration = json.loads(manifest.read_text(encoding="utf-8"))
    if (
        not isinstance(declaration, dict)
        or declaration.get("schema") != schema
        or type(declaration.get("version")) is not int
    ):
        raise ValueError("Unsupported Manager source preparation assets")
    legacy = declaration["version"] == 1
    if legacy:
        if (
            declaration.get("canonicalDir") != canonical
            or declaration.get("payloadDir") != destination
        ):
            raise ValueError("Unsupported Manager source preparation assets")
        groups = [declaration]
    elif (
        declaration["version"] == 2 and set(declaration) == {"schema", "version", "groups"}
        and isinstance(declaration["groups"], list) and 1 <= len(declaration["groups"]) <= 32
    ):
        groups = declaration["groups"]
    else:
        raise ValueError("Unsupported Manager source preparation assets")

    def relative(value):
        if (
            not isinstance(value, str) or not value or "\\" in value or ":" in value
            or any(part in {"", ".", ".."} for part in value.split("/"))
        ):
            raise ValueError("Invalid Manager source preparation asset name")
        return Path(value)

    assets = {}
    targets = set() if targets is None else targets
    directories = set() if directories is None else directories
    for group in groups:
        if not isinstance(group, dict) or not isinstance(group.get("files"), list) or not group["files"]:
            raise ValueError("Unsupported Manager source preparation assets")
        canonical_directory = relative(group.get("canonicalDir"))
        destination_directory = relative(group.get("payloadDir"))
        for name in group["files"]:
            member = relative(name)
            if legacy and member.name != name:
                raise ValueError("Invalid Manager source preparation asset name")
            target = destination_directory / member
            if target in targets or target in directories or any(parent in targets for parent in target.parents):
                raise ValueError("Duplicate Manager source preparation asset name or overlapping targets")
            targets.add(target)
            directories.update(target.parents)
            if (payload / target).is_file():
                continue
            source = payload.parent / canonical_directory / member
            if source.resolve() != source or not source.is_file():
                raise ValueError(f"Manager source preparation asset is unavailable: {name}")
            assets[target] = source
    return assets


def _preparation_assets(payload: Path) -> dict[Path, Path]:
    targets: set[Path] = set()
    directories: set[Path] = set()
    return {
        **_preparation_asset_group(
            payload, "packaged-assets.json", "copilot-extensions.packaged-assets",
            "tools", "scripts/runtime-source-tools",
            targets=targets, directories=directories,
        ),
        **_preparation_asset_group(
            payload, "launch-wrapper-assets.json", "copilot-extensions.launch-wrapper-assets",
            "libs/payload-invocation", "scripts/libs/payload-invocation",
            targets=targets, directories=directories,
        ),
    }


def retain(
    payload: Path, execution_root: Path, *, include_sdk: bool = True,
    expected_payload_digest: str | None = None,
) -> tuple[Path, bool]:
    from worktree_manager.runtime_source import _git
    from worktree_manager.runtime_source_config import configured_source
    from update_driver.core import create_queue_directory

    payload = payload.resolve(strict=True)
    source = payload.parent
    sdk = source / "plugins" / "agent-worktrees"
    applications = (payload, sdk) if include_sdk else (payload,)
    roots = tuple(sorted({
        package for application in applications for package in engine._package_roots(application)
    }))
    common = Path(os.path.commonpath(roots))
    digest = runtime.fingerprint_source(engine._source_files(roots))
    assets = _preparation_assets(payload)
    if expected_payload_digest is not None and runtime.fingerprint_source(
        engine._source_files(engine._package_roots(payload)),
    ) != expected_payload_digest:
        raise RuntimeError("Manager installation source changed before retention")
    asset_bytes: dict[Path, bytes] = {}
    asset_digest = runtime.fingerprint_source(
        tuple(assets.values()), on_file=lambda path, data: asset_bytes.__setitem__(path, data),
    ) if assets else None
    revision = _git(source, "rev-parse", "--verify", "HEAD") if (source / ".git").is_dir() else None
    share = include_sdk and revision is not None and configured_source().channel == "marketplace"
    key = hashlib.sha256(json.dumps(
        {"inputs": digest, "assets": asset_digest, "revision": revision},
        sort_keys=True,
    ).encode()).hexdigest()
    directory = execution_root / "bootstrap-inputs"
    if directory.resolve() != directory:
        raise RuntimeError("Bootstrap inputs must not traverse an indirect path")
    create_queue_directory(execution_root)
    create_queue_directory(directory)
    destination = directory / key
    relative = tuple(application.relative_to(common) for application in applications)

    def verify_retained(root: Path) -> tuple[Path, ...]:
        retained = tuple(root / path for path in relative)
        retained_roots = tuple(sorted({
            package for application in retained for package in engine._package_roots(application)
        }))
        native_roots = tuple(Path(engine._native_retained_path(root)) for root in retained_roots)
        added = {Path(engine._native_retained_path(retained[0] / path)) for path in assets}
        if runtime.fingerprint_source(tuple(
            path for path in engine._source_files(native_roots) if path not in added
        )) != digest:
            raise RuntimeError("Retained bootstrap inputs differ from their addressed source")
        for relative_asset, source_asset in assets.items():
            asset = Path(engine._native_retained_path(retained[0] / relative_asset))
            if asset.read_bytes() != asset_bytes[source_asset]:
                raise RuntimeError("Retained source preparation assets differ from their addressed source")
        return retained

    def verify_prepared(root: Path) -> None:
        if runtime.fingerprint_source(engine._source_files(roots)) != digest:
            raise RuntimeError("Bootstrap source changed during retention")
        if assets and runtime.fingerprint_source(tuple(assets.values())) != asset_digest:
            raise RuntimeError("Manager source preparation assets changed during retention")
        if revision is not None and _git(source, "rev-parse", "--verify", "HEAD") != revision:
            raise RuntimeError("Bootstrap revision changed during retention")
        verify_retained(root)

    with runtime.construction_lease(directory, key):
        if destination.resolve() != destination:
            raise RuntimeError("Bootstrap snapshot must not be indirect")
        if not destination.exists():
            with tempfile.TemporaryDirectory(
                prefix=".prepare-", dir=engine._native_retained_path(directory),
            ) as temporary:
                prepared = Path(runtime._normalize_windows_extended_path(temporary)) / "inputs"
                frozen = engine.freeze_inputs(
                    payload, prepared, additional_plugins=(sdk,) if include_sdk else (),
                )
                if frozen.payload_hash != digest:
                    raise RuntimeError("Bootstrap source changed before retention")
                for relative_asset, source_asset in assets.items():
                    target = frozen.plugin / relative_asset
                    Path(engine._native_retained_path(target.parent)).mkdir(parents=True, exist_ok=True)
                    Path(engine._native_retained_path(target)).write_bytes(asset_bytes[source_asset])
                runtime._runtime_component("versioned_runtime_construction").publish_prepared_snapshot(
                    Path(engine._native_retained_path(prepared)),
                    Path(engine._native_retained_path(destination)),
                    runtime=runtime, verify=verify_prepared,
                )
        if destination.resolve() != destination:
            raise RuntimeError("Bootstrap snapshot must not be indirect")
        retained = verify_retained(destination)
    return retained[0], share
