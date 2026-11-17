"""Authenticate a prepublished version alias by its frozen development source."""
from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path

from promotion_control import ControlPreservationError, _blob, _git, _tree
from promotion_python_artifacts import PromotionArtifactError

_SHA = re.compile(r"[0-9a-f]{40}")
_STATE = ".github/release-pipeline-state.json"


def validate_prior_source_alias(
    *, repository: str, tag_commit: str, current_commit: str, current_index: dict,
    repo: Path, cache: dict[tuple[str, str], dict],
) -> None:
    """A prior failed Main merge is not a reason to replace an immutable alias."""
    try:
        _validate_prior_source_alias(
            repository=repository, tag_commit=tag_commit, current_commit=current_commit,
            current_index=current_index, repo=repo, cache=cache,
        )
    except (ControlPreservationError, UnicodeError, json.JSONDecodeError) as error:
        raise PromotionArtifactError(f"cannot authenticate prior source alias: {error}") from error


def _validate_prior_source_alias(
    *, repository: str, tag_commit: str, current_commit: str, current_index: dict,
    repo: Path, cache: dict[tuple[str, str], dict],
) -> None:
    import promote_release as promotion
    from reuse_promotion_artifacts import read_admitted_source_index

    parents = _git(repo, ["show", "-s", "--format=%P", tag_commit]).decode("ascii").split()
    current_parents = _git(repo, ["show", "-s", "--format=%P", current_commit]).decode("ascii").split()
    state_entry = _tree(repo, tag_commit).get(_STATE)
    if len(parents) != 1 or len(current_parents) != 1 or state_entry is None:
        raise PromotionArtifactError("version alias has no single-parent source candidate provenance")
    state = json.loads(_blob(repo, state_entry, limit=1 << 20))
    previous = state.get("last_promotion") if isinstance(state, dict) else None
    if (
        not isinstance(state, dict) or state.get("schema") != "copilot-extensions.release-pipeline-state"
        or type(state.get("version")) is not int or state["version"] != 1
        or not isinstance(previous, dict)
        or not isinstance(previous.get("dev_head"), str) or not _SHA.fullmatch(previous["dev_head"])
        or previous.get("release_id") != previous["dev_head"]
        or previous.get("tag") != f"promote-dev-{previous['dev_head']}"
        or previous.get("main_before") != parents[0]
        or not isinstance(current_index.get("dev_head"), str)
        or not _SHA.fullmatch(current_index["dev_head"])
        or not promotion._is_ancestor(previous["dev_head"], current_index["dev_head"], cwd=repo)
        or not promotion._is_ancestor(parents[0], current_parents[0], cwd=repo)
    ):
        raise PromotionArtifactError("version alias does not belong to the admitted development history")
    dev = previous["dev_head"]
    content_tree = promotion._tree_excluding_state(
        promotion._git(["rev-parse", f"{tag_commit}^{{tree}}"], cwd=repo), repo=repo,
    )
    key = repository, dev
    if key not in cache:
        with tempfile.TemporaryDirectory(prefix="prior-source-artifact-index-") as temporary:
            admitted = read_admitted_source_index(
                Path(temporary), repository=repository, dev_head=dev,
                main_before=current_parents[0], content_tree=content_tree, repo=repo,
            )
        if admitted is None:
            raise PromotionArtifactError("version alias has no authenticated frozen development index")
        cache[key] = admitted[0]
    admitted_index = cache[key]
    if admitted_index.get("dev_head") != dev or admitted_index.get("content_tree") != content_tree:
        raise PromotionArtifactError("version alias composition differs from its frozen source index")
