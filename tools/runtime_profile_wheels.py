"""Compare retained wheel bytes with an exact profile; never approve or acquire."""
from __future__ import annotations

import hashlib
from typing import BinaryIO, Mapping

from promotion_python_artifacts import PromotionArtifactError, inspect_wheel
from runtime_profile import ProfileError, RuntimeProfile, validate_profile


def _archive_digest(archive: BinaryIO, limit: int) -> tuple[str, int]:
    archive.seek(0)
    digest = hashlib.sha256()
    size = 0
    while chunk := archive.read(min(1 << 20, limit - size + 1)):
        size += len(chunk)
        if size > limit:
            raise ProfileError("wheel archives exceed the supplied byte budget")
        digest.update(chunk)
    return "sha256:" + digest.hexdigest(), size


def verify_profile_wheels(
    profile: RuntimeProfile, archives: Mapping[str, BinaryIO], *,
    max_archive_bytes: int, max_unpacked_bytes: int, max_metadata_bytes: int,
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
    record = validate_profile(profile.record()).record()
    expected = {wheel["filename"]: wheel for wheel in record["wheels"]}
    if set(archives) != set(expected):
        raise ProfileError("retained wheel set differs from the exact profile")
    remaining_archive = max_archive_bytes
    remaining_unpacked = max_unpacked_bytes
    try:
        for filename, wheel in expected.items():
            archive = archives[filename]
            digest, size = _archive_digest(archive, remaining_archive)
            if digest != wheel["sha256"]:
                raise ProfileError("wheel archive digest differs from the exact profile")
            if remaining_unpacked <= 0:
                raise ProfileError("wheel members exceed the supplied byte budget")
            archive.seek(0)
            metadata, unpacked = inspect_wheel(
                archive, wheel, max_unpacked_bytes=remaining_unpacked,
                max_metadata_bytes=max_metadata_bytes,
            )
            requires_python = metadata.get_all("Requires-Python", [])
            if (
                requires_python != ([wheel["requires_python"]] if wheel["requires_python"] else [])
                or metadata.get_all("Provides-Extra", []) != wheel["provides_extras"]
                or metadata.get_all("Requires-Dist", []) != wheel["requires_dist"]
            ):
                raise ProfileError("actual wheel dependency metadata differs from the exact profile")
            after_digest, after_size = _archive_digest(archive, size)
            if (after_digest, after_size) != (digest, size):
                raise ProfileError("wheel archive changed during verification")
            remaining_archive -= size
            remaining_unpacked -= unpacked
    except (OSError, ValueError, TypeError, PromotionArtifactError) as exc:
        if isinstance(exc, ProfileError):
            raise
        raise ProfileError("retained wheel inspection failed") from exc
