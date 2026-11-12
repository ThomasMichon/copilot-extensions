"""Prepare a source-bound native core delivery candidate, without approval."""
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import re
import secrets
import subprocess
import sys
import time
from contextlib import ExitStack, nullcontext
from dataclasses import dataclass, replace
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "libs" / "installer-engine"))

import bundle_lock
import installer_native_feed as feed_owner
import local_install_profile as native
import local_install_source as source_owner
from artifact_attestation import INDEX_LIMIT
from core_native_feed_resolution import (
    compile_guarded,
    hold_compile_inputs,
    verify_guarded_cutoffs,
    verify_guarded_projects,
    verify_lock_witness,
)
from core_native_public_feed import public_feed_policy, require_public_feed
from core_runtime_profile import (
    _core_manifest,
    _read,
    build_core_profile,
    compose_core_profile_index,
    core_roots,
    validate_core_source_index,
)
from installer_engine_process import ChildLaunchError, ChildTimeoutExpired, run_owned
from installer_wheel_acquisition import (
    AcquisitionLimits,
    acquire_wheel_inputs,
    validate_acquisition_inputs,
)
from installer_wheel_inputs import _check_deadline, _ordinary
from installer_wheel_snapshot import _digest, _open, _unchanged, retain_wheel_inputs
from packaging.tags import sys_tags
from packaging.utils import canonicalize_name, parse_wheel_filename
from promotion_python_artifacts import inspect_wheel, validate_source_binding
from runtime_profile import ProfileError, _target
from runtime_profile_bundle import build_profile_bundle
from runtime_profile_delivery import (
    _held,
    _json,
    _material_files,
    _read_json,
    _unique,
    build_profile_delivery,
)
from runtime_profile_release import _unique_object
from runtime_profile_wheels import MANAGER_ISOLATED
from runtime_source_lock import _wheel_pins
from runtime_wheel_inspection import inspect_wheel as inspect_pool_wheel
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
    validate_source_binding(manifest, source, core_hash_workers=min(4, os.cpu_count() or 1))
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


def _read_document(path: Path, deadline: float, *, limit: int, label: str) -> bytes:
    before = _ordinary(path)
    if before.st_size > limit:
        raise ProfileError(f"{label} exceeds its document budget")
    with _open(path) as stream:
        opened = _unchanged(path, before, stream)
        content = stream.read(limit + 1)
        if len(content) > limit:
            raise ProfileError(f"{label} exceeds its document budget")
        _unchanged(path, before, stream, opened=opened)
    _check_deadline(deadline)
    return content


def _read_lock(path: Path, deadline: float) -> bytes:
    return _read_document(path, deadline, limit=4 << 20, label="native core lock")


def _producer_procutil():
    return native.source_inputs.engine._procutil(
        native.source_inputs.engine.FrozenInputs(
            ROOT, (ROOT / "libs" / "agent-procutil",), "",
        ),
    )


def _offline_pool_directory(pool, *, deadline: float) -> Path:
    _check_deadline(deadline)
    directories = {path.parent for path in pool.paths.values()}
    if len(directories) != 1:
        raise ProfileError("offline wheel pool directory is not a single held set")
    directory = directories.pop()
    expected = set(pool.paths)
    names = set()
    with os.scandir(directory) as entries:
        for entry in entries:
            _check_deadline(deadline)
            if entry.name not in expected:
                raise ProfileError("offline wheel pool directory differs from the held set")
            names.add(entry.name)
    if names != expected:
        raise ProfileError("offline wheel pool directory differs from the held set")
    _check_deadline(deadline)
    return directory


def _compile_offline_pool(transaction, pool, *, deadline: float) -> None:
    pool.verify()
    directory = _offline_pool_directory(pool, deadline=deadline)
    index = directory.parent / "pool-index.html"
    content = (
        "<!doctype html>\n"
        + "\n".join(
            f'<a href="{escape(native.dependencies._file_uri(path), quote=True)}">{escape(name)}</a>'
            for name, path in sorted(pool.paths.items())
        )
        + "\n"
    ).encode("ascii")
    with index.open("xb") as output:
        output.write(content)
    index.chmod(0o400)
    command = [
        *transaction.argv, "--offline", "--no-index", "--no-cache", "--quiet",
        "--find-links", native.dependencies._file_uri(index),
    ]
    _check_deadline(deadline)
    with _held(
        {"index": index},
        {"index": {"sha256": "sha256:" + hashlib.sha256(content).hexdigest(), "size": len(content)}},
        deadline,
    ):
        procutil = _producer_procutil()
        try:
            run_owned(
                command, cwd=transaction.cwd, env=transaction.environment,
                timeout=max(0.001, deadline - time.monotonic()), deadline=deadline,
                procutil=procutil, construction_fd=None,
            )
        except ChildTimeoutExpired:
            raise ProfileError("offline wheel-pool selection timed out") from None
        except subprocess.CalledProcessError as error:
            stderr = error.stderr
            if isinstance(stderr, bytes):
                stderr = stderr.decode("utf-8", errors="replace")
            diagnostics = importlib.import_module(procutil.__name__ + ".diagnostics")
            detail = diagnostics.safe_diagnostic_text(
                stderr if isinstance(stderr, str) else "", 65536, transaction.environment,
            )
            detail = re.sub(r"(?i)\b(?:https?|file)://.*", "[location]", detail)
            detail = re.sub(r"(?<!\w)(?:[A-Za-z]:[\\/]|\\\\|/(?!/)).*", "[location]", detail)
            detail = detail[:400] + ("..." if len(detail) > 400 else "")
            raise ProfileError(
                f"offline wheel-pool selection failed (exit {error.returncode}): "
                + (detail or "no diagnostic returned"),
            ) from None
        except ChildLaunchError:
            raise ProfileError("offline wheel-pool selection could not start") from None
        _check_deadline(deadline)
        _offline_pool_directory(pool, deadline=deadline)
        pool.verify()


def _inspect_offline_pool(pool, *, limits, deadline: float) -> None:
    remaining = limits.max_expanded_bytes
    for filename, archive in pool.archives.items():
        if remaining <= 0:
            raise ProfileError("offline wheel pool exceeds its inspection budget")
        _, size = inspect_pool_wheel(
            archive, {"filename": filename}, limits=limits, deadline=deadline,
            max_archive_bytes=limits.max_archive_bytes, max_unpacked_bytes=remaining,
            max_metadata_bytes=limits.max_metadata_member_bytes,
        )
        remaining -= size
    pool.verify()


def _guarded_feed_inputs(selected, transaction, projections, *, uv, download_origins,
                         exclusions, deadline):
    outer = feed_owner._projection(projections[1], "PipCompileSettings")
    settings = feed_owner._fields(feed_owner._body(outer["settings"], "PipSettings"))
    feed_owner._local_exclusions(outer.get("constraints", ""), transaction, [], exclusions)
    feed_owner._machine_constraints({**outer, "constraints": "[]"}, settings, transaction, None)
    names = {
        acquisition_name
        for option, value in native.dependencies._argument_pairs(transaction.argv[3:])
        if option == "--exclude-newer-package"
        for acquisition_name in (feed_owner._name(value.partition("=")[0]),)
    } - set(exclusions)
    if len(names) >= 256:
        raise ProfileError("guarded native feed has too many package cutoffs")
    sentinel = "guarded-cutoff-" + secrets.token_hex(16)
    while sentinel in names:
        sentinel = "guarded-cutoff-" + secrets.token_hex(16)
    policy, feed = native._profile_feed(
        selected, transaction, projections, uv=uv, download_origins=download_origins,
        wheels=[{"name": name} for name in sorted(names | {sentinel})],
        deadline=deadline, excluded_local_packages=exclusions,
    )
    return policy, feed, feed.cutoffs[sentinel], {name: feed.cutoffs[name] for name in names}


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
    native_lock: Path | None = None, offline_wheel_pool: Path | None = None,
    guarded_feed: bool = False, artifact_index: Path | None = None,
    nonsecret_feed_url: str | None = None, layout: str | None = None,
) -> dict:
    """Keep selected lock, wheel and material custody until candidate publication."""
    if type(max_bytes) is not int or max_bytes <= 0:
        raise ProfileError("core delivery requires a positive byte budget")
    if layout not in (None, MANAGER_ISOLATED):
        raise ProfileError("unsupported core delivery environment layout")
    if type(guarded_feed) is not bool or sum(
        (native_lock is not None, offline_wheel_pool is not None, guarded_feed),
    ) != 1:
        raise ProfileError("core delivery requires exactly one native selection mode")
    if nonsecret_feed_url is not None and (not guarded_feed or download_origins):
        raise ProfileError("public feed identity requires delegated guarded-feed selection")
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
    first_exclusions = tuple(sorted(first_names))
    held_paths = {"material:" + name: path for name, path in material_paths.items()}
    held_files = {"material:" + name: item for name, item in material_files.items()}
    index = None
    if artifact_index is not None:
        index_path = Path(artifact_index)
        index_data = _read_document(index_path, deadline, limit=INDEX_LIMIT, label="artifact index")
        index = json.loads(index_data, object_pairs_hook=_unique_object)
        state_path = source / ".github" / "release-pipeline-state.json"
        state_data = _read_document(state_path, deadline, limit=INDEX_LIMIT, label="release state")
        state = json.loads(state_data, object_pairs_hook=_unique_object)
        validate_core_source_index(index, state)
        for name, path, data in (
            ("artifact-index", index_path, index_data), ("release-state", state_path, state_data),
        ):
            held_paths[name] = path
            held_files[name] = {
                "sha256": "sha256:" + hashlib.sha256(data).hexdigest(), "size": len(data),
            }
    pool_paths, pool_digests = None, None
    if native_lock is not None:
        selected_lock = _read_lock(Path(native_lock), deadline)
        selected_files = _wheel_pins(selected_lock, target, deadline, prefer_target_tags=True)
        held_paths["selected-lock"] = Path(native_lock)
        held_files["selected-lock"] = {
            "sha256": "sha256:" + hashlib.sha256(selected_lock).hexdigest(),
            "size": len(selected_lock),
        }
    elif offline_wheel_pool is not None:
        pool_manifest = Path(source_owner.native_retained_path(offline_wheel_pool))
        pool_data = _read_lock(pool_manifest, deadline)
        pool_digests = json.loads(pool_data, object_pairs_hook=_unique)
        if not isinstance(pool_digests, dict) or not pool_digests:
            raise ProfileError("offline wheel pool requires a nonempty filename-to-digest object")
        pool_paths = {name: pool_manifest.parent / name for name in pool_digests}
        held_paths["offline-pool"] = pool_manifest
        held_files["offline-pool"] = {
            "sha256": "sha256:" + hashlib.sha256(pool_data).hexdigest(),
            "size": len(pool_data),
        }
        selected_files = None
    else:
        selected_files = None
    if selected_files is not None and first_names & {item["name"] for item in selected_files}:
        raise ProfileError("native core lock overlaps its first-party wheel set")
    limits = native.wheel_limits()
    acquisition_limits = AcquisitionLimits(
        max_packages=256, max_requests=2048, max_redirects=128,
        max_candidates_per_project=100_000, max_index_bytes=16 << 20,
        max_total_metadata_bytes=128 << 20, max_wheel_bytes=128 << 20,
        max_closure_bytes=max_bytes, max_temporary_bytes=2 * max_bytes,
    )
    verification_limits = None
    with native.dependencies._temporary_inputs(destination.parent) as temporary, ExitStack() as held_inputs:
        work = Path(source_owner.native_retained_path(
            native.dependencies._private_retained_root(Path(temporary)).resolve(),
        ))
        requirement_file = work / "registry.in"
        requirement_file.write_text("\n".join(requirements) + "\n", encoding="utf-8")
        lock_file = work / "pylock.toml"
        candidate = work / "delivery.zip"
        held_inputs.enter_context(_held(held_paths, held_files, deadline))
        with retain_wheel_inputs(
            first_paths, first_digests, max_wheels=256,
            max_archive_bytes=limits.max_archive_bytes, max_total_bytes=max_bytes,
            max_snapshot_bytes=4 * max_bytes, deadline=deadline,
        ) as first, ExitStack() as owners:
            _inspect_first_party(first, manifest, source, limits=limits, deadline=deadline)
            pool = owners.enter_context(retain_wheel_inputs(
                pool_paths, pool_digests, max_wheels=256,
                max_archive_bytes=limits.max_archive_bytes, max_total_bytes=max_bytes,
                max_snapshot_bytes=4 * max_bytes, deadline=deadline,
            )) if pool_paths is not None else None
            if pool is not None:
                _inspect_offline_pool(pool, limits=limits, deadline=deadline)
            projections = []
            with native.dependencies.retain_uv_compile(
                uv, [
                    str(requirement_file), "--python", sys.executable,
                    "--python-version", target["environment"]["python_full_version"],
                    "--only-binary", ":all:", "--no-python-downloads",
                    "--system-certs", "--format", "pylock.toml", "--output-file", str(lock_file),
                ], cwd=work, env=dict(os.environ), deadline=deadline, native_target=True,
                excluded_local_packages=first_exclusions, _projection=projections,
            ) as transaction, ExitStack() as compile_owners:
                if pool is not None:
                    _compile_offline_pool(transaction, pool, deadline=deadline)
                    selected_lock = _read_lock(lock_file, deadline)
                    selected_files = _wheel_pins(
                        selected_lock, target, deadline, prefer_target_tags=True,
                        local_wheels=pool.paths,
                    )
                    if any(pool.digests.get(item["filename"]) != item["sha256"] for item in selected_files):
                        raise ProfileError("offline selected wheels differ from the held pool")
                elif guarded_feed:
                    compile_owners.enter_context(hold_compile_inputs(transaction, deadline))
                    initial_policy, initial_feed, floor, cutoffs = _guarded_feed_inputs(
                        selected, transaction, projections, uv=uv, download_origins=download_origins,
                        exclusions=first_exclusions, deadline=deadline,
                    )
                    if nonsecret_feed_url is not None:
                        require_public_feed(selected, transaction, nonsecret_feed_url, initial_feed)
                    witness, guarded_directory, snapshot_budget = compile_owners.enter_context(compile_guarded(
                        transaction, projections[1], initial_feed, floor=floor, cutoffs=cutoffs,
                        exclusions=first_exclusions, target_tags=target["tags"],
                        limits=acquisition_limits, wheel_limits=limits,
                        deadline=deadline, procutil=_producer_procutil(),
                    ))
                    selected_lock = _read_lock(lock_file, deadline)
                    selected_files = _wheel_pins(selected_lock, target, deadline, prefer_target_tags=True)
                    witness_urls = verify_lock_witness(selected_lock, selected_files, witness, deadline)
                    remaining = {
                        "max_requests": acquisition_limits.max_requests - witness["usage"]["requests"],
                        "max_redirects": acquisition_limits.max_redirects - witness["usage"]["redirects"],
                        "max_total_metadata_bytes": (
                            acquisition_limits.max_total_metadata_bytes - witness["usage"]["metadata_bytes"]
                        ),
                    }
                    if any(value <= 0 for value in remaining.values()):
                        raise ProfileError("guarded native selection exhausted the acquisition budget")
                    acquisition_limits = replace(acquisition_limits, **remaining)
                if pool is not None or guarded_feed:
                    owners.enter_context(_held(
                        {"generated-lock": lock_file},
                        {"generated-lock": {
                            "sha256": "sha256:" + hashlib.sha256(selected_lock).hexdigest(),
                            "size": len(selected_lock),
                        }},
                        deadline,
                    ))
                    if first_names & {item["name"] for item in selected_files}:
                        raise ProfileError("native core lock overlaps its first-party wheel set")
                policy, feed = native._profile_feed(
                    selected, transaction, projections, uv=uv,
                    download_origins=download_origins, wheels=selected_files, deadline=deadline,
                    excluded_local_packages=first_exclusions,
                )
                if guarded_feed and (
                    policy != initial_policy or feed.policy_id != initial_feed.policy_id
                ):
                    raise ProfileError("native core feed identity changed during selection")
                public_feed = None
                if nonsecret_feed_url is not None:
                    public_feed = public_feed_policy(
                        selected, transaction, projections, uv=uv, url=nonsecret_feed_url,
                        wheels=selected_files, exclusions=first_exclusions, deadline=deadline,
                    )
                    if (
                        [(item.index_url, item.headers) for item in public_feed.feeds]
                        != [(item.index_url, item.headers) for item in feed.feeds]
                        or public_feed.cutoffs != feed.cutoffs
                    ):
                        raise ProfileError("public feed capture differs from the native acquisition")
                if guarded_feed:
                    if selected_files:
                        selected_files, _origins = validate_acquisition_inputs(
                            selected_files, feeds=feed.feeds, download_origins=feed.download_origins,
                            cutoffs=feed.cutoffs, policy_id=feed.policy_id, limits=acquisition_limits,
                            deadline=deadline, ssl_context=feed.context, proxies=feed.proxies,
                            procutil=_producer_procutil(),
                        )
                    verify_guarded_cutoffs(
                        selected_files, witness_urls, witness, feed.cutoffs, deadline=deadline,
                    )
                    acquisition = retain_wheel_inputs(
                        {item["filename"]: guarded_directory / item["filename"] for item in selected_files},
                        {item["filename"]: item["sha256"] for item in selected_files},
                        max_wheels=acquisition_limits.max_packages,
                        max_archive_bytes=acquisition_limits.max_wheel_bytes,
                        max_total_bytes=acquisition_limits.max_closure_bytes,
                        max_snapshot_bytes=snapshot_budget, deadline=deadline,
                    ) if selected_files else nullcontext(None)
                else:
                    acquisition = acquire_wheel_inputs(
                        selected_files, feeds=feed.feeds, download_origins=feed.download_origins,
                        cutoffs=feed.cutoffs, policy_id=feed.policy_id,
                        limits=acquisition_limits,
                        deadline=deadline, ssl_context=feed.context, proxies=feed.proxies,
                        procutil=_producer_procutil(),
                    ) if selected_files else nullcontext(None)
                with acquisition as acquired:
                    paths = dict(first.paths)
                    expected = dict(first_digests)
                    external = acquired if guarded_feed else (acquired.snapshot if acquired is not None else None)
                    if external is not None:
                        if paths.keys() & external.paths.keys():
                            raise ProfileError("core and native wheel filenames collide")
                        paths.update(external.paths)
                        expected.update({item["filename"]: item["sha256"] for item in selected_files})
                    with retain_wheel_inputs(
                        paths, expected, max_wheels=256,
                        max_archive_bytes=limits.max_archive_bytes, max_total_bytes=max_bytes,
                        max_snapshot_bytes=4 * max_bytes, deadline=deadline,
                    ) as retained:
                        native_profile = native.complete_profile(
                            selected, target, roots, retained, policy_id=policy["policyId"],
                            acquisition_id=public_feed.policy_id if public_feed is not None else feed.policy_id,
                            deadline=deadline,
                        )
                        selected_paths = {
                            row["filename"]: retained.paths[row["filename"]]
                            for row in native_profile.record()["wheels"]
                        }
                        if set(selected_paths) != set(paths):
                            raise ProfileError("native core inputs include unreachable wheels")
                        profile = build_core_profile(
                            manifest_data=manifest_data, native_record=native_profile.record(),
                            archives=selected_paths, limits=limits, max_wheels=256,
                            max_total_bytes=max_bytes, max_snapshot_bytes=4 * max_bytes,
                            max_unpacked_bytes=limits.max_expanded_bytes, deadline=deadline, layout=layout,
                        )
                        if index is not None:
                            candidate_index = compose_core_profile_index(index, manifest_data, profile)
                        transport = work / "wheels.zip"
                        wheel_result = build_profile_bundle(
                            profile.record(), selected_paths, transport, limits=limits,
                            max_wheels=256, max_total_bytes=max_bytes,
                            max_snapshot_bytes=4 * max_bytes,
                            max_unpacked_bytes=limits.max_expanded_bytes,
                            max_bundle_bytes=max_bytes, deadline=deadline, layout=layout,
                        )
                        result = build_profile_delivery(
                            profile.record(), transport, wheel_result["sha256"],
                            materials, material_paths, candidate, max_bytes=max_bytes,
                            # The delivery reader also uses this limit for members inside each wheel.
                            max_files=limits.max_members, max_snapshot_bytes=8 * max_bytes,
                            deadline=deadline, layout=layout,
                        )
                        retained.verify()
                    if external is not None:
                        external.verify()
                final_policy, final_feed = native._profile_feed(
                    selected, transaction, projections, uv=uv,
                    download_origins=download_origins, wheels=selected_files, deadline=deadline,
                    excluded_local_packages=first_exclusions,
                )
                if policy != final_policy or feed.policy_id != final_feed.policy_id:
                    raise ProfileError("native core feed identity changed during construction")
                if public_feed is not None:
                    final_public = public_feed_policy(
                        selected, transaction, projections, uv=uv, url=nonsecret_feed_url,
                        wheels=selected_files, exclusions=first_exclusions, deadline=deadline,
                    )
                    if final_public.policy_id != public_feed.policy_id or final_public.cutoffs != feed.cutoffs:
                        raise ProfileError("public feed identity changed during construction")
                if guarded_feed and witness["projects"]:
                    verification_limits = acquisition_limits
                if project_core_inputs(source, manifest_data, target, deadline=deadline) != (
                    roots, requirements,
                ):
                    raise ProfileError("core source projection changed during construction")
                first.verify()
                if pool is not None:
                    _offline_pool_directory(pool, deadline=deadline)
                    pool.verify()
        before = _ordinary(candidate)
        with _open(candidate) as retained:
            opened = _unchanged(candidate, before, retained)
            if _digest(retained, max_bytes, deadline) != (result["sha256"], result["size"]):
                raise ProfileError("core delivery changed after input verification")
            if verification_limits is not None:
                verify_guarded_projects(
                    final_feed, witness["projects"], limits=verification_limits,
                    wheel_limits=limits, target_tags=target["tags"],
                    deadline=deadline, procutil=_producer_procutil(),
                )
                if project_core_inputs(source, manifest_data, target, deadline=deadline) != (
                    roots, requirements,
                ):
                    raise ProfileError("core source projection changed during guarded verification")
            held_inputs.close()
            _unchanged(candidate, before, retained, opened=opened)
            _check_deadline(deadline)
            os.link(candidate, destination)
    return result | {
        "source_commit": identity.source_commit, "approval": "unattested",
        **({"selection": "offline-wheel-pool"} if offline_wheel_pool is not None else {}),
        **({"selection": "guarded-native-feed"} if guarded_feed else {}),
        **({
            "candidate_index": candidate_index,
            "candidate_index_sha256": "sha256:" + hashlib.sha256(_json(candidate_index)).hexdigest(),
            "source_index_sha256": "sha256:" + hashlib.sha256(index_data).hexdigest(),
        } if index is not None else {}),
    }


def prepare_core_delivery(
    repository: Path, commit: str, main_head: str, manifest: Path, wheel_dir: Path,
    materials: dict, material_paths: dict, destination: Path, *, uv: str,
    download_origins: tuple[str, ...], deadline: float, max_bytes: int,
    native_lock: Path | None = None, offline_wheel_pool: Path | None = None,
    guarded_feed: bool = False, artifact_index: Path | None = None,
    nonsecret_feed_url: str | None = None, layout: str | None = None,
) -> dict:
    """Compose the selected Main source and prepare one inert native candidate."""
    _check_deadline(deadline)
    if type(max_bytes) is not int or max_bytes <= 0:
        raise ProfileError("core delivery requires a positive byte budget")
    if layout not in (None, MANAGER_ISOLATED):
        raise ProfileError("unsupported core delivery environment layout")
    if type(guarded_feed) is not bool or sum(
        (native_lock is not None, offline_wheel_pool is not None, guarded_feed),
    ) != 1:
        raise ProfileError("core delivery requires exactly one native selection mode")
    if nonsecret_feed_url is not None and (not guarded_feed or download_origins):
        raise ProfileError("public feed identity requires delegated guarded-feed selection")
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
        work = Path(source_owner.native_retained_path(
            native.dependencies._private_retained_root(Path(temporary)).resolve(),
        ))
        snapshot = Path(source_owner.native_retained_path(work / "source-snapshot"))
        record = source_owner.compose(
            repository, commit, main_head, snapshot, finalized_main=True, deadline=deadline,
        )
        return _build_from_snapshot(
            snapshot / "source", record, manifest_data, wheel_dir, target, uv=uv,
            download_origins=download_origins, materials=materials, material_paths=material_paths,
            destination=destination, deadline=deadline, max_bytes=max_bytes, native_lock=native_lock,
            offline_wheel_pool=offline_wheel_pool, guarded_feed=guarded_feed, artifact_index=artifact_index,
            nonsecret_feed_url=nonsecret_feed_url, layout=layout,
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
    parser.add_argument(
        "--artifact-index", type=Path,
        help="compose an unsigned index candidate from this independently authenticated source index",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--uv", required=True)
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--native-lock", type=Path)
    selection.add_argument("--offline-wheel-pool", type=Path)
    selection.add_argument("--guarded-feed", action="store_true")
    parser.add_argument("--download-origin", action="append", default=[])
    parser.add_argument("--layout", choices=(MANAGER_ISOLATED,))
    parser.add_argument(
        "--nonsecret-feed-url",
        help="opt in to reproducible identity for this explicitly nonsecret anonymous HTTPS feed",
    )
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
            deadline=deadline, max_bytes=args.max_bytes, native_lock=args.native_lock,
            offline_wheel_pool=args.offline_wheel_pool, guarded_feed=args.guarded_feed,
            artifact_index=args.artifact_index, nonsecret_feed_url=args.nonsecret_feed_url, layout=args.layout,
        )
        print(json.dumps(result, sort_keys=True))
        return 0
    except (OSError, ValueError, RuntimeError) as error:
        parser.exit(1, f"core delivery refused: {error}\n")


if __name__ == "__main__":
    raise SystemExit(main())
