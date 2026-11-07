"""Retain the complete bootstrap/application closure before queue admission."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import tempfile

import installer_engine as engine
import versioned_runtime as runtime


def _preparation_assets(payload: Path) -> dict[Path, Path]:
    manifest = payload / "packaged-assets.json"
    if not manifest.is_file():
        return {}
    declaration = json.loads(manifest.read_text(encoding="utf-8"))
    if (
        not isinstance(declaration, dict)
        or declaration.get("schema") != "copilot-extensions.packaged-assets"
        or type(declaration.get("version")) is not int
        or (declaration["version"], declaration.get("canonicalDir")) not in ((1, "tools"), (2, "."))
        or declaration.get("payloadDir") != "scripts/runtime-source-tools"
        or not isinstance(declaration.get("files"), list) or not declaration["files"]
    ):
        raise ValueError("Unsupported Manager source preparation assets")
    assets = {}
    names = set()
    for name in declaration["files"]:
        if (
            not isinstance(name, str) or not name or Path(name).is_absolute()
            or PureWindowsPath(name).drive
            or any(part in {"", ".", ".."} for part in name.split("/"))
        ):
            raise ValueError("Invalid Manager source preparation asset name")
        if "\\" in name or ":" in name or declaration["version"] == 1 and "/" in name:
            raise ValueError("Invalid Manager source preparation asset name")
        key = name.casefold() if declaration["version"] == 2 else name
        if key in names:
            raise ValueError("Duplicate Manager source preparation asset name")
        names.add(key)
        target = Path(declaration["payloadDir"]) / name
        if (payload / target).is_file():
            continue
        source = payload.parent / declaration["canonicalDir"] / name
        if source.resolve() != source or not source.is_file():
            raise ValueError(f"Manager source preparation asset is unavailable: {name}")
        assets[target] = source
    return assets


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
                for relative_asset, source_asset in assets.items():
                    target = frozen.plugin / relative_asset
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(asset_bytes[source_asset])
                if assets and runtime.fingerprint_source(tuple(assets.values())) != asset_digest:
                    raise RuntimeError("Manager source preparation assets changed during retention")
                if revision is not None and _git(source, "rev-parse", "--verify", "HEAD") != revision:
                    raise RuntimeError("Bootstrap revision changed during retention")
                prepared.rename(destination)
        if destination.resolve() != destination:
            raise RuntimeError("Bootstrap snapshot must not be indirect")
        retained = tuple(destination / path for path in relative)
        retained_roots = tuple(sorted({
            package for application in retained for package in engine._package_roots(application)
        }))
        added = {retained[0] / path for path in assets}
        if runtime.fingerprint_source(tuple(
            path for path in engine._source_files(retained_roots) if path not in added
        )) != digest:
            raise RuntimeError("Retained bootstrap inputs differ from their addressed source")
        for relative_asset, source_asset in assets.items():
            if (retained[0] / relative_asset).read_bytes() != asset_bytes[source_asset]:
                raise RuntimeError("Retained source preparation assets differ from their addressed source")
    return retained[0], share
