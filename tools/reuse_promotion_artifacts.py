"""Reuse a source's signed immutable composition without resolving another lock."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import promotion_artifact_readiness as readiness
import publish_python_artifacts as publisher
from promotion_python_artifacts import (
    INDEX_NAME, SCHEMA, PromotionArtifactError, candidate_projects, validate_artifacts,
)

_BUNDLE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*-[0-9][A-Za-z0-9.+-]*-[0-9a-f]{64}\.zip")


def _download(
    directory: Path, *, repository: str, tag: str, name: str, asset: dict, limit: int,
) -> None:
    if type(asset.get("size")) is int and asset["size"] > limit:
        raise PromotionArtifactError(f"{tag}: source artifact exceeds its size limit")
    result = publisher._gh(
        ["api", f"repos/{repository}/releases/assets/{asset['id']}",
         "-H", "Accept: application/octet-stream"],
        verification=True, binary=True,
    )
    publisher._check(result)
    if (
        not isinstance(result.stdout, bytes) or len(result.stdout) > limit
        or "sha256:" + hashlib.sha256(result.stdout).hexdigest() != asset["digest"]
    ):
        raise PromotionArtifactError(f"{tag}: downloaded frozen artifact digest mismatch")
    (directory / name).write_bytes(result.stdout)


def read_admitted_source_index(
    directory: Path, *, repository: str, dev_head: str, main_before: str,
    content_tree: str, repo: Path,
) -> tuple[dict, dict[str, dict]] | None:
    """Authenticate a frozen composition without consuming any old bundle bytes."""
    import promote_release as promotion

    if (
        not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*/[A-Za-z0-9_.-]+", repository)
        or repository.rsplit("/", 1)[-1] in (".", "..")
        or any(not re.fullmatch(r"[0-9a-f]{40}", value)
               for value in (dev_head, main_before, content_tree))
    ):
        raise PromotionArtifactError("invalid source artifact reuse identity")
    tag = f"python-artifacts-{dev_head}"
    release = readiness._read_release(repository, tag)
    if release is None:
        return None
    if publisher._tag_commit(repository, tag) != dev_head:
        raise PromotionArtifactError(f"{tag}: source release tag is misbound")
    pages = json.loads(publisher._check(publisher._gh(
        ["api", "--paginate", "--slurp",
         f"repos/{repository}/releases/{release['id']}/assets?per_page=100"],
        verification=True,
    )))
    if not isinstance(pages, list) or any(
        not isinstance(page, list) or any(not isinstance(asset, dict) for asset in page)
        for page in pages
    ):
        raise PromotionArtifactError(f"{tag}: invalid source artifact pages")
    from promotion_release_evidence import MAX_EVIDENCE_ASSETS, supplementary_evidence_asset

    assets: dict[str, dict] = {}
    seen, evidence_count = set(), 0
    for page in pages:
        for asset in page:
            name = asset.get("name")
            if not isinstance(name, str) or name in seen:
                raise PromotionArtifactError(f"{tag}: duplicate or invalid source artifact")
            seen.add(name)
            if supplementary_evidence_asset(asset, dev_head):
                evidence_count += 1
                if evidence_count > MAX_EVIDENCE_ASSETS:
                    raise PromotionArtifactError(f"{tag}: too many supplementary evidence assets")
                continue
            if (
                not isinstance(name, str) or name in assets or "/" in name or "\\" in name
                or name in (".", "..") or len(name) > 255
                or (name != INDEX_NAME and not _BUNDLE.fullmatch(name))
                or type(asset.get("id")) is not int or asset["id"] <= 0
                or asset.get("state") != "uploaded"
                or not isinstance(asset.get("digest"), str)
                or not re.fullmatch(r"sha256:[0-9a-f]{64}", asset["digest"])
            ):
                raise PromotionArtifactError(f"{tag}: unsafe or incomplete source artifact")
            assets[name] = asset
    if INDEX_NAME not in assets or not 1 <= len(assets) <= 65:
        raise PromotionArtifactError(f"{tag}: frozen source artifact index is missing")

    _download(
        directory, repository=repository, tag=tag, name=INDEX_NAME,
        asset=assets[INDEX_NAME], limit=1 << 20,
    )
    index = json.loads((directory / INDEX_NAME).read_text(encoding="utf-8"))
    if (
        not isinstance(index, dict) or index.get("schema") != SCHEMA
        or type(index.get("schema_version")) is not int or index["schema_version"] != 2
        or "main_before" in index
        or index.get("dev_head") != dev_head or index.get("content_tree") != content_tree
        or not isinstance(index.get("artifacts"), list)
        or not 1 <= len(index["artifacts"]) <= 64
    ):
        raise PromotionArtifactError(f"{tag}: frozen source/composition differs from this candidate")
    control = index.get("admission")
    if (
        not isinstance(control, dict)
        or not isinstance(control.get("signer_digest"), str)
        or not re.fullmatch(r"[0-9a-f]{40}", control["signer_digest"])
        or control.get("source_digest") != control["signer_digest"]
        or control.get("source_ref") != "refs/heads/main"
        or not promotion._is_ancestor(control["signer_digest"], main_before, cwd=repo)
    ):
        raise PromotionArtifactError(f"{tag}: original artifact admission is not trusted Main history")
    publisher.verify_attestation_subject(
        directory / INDEX_NAME, repository=repository, signer_digest=control["signer_digest"],
        source_digest=control["source_digest"], source_ref=control["source_ref"],
    )
    expected = {INDEX_NAME}
    for entry in index["artifacts"]:
        if not isinstance(entry, dict):
            raise PromotionArtifactError(f"{tag}: invalid frozen artifact entry")
        name, digest = entry.get("filename"), entry.get("sha256")
        if (
            not isinstance(name, str) or name not in assets or name in expected
            or not name.endswith(".zip") or not isinstance(digest, str)
            or not re.fullmatch(r"[0-9a-f]{64}", digest)
            or assets[name]["digest"] != "sha256:" + digest
        ):
            raise PromotionArtifactError(f"{tag}: frozen artifact set is incomplete or conflicting")
        expected.add(name)
    if expected != assets.keys():
        raise PromotionArtifactError(f"{tag}: unexpected files in the frozen source artifact set")
    return index, assets


def reuse_artifacts(
    directory: Path, *, repository: str, dev_head: str, main_before: str,
    content_tree: str, candidate_root: Path, repo: Path,
    source_policy: str, min_age_days: int | None,
) -> dict | None:
    """Only genuine absence permits a new build; conflicts remain explicit failures."""
    admitted = read_admitted_source_index(
        directory, repository=repository, dev_head=dev_head, main_before=main_before,
        content_tree=content_tree, repo=repo,
    )
    if admitted is None:
        return None
    index, assets = admitted
    for entry in index["artifacts"]:
        _download(
            directory, repository=repository, tag=f"python-artifacts-{dev_head}",
            name=entry["filename"], asset=assets[entry["filename"]], limit=512 << 20,
        )
    control = index["admission"]
    publisher.verify_attestations(
        directory, repository=repository, signer_digest=control["signer_digest"],
        source_digest=control["source_digest"], source_ref=control["source_ref"],
    )
    return validate_artifacts(
        directory, dev_head=dev_head, main_before=main_before, content_tree=content_tree,
        candidate_root=candidate_root, projects=candidate_projects(candidate_root),
        source_policy=source_policy, min_age_days=min_age_days,
    )
