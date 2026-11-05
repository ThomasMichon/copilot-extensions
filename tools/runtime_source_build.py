"""Build independently authenticated source using admitted, closed build tools.

Reusable composition: no consumer is enabled and no source, feed or
installation policy is created here. The existing constructor owns both
immutable environments; the existing native compiler owns all resolution.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import threading
import time
from collections.abc import Callable
from contextlib import AbstractContextManager, ExitStack, contextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
for _library in ("installer-engine", "versioned-runtime"):
    sys.path.insert(0, str(_ROOT / "libs" / _library))

import installer_engine as engine
from build_toolchain_lock import ToolchainLock, _query_marker_environment
from installer_wheel_acquisition import AcquisitionLimits, acquire_wheel_inputs, admit_retained_wheel_inputs
from installer_wheel_inputs import WheelLimits, _check_deadline
from installer_wheel_snapshot import WheelInstallInputs, WheelSnapshot
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
    build_slot: Path | None
    local_packages: tuple[str, ...]
    _verification: Callable[[], None] = field(repr=False, compare=False)
    _guard: AbstractContextManager = field(default_factory=threading.RLock, repr=False, compare=False)

    @property
    def runtime_version(self) -> str:
        return self.identity.runtime_version + ".profile." + self.wheels.receipt()["lock_id"][7:23]

    def verify(self) -> None:
        with self._guard:
            self._verification()
            self.wheels.verify()
            _source(self.payload, self.identity)


@dataclass(frozen=True)
class AssociatedSourceWheels:
    """Held candidate bytes and their independently supplied association owner."""

    snapshot: WheelSnapshot
    projects: tuple[tuple[str, str], ...]
    toolchain: ToolchainLock
    _verification: Callable[[SourceIdentity], None] = field(repr=False, compare=False)

    def __post_init__(self):
        if (
            not isinstance(self.snapshot, WheelSnapshot)
            or not isinstance(self.toolchain, ToolchainLock)
            or not callable(self._verification)
            or not isinstance(self.projects, tuple) or not 1 <= len(self.projects) <= 256
            or any(
                not isinstance(item, tuple) or len(item) != 2
                or not all(isinstance(value, str) and value for value in item)
                or canonicalize_name(item[0]) != item[0]
                or item[1] not in self.snapshot.paths
                for item in self.projects
            )
            or len({name for name, _ in self.projects}) != len(self.projects)
            or len({filename for _, filename in self.projects}) != len(self.projects)
        ):
            raise ProfileError("associated source wheels need an exact retained owner selection")

    def verify(self, identity: SourceIdentity) -> None:
        if self._verification(identity) is not None:
            raise ProfileError("associated source owner returned an invalid verification result")
        self.snapshot.verify()


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
def retain_associated_source_runtime_inputs(
    plan, *, associated: AssociatedSourceWheels, installation: dict, target: dict,
    component: str, uv: str, python: Path, extras: tuple[str, ...] = (),
    env: dict[str, str] | None = None, acquisition_limits: AcquisitionLimits,
    wheel_limits: WheelLimits, max_lock_bytes: int, max_snapshot_bytes: int,
    deadline: float, revoked_inputs: Callable[[], frozenset[str]] | None = None,
):
    """Use associated retained wheels under live source and native-feed owners.

    The caller must supply the independently authenticated first-party
    association and keep its verification callback and snapshot alive. Neither
    this object nor the delivery profile approves a release or installation.
    """
    _check_deadline(deadline)
    if not isinstance(associated, AssociatedSourceWheels):
        raise ProfileError("source wheel reuse requires its associated owner")
    acquisition_limits.verify()
    if type(max_snapshot_bytes) is not int or max_snapshot_bytes <= 0:
        raise ProfileError("source runtime needs an explicit wheel snapshot budget")
    selected_uv = dependencies._select_uv_executable(uv)
    uv = str(Path(shutil.which(selected_uv) or selected_uv).resolve(strict=True))
    toolchain_record = json.dumps(
        associated.toolchain.record, sort_keys=True, separators=(",", ":"), allow_nan=False,
    )
    with ExitStack() as stack:
        authenticated = stack.enter_context(retain_authenticated_source(
            plan, installation=installation, deadline=deadline,
        ))
        associated.verify(authenticated.identity)
        if json.dumps(
            associated.toolchain.record, sort_keys=True, separators=(",", ":"), allow_nan=False,
        ) != toolchain_record:
            raise ProfileError("associated build toolchain changed")
        locked = stack.enter_context(retain_source_wheel_lock(
            plan, payload=authenticated.payload, expected_source=authenticated.identity,
            target=target, component=component, uv=uv, python=python, extras=extras, env=env,
            max_lock_bytes=max_lock_bytes, deadline=deadline, installation=installation,
        ))
        feed = stack.enter_context(retain_source_feed_inputs(
            plan, locked, installation=installation, deadline=deadline,
        ))
        files = locked.record()["files"]
        filenames = tuple(item["filename"] for item in files)
        first_party = {name: associated.snapshot.paths[filename] for name, filename in associated.projects}
        if (
            len(set(filenames)) != len(filenames)
            or set(filenames) & {filename for _, filename in associated.projects}
            or any(
                item["filename"] not in associated.snapshot.paths
                or associated.snapshot.digests[item["filename"]] != item["sha256"]
                for item in files
            )
        ):
            raise ProfileError("retained wheels differ from the selected source lock")
        external = associated.snapshot.subset(filenames) if filenames else None
        expected_hashes = {
            filename: associated.snapshot.digests[filename]
            for filename in (*filenames, *(item[1] for item in associated.projects))
        }
        packages = engine._package_roots(locked.payload)
        procutil = engine._procutil(engine.FrozenInputs(locked.payload, packages, ""))

        def verify_source():
            authenticated.verify()
            locked.verify(deadline=deadline)

        def verify_owner():
            verify_source()
            associated.verify(authenticated.identity)
            if json.dumps(
                associated.toolchain.record, sort_keys=True, separators=(",", ":"), allow_nan=False,
            ) != toolchain_record:
                raise ProfileError("associated build toolchain changed")

        def verify_feed():
            if external is None:
                return None
            with admit_retained_wheel_inputs(
                files, snapshot=external, feeds=feed.feeds, download_origins=feed.download_origins,
                cutoffs=feed.cutoffs, policy_id=feed.policy_id, limits=acquisition_limits,
                deadline=deadline, ssl_context=feed.context, proxies=feed.proxies, procutil=procutil,
            ) as admitted:
                evidence = admitted.evidence()
                hints = {
                    item["filename"]: item["requires_python"] for item in evidence["files"]
                    if "requires_python" in item
                }
                _metadata_entries(
                    admitted.snapshot, files, limits=wheel_limits, deadline=deadline, hints=hints,
                )
                return {key: evidence[key] for key in ("schema", "version", "policy_id", "files")}

        def stable(evidence):
            if evidence is None:
                return None
            return {
                **evidence,
                "files": [
                    {key: value for key, value in item.items() if key != "requires_python"}
                    for item in evidence["files"]
                ],
            }

        verify_owner()
        evidence = verify_feed()
        verify_owner()
        selected = replace(
            locked,
            canonical=json.dumps({
                **locked.record(), "source_authentication": authenticated.record(),
                **({"acquisition": evidence} if evidence is not None else {}),
            }, sort_keys=True, separators=(",", ":"), allow_nan=False).encode(),
            _verification=verify_source,
        )
        third_party = {name: associated.snapshot.paths[name] for name in filenames}
        with retain_source_profile(
            selected, third_party=third_party, first_party=first_party, uv=uv,
            toolchain=associated.toolchain, limits=wheel_limits,
            max_wheels=acquisition_limits.max_packages + len(first_party),
            max_total_bytes=max_snapshot_bytes, max_snapshot_bytes=max_snapshot_bytes,
            deadline=deadline,
        ) as completed:
            active = True

            def verify_wheels():
                if dict(completed.wheels.snapshot.digests) != expected_hashes:
                    raise ProfileError("completed source wheels differ from the retained association")

            def verify_runtime():
                if not active:
                    raise ProfileError("associated source wheel custody is closed")
                verify_owner()
                if stable(verify_feed()) != stable(evidence):
                    raise ProfileError("retained source wheel feed evidence changed")
                verify_owner()
                verify_wheels()
                if revoked_inputs is not None:
                    from runtime_source_reuse import verify_not_revoked

                    verify_not_revoked(completed.wheels.receipt(), revoked_inputs)

            result = SourceRuntimeInputs(
                locked.payload, authenticated.identity, completed.profile,
                completed.wheels, None, completed.local_packages, verify_runtime,
            )
            try:
                result.verify()
                yield result
                result.verify()
            finally:
                with result._guard:
                    active = False


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
                locked.verify(deadline=deadline)
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
