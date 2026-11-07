"""Version-preserving local composition through the canonical promotion owner."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
import re

import promote_release as promotion


@dataclass(frozen=True)
class LocalComposition:
    source_commit: str
    main_seed: str
    tree: str
    export_paths: tuple[str, ...] | None = None


def compose(
    repo: Path, *, source_commit: str, main_seed: str,
    select_inputs: Callable[[Path], tuple[str, ...]] | None = None,
) -> LocalComposition:
    """Compose committed inputs without allocating versions or publishing refs."""
    repo = repo.resolve(strict=True)
    for label, value in (("source", source_commit), ("Main seed", main_seed)):
        if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{40}", value) is None:
            raise promotion.PromotionError(f"Local promotion requires an exact {label} commit")
        if promotion._rev_parse(f"{value}^{{commit}}", cwd=repo) != value:
            raise promotion.PromotionError(f"Local promotion cannot resolve the exact {label} commit")
    scratch = promotion.add_scratch_worktree(source_commit, repo=repo)
    try:
        summary = promotion.consume_pending_changes(
            scratch, main_head=main_seed, repo=repo,
            trusted_source=promotion.REPO, allocate_versions=False,
        )
        tree = promotion.build_promotion_tree(
            scratch, control=summary.get("control_preserved"),
        )
        paths = select_inputs(scratch) if select_inputs is not None else None
        return LocalComposition(source_commit, main_seed, tree, paths)
    finally:
        promotion.remove_scratch_worktree(scratch, repo=repo)
