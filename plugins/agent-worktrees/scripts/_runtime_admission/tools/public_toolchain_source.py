"""Explicit public-runner build-toolchain policy, never a runtime feed policy."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from governed_feed_trust import ArtifactBuildError, _opaque_index_identity
from seasoned_public_feed import SeasonedVersionError, resolve_seasoned_public_release


@dataclass(frozen=True)
class PublicToolchainSource:
    min_age_days: int
    packages: dict[str, str]
    hashes: dict[str, tuple[str, ...]]

    @property
    def provenance(self) -> dict:
        return {
            "source_policy": "seasoned-public",
            "index": "https://pypi.org/simple",
            "min_age_days": self.min_age_days,
            "packages": dict(sorted(self.packages.items())),
            "file_hashes": {name: list(values) for name, values in sorted(self.hashes.items())},
        }

    def install_args(self, directory: Path) -> list[str]:
        requirements = directory / "public-toolchain-requirements.txt"
        requirements.write_text("\n".join(
            f"{name}=={version} " + " ".join(f"--hash=sha256:{digest}" for digest in self.hashes[name])
            for name, version in sorted(self.packages.items())
        ) + "\n", encoding="utf-8")
        return ["--require-hashes", "--requirements", str(requirements)]

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
        releases = {
            name: resolve_seasoned_public_release(name, min_age_days=min_age_days, now=now)
            for name in packages
        }
    except SeasonedVersionError as exc:
        raise ArtifactBuildError(f"could not resolve seasoned public toolchain: {exc}") from exc
    return PublicToolchainSource(
        min_age_days, {name: release.version for name, release in releases.items()},
        {name: release.hashes for name, release in releases.items()},
    )


def source_provenance(source: str | PublicToolchainSource) -> dict:
    if isinstance(source, PublicToolchainSource):
        return source.provenance
    return {"validated_index_identity": _opaque_index_identity(source)}


def validate_lock_provenance(provenance: dict | None, packages: dict[str, str]) -> None:
    if not isinstance(provenance, dict) or not isinstance(provenance.get("python"), str) or not provenance["python"]:
        raise ArtifactBuildError("schema-3 toolchain requires explicit interpreter provenance")
    policy = provenance.get("source_policy")
    if policy == "governed":
        identity = provenance.get("validated_index_identity")
        if not isinstance(identity, str) or not identity:
            raise ArtifactBuildError("governed toolchain requires validated index provenance")
        if provenance != {
            "source_policy": "governed", "python": provenance["python"],
            "validated_index_identity": identity,
        }:
            raise ArtifactBuildError("governed toolchain provenance must have its canonical shape")
    elif policy == "seasoned-public":
        age = provenance.get("min_age_days")
        hashes = provenance.get("file_hashes")
        if not isinstance(hashes, dict) or set(hashes) != set(packages) or any(
            not isinstance(values, list) or not values or any(
                not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest)
                for digest in values
            ) for values in hashes.values()
        ):
            raise ArtifactBuildError("public toolchain requires eligible file-hash provenance")
        if any(values != sorted(set(values)) for values in hashes.values()):
            raise ArtifactBuildError("public toolchain hash provenance must be sorted and unique")
        if type(age) is not int or age < 0 or provenance != {
            **PublicToolchainSource(age, packages, {name: tuple(values) for name, values in hashes.items()}).provenance,
            "python": provenance["python"],
        }:
            raise ArtifactBuildError("public toolchain provenance does not match its policy and pins")
    else:
        raise ArtifactBuildError("schema-3 toolchain requires explicit source-policy provenance")
