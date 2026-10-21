"""Publish and read back an immutable candidate set before enabling promotion merge."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import publish_python_artifacts as publisher
from promotion_python_artifacts import INDEX_NAME, PromotionArtifactError


def _expected(directory: Path, index: dict) -> dict[str, str]:
    return {
        INDEX_NAME: hashlib.sha256((directory / INDEX_NAME).read_bytes()).hexdigest(),
        **{entry["filename"]: entry["sha256"] for entry in index["artifacts"]},
    }


def _read_release(repository: str, tag: str) -> dict | None:
    result = publisher._gh(
        ["api", f"repos/{repository}/releases/tags/{tag}"],
        verification=True, timeout=120,
    )
    if publisher._is_not_found(result):
        return None
    release = publisher._release(result, tag)
    if (
        release.get("prerelease") is not True
        or type(release.get("id")) is not int or release["id"] <= 0
    ):
        raise PromotionArtifactError(f"{tag}: invalid candidate release")
    return release


def _assets(repository: str, release: dict, expected: dict[str, str]) -> dict[str, dict]:
    result = publisher._gh(
        ["api", "--paginate", "--slurp",
         f"repos/{repository}/releases/{release['id']}/assets?per_page=100"],
        verification=True, timeout=120,
    )
    try:
        pages = json.loads(publisher._check(result))
        if not isinstance(pages, list) or not all(isinstance(page, list) for page in pages):
            raise ValueError("invalid asset pages")
        assets = {}
        for page in pages:
            for asset in page:
                name = asset["name"]
                if (
                    name not in expected or name in assets
                    or asset.get("digest") != f"sha256:{expected[name]}"
                    or asset.get("state") != "uploaded"
                    or type(asset.get("id")) is not int or asset["id"] <= 0
                ):
                    raise ValueError("unexpected, incomplete or conflicting immutable asset")
                assets[name] = asset
        return assets
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise PromotionArtifactError(f"{release['tag_name']}: invalid readiness assets: {exc}") from exc


def _verify_downloads(
    repository: str, tag: str, assets: dict[str, dict], expected: dict[str, str],
) -> None:
    if assets.keys() != expected.keys():
        raise PromotionArtifactError(f"{tag}: candidate artifact publication is incomplete")
    publisher.verify_asset_downloads(repository, [
        (f"{tag}/{name}", assets[name]["id"], digest) for name, digest in expected.items()
    ])


def _validated_set(
    directory: Path, *, candidate_commit: str, dev_head: str, repo: Path,
) -> tuple[str, str, dict[str, str]]:
    index = publisher.validate_commit_artifacts(
        directory, commit=candidate_commit, dev_head=dev_head, repo=repo,
    )
    target = dev_head if index["schema_version"] == 2 else candidate_commit
    return f"python-artifacts-{target}", target, _expected(directory, index)


def prepare_artifacts(
    directory: Path, *, repository: str, candidate_commit: str, dev_head: str, repo: Path,
) -> str:
    """Only a complete set readable with consumer authority authorizes merge."""
    tag, source, expected = _validated_set(
        directory, candidate_commit=candidate_commit, dev_head=dev_head, repo=repo,
    )
    release = _read_release(repository, tag)
    target = publisher._tag_commit(repository, tag)
    if target is not None and target != source:
        raise PromotionArtifactError(f"{tag}: candidate tag points to a different commit")
    if release is None:
        publisher._check(publisher._gh([
            "release", "create", tag, "--repo", repository, "--target", source,
            "--prerelease", "--latest=false", "--title", tag, "--notes",
            "Attested candidate inputs, not a promoted source release or dependency-policy approval. "
            "Consume only after independently verifying the selected source and artifact provenance.",
        ], timeout=120))
        release = _read_release(repository, tag)
    if release is None or publisher._tag_commit(repository, tag) != source:
        raise PromotionArtifactError(f"{tag}: candidate release/tag creation was not verified")
    assets = _assets(repository, release, expected)
    missing = [str(directory / name) for name in expected if name not in assets]
    if missing:
        publisher._check(publisher._gh([
            "release", "upload", tag, "--repo", repository, *missing,
        ], timeout=120))
    return verify_ready_artifacts(
        directory, repository=repository, candidate_commit=candidate_commit,
        dev_head=dev_head, repo=repo,
    )


def verify_ready_artifacts(
    directory: Path, *, repository: str, candidate_commit: str, dev_head: str, repo: Path,
) -> str:
    """Read-only verification; never repair a readiness failure after source merge."""
    tag, source, expected = _validated_set(
        directory, candidate_commit=candidate_commit, dev_head=dev_head, repo=repo,
    )
    release = _read_release(repository, tag)
    if release is None or publisher._tag_commit(repository, tag) != source:
        raise PromotionArtifactError(f"{tag}: candidate release is unavailable or misbound")
    _verify_downloads(repository, tag, _assets(repository, release, expected), expected)
    return tag
