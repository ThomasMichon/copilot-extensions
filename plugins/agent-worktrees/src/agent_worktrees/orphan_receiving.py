"""Receiving-source custody for orphan reclamation, independent of old labels."""

from __future__ import annotations

from pathlib import Path

from . import config as cfg, record_reporting, tracking


def has_released_local_claim(entry: dict, config: cfg.Config) -> bool:
    """Require the actual receiving source and its completed claim disposition."""
    project, source = entry.get("project"), entry.get("source_worktree")
    if (
        not isinstance(project, str) or project != getattr(config, "repo_name", None)
        or cfg._PROJECT_NAME_RE.fullmatch(project) is None
        or not isinstance(source, str) or source in {"", ".", ".."}
        or Path(source).name != source or "/" in source or "\\" in source
    ):
        return False
    try:
        record = tracking.load_record_by_id(
            source, tracking_path=cfg.project_dir(project) / "worktrees",
        )
        if (
            record is None or record.repo != project or record.worktree_id != source
            or record.status != "finalized" or not record_reporting.locally_proven_subject(record)
        ):
            return False
        claims = [
            claim for claim in record.resources
            if (claim.kind, claim.ref) == (entry.get("kind"), entry.get("ref"))
        ]
        return len(claims) == 1 and (
            claims[0].state == "released" and not claims[0].metadata_opaque
            and not claims[0].has_producer and not claims[0].handoff_bundle
        )
    except (OSError, ValueError, RuntimeError, TypeError):
        return False
