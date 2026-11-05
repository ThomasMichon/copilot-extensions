"""Wheel semantics shared by runtime admission and first-party promotion.

Inspection proves byte consistency, never source, feed or release authority.
Packaging is supplied by the admitted tooling interpreter at installation time.
"""
from __future__ import annotations

from dataclasses import replace
from email.message import Message
from pathlib import Path
import re
import sys
import time
from typing import BinaryIO
import zipfile

from artifact_source_build import PromotionArtifactError, parse_wheel_identity

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "libs" / "installer-engine"))
from installer_wheel_inputs import WheelFacts, WheelLimits, bounded_tag, inspect_archive

_BUNDLE_LIMIT = 512 << 20
_MANIFEST_LIMIT = 1 << 20
_WHEEL_TAG_LIMIT = 10_000


def inspect_wheel_facts(
    archive: BinaryIO, wheel: dict, *, expected_generator: str | None = None,
    max_unpacked_bytes: int = _BUNDLE_LIMIT, max_metadata_bytes: int = _MANIFEST_LIMIT,
    max_archive_bytes: int = _BUNDLE_LIMIT,
    record_algorithms: frozenset[str] = frozenset({"sha256", "sha384", "sha512"}),
    limits: WheelLimits | None = None, deadline: float | None = None,
) -> WheelFacts:
    """Inspect actual bytes once; promotion may additionally bind their source."""
    if any(type(value) is not int or value <= 0 for value in (
        max_unpacked_bytes, max_metadata_bytes, max_archive_bytes,
    )):
        raise PromotionArtifactError("wheel inspection requires positive byte limits")
    if not record_algorithms or not record_algorithms <= {"sha256", "sha384", "sha512"}:
        raise PromotionArtifactError("unsupported wheel RECORD digest policy")
    info = parse_wheel_identity(Path(wheel["filename"]))
    prefix = f"{info['name']}-{info['version']}.dist-info/"
    try:
        deadline = time.monotonic() + 120 if deadline is None else deadline
        limits = limits or WheelLimits(
            max_archive_bytes=max_archive_bytes, max_expanded_bytes=max_unpacked_bytes,
            max_member_bytes=max_unpacked_bytes,
            max_metadata_bytes=max(max_archive_bytes, 3 * max_metadata_bytes),
            max_members=100_000, max_tags=_WHEEL_TAG_LIMIT,
            max_metadata_member_bytes=max_metadata_bytes,
        )
        facts = inspect_archive(
            archive, limits=replace(
                limits,
                max_archive_bytes=min(limits.max_archive_bytes, max_archive_bytes),
                max_expanded_bytes=min(limits.max_expanded_bytes, max_unpacked_bytes),
                max_member_bytes=min(limits.max_member_bytes, max_unpacked_bytes),
                max_metadata_member_bytes=min(limits.max_metadata_member_bytes, max_metadata_bytes),
            ), deadline=deadline, record_algorithms=record_algorithms,
        )
        canonical_tags, _ = bounded_tag(
            "-".join(info[key] for key in ("python_tag", "abi_tag", "platform_tag")),
            limit=limits.max_tags, deadline=deadline,
        )
        python_tags, abi_tags, platform_tags = canonical_tags.split("-")
        expected_tags = {
            f"{python}-{abi}-{platform}"
            for python in python_tags.split(".")
            for abi in abi_tags.split(".")
            for platform in platform_tags.split(".")
        }
        version_matches = facts.version == info["version"]
        if expected_generator is None:
            from packaging.version import Version

            version_matches = Version(facts.version) == Version(info["version"])
        if (
            facts.prefix != prefix
            or (
                expected_generator is not None
                and facts.wheel_metadata.get_all("Generator") != [expected_generator]
            )
            or set(facts.tags) != expected_tags
            or re.sub(r"[-_.]+", "_", facts.name).lower()
            != re.sub(r"[-_.]+", "_", info["name"]).lower()
            or not version_matches
        ):
            raise PromotionArtifactError("wheel metadata differs from declared source/toolchain")
        return facts
    except (
        OSError, UnicodeDecodeError, zipfile.BadZipFile, RuntimeError, NotImplementedError,
        ValueError,
    ) as exc:
        if isinstance(exc, PromotionArtifactError):
            raise
        raise PromotionArtifactError(f"{wheel['filename']}: invalid wheel archive: {exc}") from exc


def inspect_wheel(
    archive: BinaryIO, wheel: dict, *, expected_generator: str | None = None,
    max_unpacked_bytes: int = _BUNDLE_LIMIT, max_metadata_bytes: int = _MANIFEST_LIMIT,
    max_archive_bytes: int = _BUNDLE_LIMIT,
    record_algorithms: frozenset[str] = frozenset({"sha256", "sha384", "sha512"}),
    limits: WheelLimits | None = None, deadline: float | None = None,
) -> tuple[Message, int]:
    facts = inspect_wheel_facts(
        archive, wheel, expected_generator=expected_generator,
        max_unpacked_bytes=max_unpacked_bytes, max_metadata_bytes=max_metadata_bytes,
        max_archive_bytes=max_archive_bytes, record_algorithms=record_algorithms,
        limits=limits, deadline=deadline,
    )
    return facts.metadata, facts.unpacked_size
