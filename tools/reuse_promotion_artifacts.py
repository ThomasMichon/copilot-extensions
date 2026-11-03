"""Reuse a source's signed immutable composition without resolving another lock."""
from __future__ import annotations

from pathlib import Path

import artifact_release_discovery as discovery
from artifact_attestation import (
    INDEX_LIMIT as INDEX_LIMIT, INDEX_NAME as INDEX_NAME, INDEX_SCHEMA,
    PromotionArtifactError as PromotionArtifactError,
)
import publish_python_artifacts as publisher
from promotion_python_artifacts import (
    candidate_projects, validate_artifacts,
)

SCHEMA = INDEX_SCHEMA


def _download(
    directory: Path, *, repository: str, tag: str, name: str, asset: dict, limit: int,
) -> None:
    discovery.download(
        directory, repository=repository, tag=tag, name=name, asset=asset,
        limit=limit, runner=publisher._gh,
    )


def read_admitted_source_index(
    directory: Path, *, repository: str, dev_head: str, main_before: str,
    content_tree: str, repo: Path,
) -> tuple[dict, dict[str, dict]] | None:
    """Authenticate a frozen composition without consuming any old bundle bytes."""
    import promote_release as promotion

    return discovery.read_admitted_source_index(
        directory, repository=repository, dev_head=dev_head, main_before=main_before,
        content_tree=content_tree,
        is_ancestor=lambda ancestor, descendant: promotion._is_ancestor(ancestor, descendant, cwd=repo),
        runner=publisher._gh, verify_subject=publisher.verify_attestation_subject,
    )


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
