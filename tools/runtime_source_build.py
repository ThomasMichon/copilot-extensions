"""Build independently authenticated source using admitted, closed build tools.

Repository-only composition: no consumer is enabled and no source, feed or
installation policy is created here. The existing constructor owns both
immutable environments; the existing native compiler owns all resolution.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import time
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
for _library in ("installer-engine", "versioned-runtime"):
    sys.path.insert(0, str(_ROOT / "libs" / _library))

import installer_engine as engine
from build_toolchain_lock import ToolchainLock, _query_marker_environment
from installer_wheel_acquisition import AcquisitionLimits, acquire_wheel_inputs
from installer_wheel_inputs import WheelLimits, _check_deadline
from installer_wheel_snapshot import WheelInstallInputs
from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name
from packaging.version import Version
from runtime_wheel_inspection import inspect_wheel
import runtime_feed_configuration
from runtime_feed_configuration import retain_source_feed_inputs
from runtime_profile import (
    SCHEMA,
    ProfileError,
    RuntimeProfile,
    _reachable,
    validate_profile,
)
from runtime_profile_wheels import profile_wheel_inputs
from runtime_source_authentication import retain_authenticated_source
from runtime_source_lock import (
    _build_roots,
    _source,
    dependencies,
    retain_source_configuration,
    retain_source_wheel_lock,
)
from runtime_source_wheels import retain_source_profile
from versioned_runtime_source import SourceIdentity


@dataclass(frozen=True)
class SourceRuntimeInputs:
    payload: Path
    identity: SourceIdentity
    profile: RuntimeProfile
    wheels: WheelInstallInputs
    build_slot: Path
    local_packages: tuple[str, ...]
    _verification: Callable[[], None] = field(repr=False, compare=False)

    @property
    def runtime_version(self) -> str:
        return self.identity.runtime_version + ".profile." + self.wheels.receipt()["lock_id"][7:23]

    def verify(self) -> None:
        self._verification()
        self.wheels.verify()
        _source(self.payload, self.identity)


def _metadata_entries(snapshot, files, *, limits, deadline, hints=None):
    entries, remaining = [], limits.max_expanded_bytes
    for selected in files:
        _check_deadline(deadline)
        archive = snapshot.archives[selected["filename"]]
        archive.seek(0)
        metadata, size = inspect_wheel(
            archive, selected, max_archive_bytes=limits.max_archive_bytes,
            max_unpacked_bytes=remaining, max_metadata_bytes=limits.max_metadata_member_bytes,
            limits=limits, deadline=deadline,
        )
        if (
            canonicalize_name(metadata["Name"]) != selected["name"]
            or Version(metadata["Version"]) != Version(selected["version"])
        ):
            raise ProfileError("source lock differs from actual wheel metadata")
        hint = (hints or {}).get(selected["filename"])
        if hint is not None and SpecifierSet(hint) != SpecifierSet(metadata.get("Requires-Python", "")):
            raise ProfileError("source wheel Python metadata differs from its selected index hint")
        entries.append({
            **selected, "requires_python": metadata.get("Requires-Python", ""),
            "provides_extras": metadata.get_all("Provides-Extra", []), "selected_extras": [],
            "requires_dist": metadata.get_all("Requires-Dist", []),
        })
        remaining -= size
    snapshot.verify()
    return entries


def _profile(locked, authenticated, feed, entries, roots, deadline):
    record = locked.record()
    by_name = {
        item["name"]: (item, [Requirement(text) for text in item["requires_dist"]])
        for item in entries
    }
    if len(by_name) != len(entries):
        raise ProfileError("source build has ambiguous distribution metadata")
    wanted = _reachable(by_name, roots, record["target"]["environment"], deadline)
    return validate_profile({
        "schema": SCHEMA, "version": 1,
        "release": {
            "identity": "source:" + authenticated.identity.source_commit,
            "sha256": "sha256:" + authenticated.identity.assembly_hash,
        },
        "mode": "source", "artifact": None, "component": record["component"],
        "backend": record["policy"]["selection"]["backend"], "target": record["target"],
        "roots": roots, "approval_source": "configured-source:" + hashlib.sha256(authenticated.canonical).hexdigest(),
        "acquisition_policy": feed.policy_id,
        "wheels": [
            {**by_name[name][0], "selected_extras": sorted(wanted[name])}
            for name in sorted(wanted)
        ],
    }, deadline=deadline)


def _inputs(profile, snapshot, *, evidence, limits, deadline):
    result = profile_wheel_inputs(
        profile, snapshot, max_archive_bytes=limits.max_archive_bytes * len(snapshot.paths),
        max_unpacked_bytes=limits.max_expanded_bytes,
        max_metadata_bytes=limits.max_metadata_member_bytes, limits=limits, deadline=deadline,
    )
    # Include stable source/build/acquisition facts in the constructor's own
    # binding, not in an ad hoc approval map or an acquisition trust token.
    result = replace(result, binding_json=json.dumps({
        "schema": "copilot-extensions.source-runtime-inputs", "version": 1,
        "profile": profile.record(), **evidence,
    }, sort_keys=True, separators=(",", ":"), allow_nan=False))
    result.verify()
    return result


@contextmanager
def retain_source_runtime_inputs(
    plan, *, installation: dict, target: dict, component: str, uv: str,
    python: Path, extras: tuple[str, ...] = (), env: dict[str, str] | None = None,
    acquisition_limits: AcquisitionLimits, wheel_limits: WheelLimits,
    max_lock_bytes: int, max_snapshot_bytes: int, deadline: float,
    revoked_inputs: Callable[[], frozenset[str]] | None = None,
):
    """Retain authenticated source, admitted tools and built runtime wheels.

    The caller must keep this context open through installed health. Build tools
    live in their own numbered root; they never enter the final runtime closure.
    All subprocesses that execute a backend use the existing owned-child scope.
    """
    _check_deadline(deadline)
    acquisition_limits.verify()
    if type(max_snapshot_bytes) is not int or max_snapshot_bytes <= 0:
        raise ProfileError("source runtime needs an explicit wheel snapshot budget")
    selected_uv = dependencies._select_uv_executable(uv)
    uv = str(Path(shutil.which(selected_uv) or selected_uv).resolve(strict=True))
    with retain_authenticated_source(
        plan, installation=installation, deadline=deadline,
    ) as authenticated, retain_source_wheel_lock(
        plan, payload=authenticated.payload, expected_source=authenticated.identity,
        target=target, component=component, uv=uv, python=python, extras=extras, env=env,
        max_lock_bytes=max_lock_bytes, deadline=deadline, include_build_tools=True,
        installation=installation,
    ) as locked, retain_source_feed_inputs(
        plan, locked, installation=installation, deadline=deadline,
    ) as feed:
        packages = engine._package_roots(locked.payload)
        frozen = engine.FrozenInputs(locked.payload, packages, "")
        procutil = engine._procutil(frozen)
        with acquire_wheel_inputs(
            locked.record()["files"], feeds=feed.feeds, download_origins=feed.download_origins,
            cutoffs=feed.cutoffs, policy_id=feed.policy_id, limits=acquisition_limits,
            deadline=deadline, ssl_context=feed.context, proxies=feed.proxies, procutil=procutil,
        ) as acquired:
            record = locked.record()
            if record["build_roots"] != _build_roots(locked.payload, deadline):
                raise ProfileError("source build requirements differ from their native lock")
            acquisition = acquired.evidence()
            hints = {
                item["filename"]: item["requires_python"] for item in acquisition["files"]
                if "requires_python" in item
            }
            entries = _metadata_entries(
                acquired.snapshot, record["files"], limits=wheel_limits, deadline=deadline, hints=hints,
            )
            evidence = {
                "source": authenticated.record(), "source_lock": record,
                "acquisition": {
                    key: acquisition[key] for key in ("schema", "version", "policy_id", "files")
                },
            }
            build_profile = _profile(
                locked, authenticated, feed, entries, record["build_roots"], deadline,
            )
            build_snapshot = acquired.snapshot.subset(tuple(
                item["filename"] for item in build_profile.record()["wheels"]
            ))
            build_inputs = _inputs(
                build_profile, build_snapshot, evidence=evidence, limits=wheel_limits, deadline=deadline,
            )

            def verify_build():
                authenticated.verify()
                if revoked_inputs is not None:
                    from runtime_source_reuse import verify_not_revoked

                    verify_not_revoked(build_inputs.receipt(), revoked_inputs)

            verify_build()
            build_root = Path(record["policy"]["owner"]["runtimeRoot"]) / ".source-build-tools"
            build_version = authenticated.identity.runtime_version + ".build." + build_inputs.receipt()["lock_id"][7:23]

            def validate_tools(slot):
                verify_build()
                installed_python = engine.runtime.slot_python(build_root, slot.name)
                if _query_marker_environment(installed_python, deadline=deadline) != record["target"]["environment"]:
                    raise ProfileError("installed build-tools interpreter differs from the selected target")

            with engine.numbered_candidate(
                build_root, build_version, plugin=locked.payload, uv=Path(uv), base_python=python,
                health_module="packaging", local_packages=(), wheel_inputs=build_inputs,
                wait_timeout=deadline - time.monotonic(),
                expected_source_identity=engine.runtime_source.manifest_fields(authenticated.identity),
                validate_candidate=validate_tools,
            ) as tools:
                tool_python = tools.slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
                toolchain = ToolchainLock(tool_python, {
                    item["name"]: item["version"] for item in build_profile.record()["wheels"]
                })
                selected = replace(locked, canonical=json.dumps({
                    **record, "source_authentication": evidence["source"],
                    "acquisition": evidence["acquisition"],
                }, sort_keys=True, separators=(",", ":"), allow_nan=False).encode(),
                    _verification=verify_build,
                )
                authenticated.verify()
                with retain_source_profile(
                    selected, third_party=acquired.snapshot.paths, uv=uv,
                    toolchain=toolchain, limits=wheel_limits, build_profile=build_profile,
                    max_wheels=acquisition_limits.max_packages + len(packages),
                    max_total_bytes=max_snapshot_bytes, max_snapshot_bytes=max_snapshot_bytes,
                    deadline=deadline, offline=True, procutil=procutil,
                    construction_fd=tools.descriptor,
                    retained_runtime=(build_root, build_version, Path(engine.runtime.__file__)),
                    opaque_identity=runtime_feed_configuration._opaque_index_identity,
                ) as completed:
                    def verify_runtime():
                        verify_build()
                        if revoked_inputs is not None:
                            from runtime_source_reuse import verify_not_revoked

                            verify_not_revoked(completed.wheels.receipt(), revoked_inputs)

                    result = SourceRuntimeInputs(
                        locked.payload, authenticated.identity, completed.profile,
                        completed.wheels, tools.slot, completed.local_packages, verify_runtime,
                    )
                    result.verify()
                    yield result
                    result.verify()


@contextmanager
def _manager_source_inputs(plan, *, revoked_inputs, **options):
    from runtime_source_reuse import retain_source_runtime_reuse, selected_generation

    root = Path(plan.policy()["owner"]["runtimeRoot"])
    current = selected_generation(root)
    if current and ".profile." in current:
        with retain_authenticated_source(
            plan, installation=options["installation"], deadline=options["deadline"],
        ) as authenticated, retain_source_configuration(
            plan, payload=authenticated.payload, expected_source=authenticated.identity,
            target=options["target"], component=options["component"], uv=options["uv"],
            python=options["python"], extras=options.get("extras", ()), env=options.get("env"),
            deadline=options["deadline"], wheel_witness=True, include_build_tools=True,
            installation=options["installation"],
        ) as configuration, retain_source_runtime_reuse(
            authenticated, configuration, max_receipt_bytes=options["max_snapshot_bytes"],
            max_tags=options["wheel_limits"].max_tags, deadline=options["deadline"],
            revoked_inputs=revoked_inputs,
        ) as existing:
            if existing is not None:
                yield existing
                return
    with retain_source_runtime_inputs(
        plan, **options, **({"revoked_inputs": revoked_inputs} if revoked_inputs is not None else {}),
    ) as source:
        yield source


def install_manager_source_runtime(plan, *, revoked_inputs=None, **options):
    """Enter the actual Manager lifecycle while its authenticated inputs live.

    This explicit repository caller does not enable marketplace/bootstrap
    consumers or turn retained constructor receipts into source approval.
    """
    from worktree_manager import self_install

    owner = plan.policy()["owner"]
    if owner["pluginId"] != "worktree-manager" or options.get("extras", ()):
        raise ProfileError("Manager source installation requires its base installation owner")
    if options.get("uv") is not None:
        selected_uv = dependencies._select_uv_executable(options["uv"])
        options["uv"] = str(Path(shutil.which(selected_uv) or selected_uv).resolve(strict=True))
    root = Path(owner["runtimeRoot"]).resolve()
    with _manager_source_inputs(plan, revoked_inputs=revoked_inputs, **options) as source:
        try:
            return _install_manager_inputs(
                source, root, self_install, revoked_inputs, options.get("deadline"), options.get("python"),
                options.get("uv"),
            )
        except engine.HealthRepairRequired:
            if not isinstance(source.wheels, engine.WheelReuseInputs):
                raise
    # A stored receipt cannot build its own repair. Re-enter ordinary source
    # admission with actual bytes before the existing adapter allocates one.
    with retain_source_runtime_inputs(
        plan, **options, **({"revoked_inputs": revoked_inputs} if revoked_inputs is not None else {}),
    ) as source:
        return _install_manager_inputs(
            source, root, self_install, revoked_inputs, options.get("deadline"), options.get("python"),
            options.get("uv"),
        )


def _install_manager_inputs(source, root, self_install, revoked_inputs, deadline, python, uv):
    if source.identity.plugin != "worktree-manager":
        raise ProfileError("Manager source installation has a foreign source identity")

    def verify():
        source.verify()
        if revoked_inputs is not None:
            from runtime_source_reuse import verify_not_revoked

            verify_not_revoked(source.wheels.receipt(), revoked_inputs)

    def validate(slot):
        verify()
        python = engine.runtime.slot_python(root, slot.name)
        if python is None or _query_marker_environment(python, deadline=deadline) != (
            source.profile.record()["target"]["environment"]
        ):
            raise ProfileError("installed Manager interpreter differs from the selected target")

    verify()
    result = self_install.self_install(
        source.payload, root=root, dry_run=False,
        _wheel_inputs=source.wheels, _local_packages=source.local_packages,
        _validate_candidate=validate, _before_publish=verify,
        _raise_candidate_errors=True,
        **({"_base_python": python} if python is not None else {}),
        **({"_uv": Path(uv)} if uv is not None else {}),
    )
    verify()
    return result
