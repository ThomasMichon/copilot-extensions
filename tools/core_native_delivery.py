"""Prepare a source-bound native core delivery candidate, without approval."""
from __future__ import annotations

import argparse
from contextlib import nullcontext
from dataclasses import dataclass
import importlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "libs" / "installer-engine"))

import bundle_lock
import local_install_profile as native
import local_install_source as source_owner
from core_runtime_profile import _core_manifest, _read, build_core_profile, core_roots
from installer_wheel_acquisition import AcquisitionLimits, acquire_wheel_inputs
from installer_wheel_inputs import _check_deadline, _ordinary
from installer_wheel_snapshot import _digest, _open, _unchanged, retain_wheel_inputs
from packaging.tags import sys_tags
from packaging.utils import canonicalize_name, parse_wheel_filename
from promotion_python_artifacts import inspect_wheel, validate_source_binding
from runtime_profile import ProfileError, _target
from runtime_profile_bundle import build_profile_bundle
from runtime_profile_delivery import _held, _json, _material_files, _read_json, build_profile_delivery
from runtime_source_lock import _wheel_pins
from standalone_python_artifacts import COMPONENTS


@dataclass(frozen=True)
class _CoreIdentity:
    source_commit: str
    source_tree: str
    main_head: str
    assembly_hash: str
    artifact_id: str
    plugin: str = "composition-core"

    def document(self) -> dict:
        return {
            "source_commit": self.source_commit, "source_tree": self.source_tree,
            "main_head": self.main_head, "core_payload_sha256": self.assembly_hash,
            "core_artifact_id": self.artifact_id,
        }


@dataclass(frozen=True)
class _CoreSelection:
    identity: _CoreIdentity


def project_core_inputs(
    source: Path, manifest_data: bytes, target: dict, *, deadline: float,
) -> tuple[list[str], list[str]]:
    """Bind the existing target projection to the core's exact source selection."""
    _check_deadline(deadline)
    _target(target, 10_000, deadline)
    manifest = _core_manifest(manifest_data)
    validate_source_binding(manifest, source)
    try:
        groups, _ranges = bundle_lock.collect_inputs(
            source, groups=("core",), target_environment=target["environment"],
        )
    except bundle_lock.InputError as error:
        raise ProfileError(f"native core input projection refused: {error}") from error
    versions = {
        canonicalize_name(row["name"]): str(parse_wheel_filename(row["filename"])[1])
        for row in manifest["wheels"]
    }
    _check_deadline(deadline)
    return core_roots(manifest, versions), groups["core"]


def _read_lock(path: Path, deadline: float) -> bytes:
    before = _ordinary(path)
    if before.st_size > 4 << 20:
        raise ProfileError("native core lock exceeds its document budget")
    with _open(path) as stream:
        opened = _unchanged(path, before, stream)
        content = stream.read((4 << 20) + 1)
        if len(content) > 4 << 20:
            raise ProfileError("native core lock exceeds its document budget")
        _unchanged(path, before, stream, opened=opened)
    _check_deadline(deadline)
    return content


def _producer_procutil():
    return native.source_inputs.engine._procutil(
        native.source_inputs.engine.FrozenInputs(
            ROOT, (ROOT / "libs" / "agent-procutil",), "",
        ),
    )


def _run_compile(transaction, *, deadline: float) -> None:
    _check_deadline(deadline)
    try:
        result = subprocess.run(
            list(transaction.argv), cwd=transaction.cwd, env=transaction.environment,
            capture_output=True, timeout=max(0.1, deadline - time.monotonic()),
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except subprocess.TimeoutExpired:
        raise ProfileError("native core dependency resolution timed out") from None
    if result.returncode:
        procutil = _producer_procutil()
        diagnostics = importlib.import_module(procutil.__name__ + ".diagnostics")
        detail = diagnostics.safe_diagnostic_text(
            result.stderr.decode("utf-8", errors="replace"), 400, transaction.environment,
        )
        detail = re.sub(r"(?i)\b(?:https?|file)://\S+", "[URL]", detail)
        raise ProfileError(
            f"native core dependency resolution failed (exit {result.returncode}): "
            + (detail or "no diagnostic returned"),
        )
    _check_deadline(deadline)


def _inspect_first_party(snapshot, manifest: dict, source: Path, *, limits, deadline: float) -> None:
    for row in manifest["wheels"]:
        _check_deadline(deadline)
        component = next(
            (name for name, relative in COMPONENTS.items() if relative == row["source"]),
            None,
        ) if row["role"] == "standalone" else None
        inspect_wheel(
            snapshot.archives[row["filename"]], row, expected_generator=row["generator"],
            source_dir=source / row["source"], candidate_root=source, component=component,
            limits=limits, deadline=deadline,
        )
    snapshot.verify()


def _build_from_snapshot(
    source: Path, source_record: dict, manifest_data: bytes, wheel_dir: Path,
    target: dict, *, uv: str, download_origins: tuple[str, ...], materials: dict,
    material_paths: dict, destination: Path, deadline: float, max_bytes: int,
) -> dict:
    """Keep actual compiler, wheel and material custody until candidate publication."""
    if type(max_bytes) is not int or max_bytes <= 0:
        raise ProfileError("core delivery requires a positive byte budget")
    if (
        not isinstance(materials, dict) or not isinstance(material_paths, dict)
        or any(not isinstance(name, str) for name in materials)
        or materials.keys() != material_paths.keys()
    ):
        raise ProfileError("core delivery materials and paths must match")
    try:
        materials = json.loads(_json(materials))
    except (TypeError, ValueError) as error:
        raise ProfileError("core delivery materials must be JSON values") from error
    material_paths = dict(material_paths)
    material_files = _material_files(materials, 256, max_bytes, deadline)
    destination = Path(os.path.abspath(destination))
    if os.path.lexists(destination):
        raise FileExistsError("core delivery output already exists")
    manifest = _core_manifest(manifest_data)
    roots, requirements = project_core_inputs(source, manifest_data, target, deadline=deadline)
    identity = _CoreIdentity(
        source_record["source_commit"], source_record["source_tree"], source_record["main_head"],
        manifest["payload_hash"].removeprefix("sha256:"), manifest["artifact_id"],
    )
    selected = _CoreSelection(identity)
    first_paths = {row["filename"]: wheel_dir / row["filename"] for row in manifest["wheels"]}
    first_digests = {row["filename"]: row["sha256"] for row in manifest["wheels"]}
    if len(first_paths) != len(manifest["wheels"]):
        raise ProfileError("core first-party wheel filenames collide")
    first_names = {canonicalize_name(row["name"]) for row in manifest["wheels"]}
    limits = native.wheel_limits()
    with native.dependencies._temporary_inputs(destination.parent) as temporary:
        work = native.dependencies._private_retained_root(Path(temporary)).resolve()
        requirement_file = work / "registry.in"
        requirement_file.write_text("\n".join(requirements) + "\n", encoding="utf-8")
        lock_file = work / "pylock.toml"
        candidate = work / "delivery.zip"
        with _held(material_paths, material_files, deadline), retain_wheel_inputs(
            first_paths, first_digests, max_wheels=256,
            max_archive_bytes=limits.max_archive_bytes, max_total_bytes=max_bytes,
            max_snapshot_bytes=4 * max_bytes, deadline=deadline,
        ) as first:
            _inspect_first_party(first, manifest, source, limits=limits, deadline=deadline)
            projections = []
            with native.dependencies.retain_uv_compile(
                uv, [
                    str(requirement_file), "--python", sys.executable,
                    "--python-version", target["environment"]["python_full_version"],
                    "--only-binary", ":all:", "--no-python-downloads",
                    "--system-certs", "--format", "pylock.toml", "--output-file", str(lock_file),
                ], cwd=work, env=dict(os.environ), deadline=deadline, native_target=True,
                _projection=projections,
            ) as transaction:
                initial_policy, initial_feed = native._profile_feed(
                    selected, transaction, projections, uv=uv,
                    download_origins=download_origins, wheels=None, deadline=deadline,
                )
                _run_compile(transaction, deadline=deadline)
                lock_bytes = _read_lock(lock_file, deadline)
                files = _wheel_pins(lock_bytes, target, deadline, prefer_target_tags=True)
                if first_names & {item["name"] for item in files}:
                    raise ProfileError("native core lock overlaps its first-party wheel set")
                policy, feed = native._profile_feed(
                    selected, transaction, projections, uv=uv,
                    download_origins=download_origins, wheels=files, deadline=deadline,
                )
                if policy != initial_policy or feed.policy_id != initial_feed.policy_id:
                    raise ProfileError("native core feed identity changed during resolution")
                acquisition = acquire_wheel_inputs(
                    files, feeds=feed.feeds, download_origins=feed.download_origins,
                    cutoffs=feed.cutoffs, policy_id=feed.policy_id,
                    limits=AcquisitionLimits(
                        max_packages=256, max_requests=2048, max_redirects=128,
                        max_candidates_per_project=100_000, max_index_bytes=16 << 20,
                        max_total_metadata_bytes=128 << 20, max_wheel_bytes=128 << 20,
                        max_closure_bytes=max_bytes, max_temporary_bytes=2 * max_bytes,
                    ),
                    deadline=deadline, ssl_context=feed.context, proxies=feed.proxies,
                    procutil=_producer_procutil(),
                ) if files else nullcontext(None)
                with acquisition as acquired:
                    paths = dict(first.paths)
                    expected = dict(first_digests)
                    if acquired is not None:
                        if paths.keys() & acquired.snapshot.paths.keys():
                            raise ProfileError("core and native wheel filenames collide")
                        paths.update(acquired.snapshot.paths)
                        expected.update({item["filename"]: item["sha256"] for item in files})
                    with retain_wheel_inputs(
                        paths, expected, max_wheels=256,
                        max_archive_bytes=limits.max_archive_bytes, max_total_bytes=max_bytes,
                        max_snapshot_bytes=4 * max_bytes, deadline=deadline,
                    ) as retained:
                        native_profile = native.complete_profile(
                            selected, target, roots, retained, policy_id=policy["policyId"],
                            acquisition_id=feed.policy_id, deadline=deadline,
                        )
                        selected_paths = {
                            row["filename"]: retained.paths[row["filename"]]
                            for row in native_profile.record()["wheels"]
                        }
                        profile = build_core_profile(
                            manifest_data=manifest_data, native_record=native_profile.record(),
                            archives=selected_paths, limits=limits, max_wheels=256,
                            max_total_bytes=max_bytes, max_snapshot_bytes=4 * max_bytes,
                            max_unpacked_bytes=limits.max_expanded_bytes, deadline=deadline,
                        )
                        transport = work / "wheels.zip"
                        wheel_result = build_profile_bundle(
                            profile.record(), selected_paths, transport, limits=limits,
                            max_wheels=256, max_total_bytes=max_bytes,
                            max_snapshot_bytes=4 * max_bytes,
                            max_unpacked_bytes=limits.max_expanded_bytes,
                            max_bundle_bytes=max_bytes, deadline=deadline,
                        )
                        result = build_profile_delivery(
                            profile.record(), transport, wheel_result["sha256"],
                            materials, material_paths, candidate, max_bytes=max_bytes,
                            max_files=256, max_snapshot_bytes=8 * max_bytes, deadline=deadline,
                        )
                        retained.verify()
                    if acquired is not None:
                        acquired.snapshot.verify()
                if _read_lock(lock_file, deadline) != lock_bytes:
                    raise ProfileError("native core lock changed during construction")
                if project_core_inputs(source, manifest_data, target, deadline=deadline) != (
                    roots, requirements,
                ):
                    raise ProfileError("core source projection changed during construction")
                first.verify()
        before = _ordinary(candidate)
        with _open(candidate) as retained:
            opened = _unchanged(candidate, before, retained)
            if _digest(retained, max_bytes, deadline) != (result["sha256"], result["size"]):
                raise ProfileError("core delivery changed after input verification")
            _unchanged(candidate, before, retained, opened=opened)
            _check_deadline(deadline)
            os.link(candidate, destination)
    return result | {"source_commit": identity.source_commit, "approval": "unattested"}


def prepare_core_delivery(
    repository: Path, commit: str, main_head: str, manifest: Path, wheel_dir: Path,
    materials: dict, material_paths: dict, destination: Path, *, uv: str,
    download_origins: tuple[str, ...], deadline: float, max_bytes: int,
) -> dict:
    """Compose the selected Main source and prepare one inert native candidate."""
    _check_deadline(deadline)
    if type(max_bytes) is not int or max_bytes <= 0:
        raise ProfileError("core delivery requires a positive byte budget")
    destination = Path(os.path.abspath(destination))
    if os.path.lexists(destination):
        raise FileExistsError("core delivery output already exists")
    manifest_data = _read(manifest)
    target = {
        "device": "cpu",
        "environment": native._query_marker_environment(Path(sys.executable), deadline=deadline),
        "tags": [str(tag) for tag in sys_tags()],
    }
    with native.dependencies._temporary_inputs(destination.parent) as temporary:
        work = native.dependencies._private_retained_root(Path(temporary)).resolve()
        snapshot = work / "source-snapshot"
        record = source_owner.compose(
            repository, commit, main_head, snapshot, finalized_main=True, deadline=deadline,
        )
        return _build_from_snapshot(
            snapshot / "source", record, manifest_data, wheel_dir, target, uv=uv,
            download_origins=download_origins, materials=materials, material_paths=material_paths,
            destination=destination, deadline=deadline, max_bytes=max_bytes,
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--source-checkout", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--main-head", required=True)
    parser.add_argument("--core-manifest", type=Path, required=True)
    parser.add_argument("--wheel-dir", type=Path, required=True)
    parser.add_argument("--materials", type=Path, required=True)
    parser.add_argument("--material-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--uv", required=True)
    parser.add_argument("--download-origin", action="append", default=[])
    parser.add_argument("--timeout", type=float, default=600)
    parser.add_argument("--max-bytes", type=int, default=512 << 20)
    args = parser.parse_args(argv)
    deadline = time.monotonic() + args.timeout
    try:
        materials = _read_json(args.materials, deadline)
        if not isinstance(materials, dict) or any(not isinstance(name, str) for name in materials):
            raise ProfileError("core delivery materials must be an object with named entries")
        result = prepare_core_delivery(
            args.source_checkout, args.source_commit, args.main_head, args.core_manifest,
            args.wheel_dir, materials, {name: args.material_dir / name for name in materials},
            args.output, uv=args.uv, download_origins=tuple(args.download_origin),
            deadline=deadline, max_bytes=args.max_bytes,
        )
        print(json.dumps(result, sort_keys=True))
        return 0
    except (OSError, ValueError, RuntimeError) as error:
        parser.exit(1, f"core delivery refused: {error}\n")


if __name__ == "__main__":
    raise SystemExit(main())
