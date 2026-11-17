"""Read-only source identity and selected-profile reuse for Manager installation."""

from __future__ import annotations

import json
import re
import shutil
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

import installer_engine as engine
import versioned_runtime as runtime
import versioned_runtime_source as runtime_source


@contextmanager
def selection_workspace(inputs: Path):
    workspace = Path(tempfile.mkdtemp(prefix="manager-selection-", dir=inputs))
    try:
        yield workspace
    except BaseException:
        print(f"Manager selection evidence retained: {workspace}", file=sys.stderr, flush=True)
        raise
    else:
        shutil.rmtree(workspace)


def selector_text(data: bytes) -> str | None:
    try:
        return data.decode("utf-8").strip() or None
    except UnicodeError:
        return None


def source_digest(payload: Path) -> str:
    return runtime.fingerprint_source(engine._source_files(engine._package_roots(payload)))


@contextmanager
def prepared_manager_inputs(snapshot: Path):
    """Retain the complete source/profile through the existing Manager lifecycle."""
    from install_source import _member, manager_inputs

    with manager_inputs(snapshot) as (selected, wheels):
        installed = {row[0] for row in wheels.distributions}
        local = tuple(sorted(
            engine._package_name(path) for path in engine._package_roots(selected.payload)
            if engine._package_name(path) in installed
        ))
        options = {"wheel_inputs": wheels, "local_packages": local}
        if "application" in selected.project:
            options["application"] = _member(snapshot, selected.project["application"])
        yield selected, options


def base_python() -> Path:
    return Path(getattr(sys, "_base_executable", sys.executable)).resolve(strict=True)


def selected_version(root: Path) -> str | None:
    """Read ordinary old selection, never follow an unsafe historical name."""
    try:
        selected = (root / "current-version").read_text(encoding="utf-8").strip()
        runtime._validate_dev_previous_version(selected)
    except (OSError, ValueError):
        return None
    slot = runtime.version_dir(root, selected)
    return selected if not runtime._is_link(slot) and slot.is_dir() else None


def source_install_selection(
    payload: Path, version: str, selected: Path | None,
    *, wheel_inputs=None, local_packages: tuple[str, ...] | None = None,
) -> tuple[str, bool]:
    """Resolve requested source identity without constructing or publishing."""
    identity = runtime_source.read_source_identity(payload, version, plugin="worktree-manager")
    if wheel_inputs is not None:
        if identity is None:
            raise ValueError("Manager source wheels require an attributed source identity")
        wheel_inputs.verify()
        receipt = wheel_inputs.receipt()
        generation = identity.runtime_version + ".profile." + receipt["lock_id"][7:23]
        reused = _source_profile_reuse(payload, identity, selected)
        return generation, (
            reused is not None and reused[0].receipt() == receipt
            and reused[1] == tuple(sorted(local_packages or ()))
        )
    if identity is None:
        return version, True
    if _source_profile_reuse(payload, identity, selected) is not None:
        return identity.runtime_version, True
    if selected is None or not (
        selected.name == identity.runtime_version
        or selected.name.startswith(identity.runtime_version + ".repair.")
    ):
        return identity.runtime_version, False
    matches = not runtime._is_link(selected) and runtime.is_complete(selected.parent.parent, selected.name)
    if matches:
        try:
            matches = runtime_source.read_source_identity(
                selected / "payload", version, plugin="worktree-manager",
            ) == identity
        except (OSError, ValueError, TypeError):
            matches = False
    return identity.runtime_version, matches


def _source_profile_reuse(payload: Path, identity, selected: Path | None):
    """Qualify an existing closed-wheel generation, never construct from a receipt."""
    if selected is None or re.fullmatch(
        re.escape(identity.runtime_version) + r"\.profile\.[0-9a-f]{16}(?:\.repair\.[0-9a-f]{32})?",
        selected.name,
    ) is None:
        return None
    root = selected.parent.parent
    if runtime._is_link(selected) or not runtime.is_complete(root, selected.name):
        return None
    packages = engine._package_roots(payload)
    package_names = tuple(engine._package_name(package) for package in packages)
    source_files = engine._source_files(packages)
    reader = runtime._runtime_component("versioned_runtime_construction")
    try:
        if runtime_source.read_source_identity(
            selected / "payload", identity.package_version, plugin="worktree-manager",
        ) != identity:
            return None
        record = reader.read_completion(
            selected / ".runtime-input-origins.json",
            object_pairs_hook=runtime._unique_object_pairs_hook,
        )
        if not isinstance(record, dict) or record.get("schema") != "installer-engine.frozen-input-origins":
            raise ValueError("source profile has no constructor origins")
        if type(record.get("version")) is not int or record["version"] != 2:
            raise ValueError("source profile has unsupported constructor origins")
        reuse = engine.WheelReuseInputs(
            selected.name,
            json.dumps(record["wheel_inputs"], sort_keys=True, separators=(",", ":"), allow_nan=False),
            time.monotonic() + 30,
        )
        receipt = reuse.receipt()
        profile = receipt["binding"]
        if (
            profile.get("component") != "worktree-manager" or profile.get("mode") != "source"
            or profile.get("release") != {
                "identity": "source:" + identity.source_commit,
                "sha256": "sha256:" + identity.assembly_hash,
            }
            or re.fullmatch(
                re.escape(identity.runtime_version + ".profile." + receipt["lock_id"][7:23])
                + r"(?:\.repair\.[0-9a-f]{32})?", selected.name,
            ) is None
        ):
            raise ValueError("source profile differs from its selected source/generation")
        expected_local = tuple(sorted(
            name for name in package_names if name in receipt["distributions"]
        ))
        if record.get("local_packages") != list(expected_local):
            raise ValueError("source profile differs from its local package selection")
    except (OSError, ValueError, TypeError, KeyError):
        return None
    construction = engine._wheel_construction_hash(
        engine._construction_hash(source_files, (), expected_local), reuse,
    )
    if not runtime.is_complete(root, selected.name, expect_hash=construction):
        return None
    return reuse, expected_local
