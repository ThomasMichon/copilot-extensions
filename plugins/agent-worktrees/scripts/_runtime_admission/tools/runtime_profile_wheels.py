"""Compare retained wheel bytes with an exact profile; never approve or acquire."""
from __future__ import annotations

import hashlib
import json
import time
from types import MappingProxyType
from typing import TYPE_CHECKING, BinaryIO, Mapping

from runtime_wheel_inspection import PromotionArtifactError, inspect_wheel_facts
from runtime_wheel_inspection import inspect_wheel as inspect_wheel
from installer_wheel_inputs import WheelLimits, _check_deadline
from packaging.utils import canonicalize_name
from runtime_profile import ProfileError, RuntimeProfile, _reachable, _requirement, validate_profile

if TYPE_CHECKING:
    from installer_wheel_snapshot import WheelInstallInputs, WheelSnapshot

MANAGER_ISOLATED = "manager-isolated"
_MANAGER = "copilot-extensions-worktree-manager"


def _archive_digest(archive: BinaryIO, limit: int, deadline: float) -> tuple[str, int]:
    archive.seek(0)
    digest = hashlib.sha256()
    size = 0
    while True:
        _check_deadline(deadline)
        chunk = archive.read(min(1 << 20, limit - size + 1))
        if not chunk:
            break
        size += len(chunk)
        if size > limit:
            raise ProfileError("wheel archives exceed the supplied byte budget")
        digest.update(chunk)
    _check_deadline(deadline)
    return "sha256:" + digest.hexdigest(), size


def derive_profile_environments(
    profile: RuntimeProfile, *, layout: str, deadline: float | None = None,
) -> Mapping[str, RuntimeProfile]:
    """Derive closures from a whole profile, without independently approving them."""
    deadline = time.monotonic() + 120 if deadline is None else deadline
    _check_deadline(deadline)
    if layout != MANAGER_ISOLATED or not isinstance(profile, RuntimeProfile):
        raise ProfileError("unsupported profile environment layout")
    record = validate_profile(profile.record(), deadline=deadline).record()
    roots = {"shared": [], "manager": []}
    for text in record["roots"]:
        requirement = _requirement(text, deadline)
        roots["manager" if canonicalize_name(requirement.name) == _MANAGER else "shared"].append(text)
    if not roots["manager"] or not roots["shared"]:
        raise ProfileError("manager isolation requires explicit manager and shared roots")
    wheels = {
        canonicalize_name(row["name"]): (
            row, [_requirement(text, deadline) for text in row["requires_dist"]],
        )
        for row in record["wheels"]
    }
    reachable = {
        name: _reachable(wheels, selected, record["target"]["environment"], deadline)
        for name, selected in roots.items()
    }
    if (
        _MANAGER not in reachable["manager"] or _MANAGER in reachable["shared"]
        or set(reachable["shared"]) | set(reachable["manager"]) != set(wheels)
    ):
        raise ProfileError("manager isolation does not preserve the complete root closure")
    result = {}
    for name in ("shared", "manager"):
        wanted = reachable[name]
        selected_wheels = [
            row | {"selected_extras": sorted(wanted[canonicalize_name(row["name"])])}
            for row in record["wheels"] if canonicalize_name(row["name"]) in wanted
        ]
        result[name] = validate_profile(
            record | {"roots": roots[name], "wheels": selected_wheels}, deadline=deadline,
        )
    return MappingProxyType(result)


def verify_profile_layout(
    profile: RuntimeProfile, archives: Mapping[str, BinaryIO], *,
    max_archive_bytes: int, max_unpacked_bytes: int, max_metadata_bytes: int,
    limits: WheelLimits | None = None, deadline: float | None = None,
    layout: str | None = None,
) -> Mapping[str, RuntimeProfile]:
    """Inspect every selected wheel in its derived environment, without approval."""
    deadline = time.monotonic() + 120 if deadline is None else deadline
    if layout is None:
        verify_profile_wheels(
            profile, archives, max_archive_bytes=max_archive_bytes,
            max_unpacked_bytes=max_unpacked_bytes, max_metadata_bytes=max_metadata_bytes,
            limits=limits, deadline=deadline,
        )
        return MappingProxyType({"shared": profile})
    environments = derive_profile_environments(profile, layout=layout, deadline=deadline)
    expected = {row["filename"] for row in profile.record()["wheels"]}
    if set(archives) != expected:
        raise ProfileError("retained wheel set differs from the exact profile")
    aggregate = {"archive": max_archive_bytes, "expanded": max_unpacked_bytes, "seen": {}}
    for selected in environments.values():
        names = {row["filename"] for row in selected.record()["wheels"]}
        _verified_profile_wheels(
            selected, {name: archives[name] for name in names},
            max_archive_bytes=max_archive_bytes, max_unpacked_bytes=max_unpacked_bytes,
            max_metadata_bytes=max_metadata_bytes, limits=limits, deadline=deadline, aggregate=aggregate,
        )
    return environments


def verify_profile_wheels(
    profile: RuntimeProfile, archives: Mapping[str, BinaryIO], *,
    max_archive_bytes: int, max_unpacked_bytes: int, max_metadata_bytes: int,
    limits: WheelLimits | None = None, deadline: float | None = None,
) -> None:
    """Verify the exact complete retained set without extraction or installation.

    Limits are inclusive across the closure, except metadata's per-file limit.
    The caller owns seekable binary streams and must retain immutable custody
    through subsequent construction. This check creates no reusable admission
    receipt and proves no release, feed, approval, device or revocation policy.
    """
    _verified_profile_wheels(
        profile, archives, max_archive_bytes=max_archive_bytes,
        max_unpacked_bytes=max_unpacked_bytes, max_metadata_bytes=max_metadata_bytes,
        limits=limits, deadline=deadline,
    )


def profile_wheel_inputs(
    profile: RuntimeProfile, snapshot: WheelSnapshot, *,
    max_archive_bytes: int, max_unpacked_bytes: int, max_metadata_bytes: int,
    limits: WheelLimits | None = None, deadline: float,
) -> WheelInstallInputs:
    """Compose complete byte inspection with construction, never authenticate.

    Existing release/profile/feed owners must admit the selected record before
    using this data. Keep the snapshot's context open through installed health.
    """
    from installer_wheel_snapshot import WheelInstallInputs, WheelSnapshot

    if not isinstance(snapshot, WheelSnapshot):
        raise ProfileError("profile construction requires retained wheel custody")
    # Freeze the semantic input independently of a caller's mutable record.
    profile = validate_profile(
        profile.record(), max_tags=limits.max_tags if limits is not None else 10_000,
        deadline=deadline,
    )
    distributions = _verified_profile_wheels(
        profile, snapshot.archives, max_archive_bytes=max_archive_bytes,
        max_unpacked_bytes=max_unpacked_bytes, max_metadata_bytes=max_metadata_bytes,
        limits=limits, deadline=deadline,
    )
    result = WheelInstallInputs(
        snapshot,
        json.dumps(profile.record(), sort_keys=True, separators=(",", ":"), allow_nan=False),
        tuple(distributions),
    )
    result.verify()
    _check_deadline(deadline)
    return result


def _verified_profile_wheels(
    profile: RuntimeProfile, archives: Mapping[str, BinaryIO], *,
    max_archive_bytes: int, max_unpacked_bytes: int, max_metadata_bytes: int,
    limits: WheelLimits | None, deadline: float | None, aggregate: dict | None = None,
) -> list[tuple[str, str, str, str]]:
    if any(type(value) is not int or value <= 0 for value in (
        max_archive_bytes, max_unpacked_bytes, max_metadata_bytes,
    )):
        raise ProfileError("wheel verification requires positive byte budgets")
    try:
        deadline = time.monotonic() + 120 if deadline is None else deadline
        _check_deadline(deadline)
        record = validate_profile(
            profile.record(), max_tags=limits.max_tags if limits is not None else 10_000,
            deadline=deadline,
        ).record()
        expected = {wheel["filename"]: wheel for wheel in record["wheels"]}
        if set(archives) != set(expected):
            raise ProfileError("retained wheel set differs from the exact profile")
        remaining_archive = max_archive_bytes
        remaining_unpacked = max_unpacked_bytes
        distributions = []
        owners = {}
        directory_owners = {}
        manager_directories = set()
        manager = "copilot-extensions-worktree-manager"
        check_ownership = any(
            canonicalize_name(wheel["name"]) == manager for wheel in expected.values()
        )
        for filename, wheel in expected.items():
            archive = archives[filename]
            archive_limit = min(
                remaining_archive, limits.max_archive_bytes if limits is not None else remaining_archive,
            )
            new_wheel = aggregate is not None and filename not in aggregate["seen"]
            if new_wheel:
                archive_limit = min(archive_limit, aggregate["archive"])
            digest, size = _archive_digest(archive, archive_limit, deadline)
            if digest != wheel["sha256"]:
                raise ProfileError("wheel archive digest differs from the exact profile")
            if remaining_unpacked <= 0:
                raise ProfileError("wheel members exceed the supplied byte budget")
            archive.seek(0)
            expanded_limit = (
                min(remaining_unpacked, aggregate["expanded"]) if new_wheel else remaining_unpacked
            )
            if expanded_limit <= 0:
                raise ProfileError("wheel members exceed the aggregate byte budget")
            facts = inspect_wheel_facts(
                archive, wheel, max_unpacked_bytes=expanded_limit,
                max_archive_bytes=archive_limit,
                max_metadata_bytes=max_metadata_bytes, limits=limits, deadline=deadline,
            )
            metadata, unpacked = facts.metadata, facts.unpacked_size
            wheel_is_manager = canonicalize_name(wheel["name"]) == manager
            environment = record["target"]["environment"]
            case_insensitive = environment["os_name"] == "nt" or environment["sys_platform"] == "darwin"
            site_packages = (
                "Lib/site-packages/" if environment["os_name"] == "nt"
                else f"lib/python{environment['python_version']}/site-packages/"
            )
            for member in (*facts.member_hashes, facts.prefix + "RECORD") if check_ownership else ():
                _check_deadline(deadline)
                parts = member.split("/")
                if len(parts) > 2 and parts[0].endswith(".data"):
                    relocated = "/".join(parts[2:])
                    if parts[1] in {"purelib", "platlib"}:
                        member = relocated
                    elif parts[1] == "data":
                        prefix = site_packages.casefold() if case_insensitive else site_packages
                        path = relocated.casefold() if case_insensitive else relocated
                        if path.startswith(prefix):
                            member = relocated[len(site_packages):]
                key = member.casefold() if case_insensitive else member
                parents = []
                parent = key
                while "/" in parent:
                    _check_deadline(deadline)
                    parent = parent.rsplit("/", 1)[0]
                    parents.append(parent)
                previous = owners.get(key)
                if previous is None or not (
                    wheel_is_manager or canonicalize_name(previous) == manager
                ):
                    previous = None
                    for parent in parents:
                        _check_deadline(deadline)
                        ancestor = owners.get(parent)
                        if ancestor is not None and (
                            wheel_is_manager or canonicalize_name(ancestor) == manager
                        ):
                            previous = ancestor
                            break
                if previous is None and key in directory_owners:
                    if wheel_is_manager:
                        previous = directory_owners[key]
                    elif key in manager_directories:
                        previous = manager
                if previous is not None:
                    raise ProfileError(
                        f"wheel payload ownership collision: {member} belongs to {previous} and {wheel['name']}"
                    )
                owners[key] = wheel["name"]
                for parent in parents:
                    _check_deadline(deadline)
                    directory_owners.setdefault(parent, wheel["name"])
                    if wheel_is_manager:
                        manager_directories.add(parent)
            requires_python = metadata.get_all("Requires-Python", [])
            if (
                requires_python != ([wheel["requires_python"]] if wheel["requires_python"] else [])
                or metadata.get_all("Provides-Extra", []) != wheel["provides_extras"]
                or metadata.get_all("Requires-Dist", []) != wheel["requires_dist"]
            ):
                raise ProfileError("actual wheel dependency metadata differs from the exact profile")
            after_digest, after_size = _archive_digest(archive, size, deadline)
            if (after_digest, after_size) != (digest, size):
                raise ProfileError("wheel archive changed during verification")
            if aggregate is not None:
                observed = (digest, size, unpacked)
                if new_wheel:
                    aggregate["archive"] -= size
                    aggregate["expanded"] -= unpacked
                    aggregate["seen"][filename] = observed
                elif aggregate["seen"][filename] != observed:
                    raise ProfileError("shared wheel changed between environments")
            remaining_archive -= size
            remaining_unpacked -= unpacked
            distributions.append((
                canonicalize_name(wheel["name"]), str(metadata["Version"]), filename, digest,
            ))
        _check_deadline(deadline)
        return distributions
    except (OSError, ValueError, TypeError, PromotionArtifactError) as exc:
        if isinstance(exc, ProfileError):
            raise
        raise ProfileError("retained wheel inspection failed") from exc
