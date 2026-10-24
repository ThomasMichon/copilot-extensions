"""Compare retained wheel bytes with an exact profile; never approve or acquire."""
from __future__ import annotations

import hashlib
import time
from typing import BinaryIO, Mapping

from promotion_python_artifacts import PromotionArtifactError, inspect_wheel
from installer_wheel_inputs import WheelLimits, _check_deadline
from runtime_profile import ProfileError, RuntimeProfile, validate_profile


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
        for filename, wheel in expected.items():
            archive = archives[filename]
            archive_limit = min(
                remaining_archive, limits.max_archive_bytes if limits is not None else remaining_archive,
            )
            digest, size = _archive_digest(archive, archive_limit, deadline)
            if digest != wheel["sha256"]:
                raise ProfileError("wheel archive digest differs from the exact profile")
            if remaining_unpacked <= 0:
                raise ProfileError("wheel members exceed the supplied byte budget")
            archive.seek(0)
            metadata, unpacked = inspect_wheel(
                archive, wheel, max_unpacked_bytes=remaining_unpacked,
                max_archive_bytes=archive_limit,
                max_metadata_bytes=max_metadata_bytes, limits=limits, deadline=deadline,
            )
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
            remaining_archive -= size
            remaining_unpacked -= unpacked
        _check_deadline(deadline)
    except (OSError, ValueError, TypeError, PromotionArtifactError) as exc:
        if isinstance(exc, ProfileError):
            raise
        raise ProfileError("retained wheel inspection failed") from exc
