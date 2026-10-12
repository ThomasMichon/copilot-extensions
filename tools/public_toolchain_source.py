"""Explicit public-runner build-toolchain policy, never a runtime feed policy."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from governed_feed_trust import ArtifactBuildError, _opaque_index_identity
from seasoned_public_feed import SeasonedVersionError, resolve_seasoned_public_version


@dataclass(frozen=True)
class PublicToolchainSource:
    min_age_days: int
    packages: dict[str, str]

    @property
    def provenance(self) -> dict:
        return {
            "source_policy": "seasoned-public",
            "index": "https://pypi.org/simple",
            "min_age_days": self.min_age_days,
            "packages": dict(sorted(self.packages.items())),
        }

    def verify(self, packages: dict[str, str]) -> None:
        if packages != self.packages:
            raise ArtifactBuildError(
                "public toolchain installed versions do not match seasoned pins "
                f"{self.packages!r}; got {packages!r}"
            )


def resolve_public_source(
    packages: tuple[str, ...], min_age_days: int
) -> PublicToolchainSource:
    now = datetime.now(timezone.utc)
    try:
        pins = {
            name: resolve_seasoned_public_version(name, min_age_days=min_age_days, now=now)
            for name in packages
        }
    except SeasonedVersionError as exc:
        raise ArtifactBuildError(f"could not resolve seasoned public toolchain: {exc}") from exc
    return PublicToolchainSource(min_age_days, pins)


def source_provenance(source: str | PublicToolchainSource) -> dict:
    if isinstance(source, PublicToolchainSource):
        return source.provenance
    return {"validated_index_identity": _opaque_index_identity(source)}
