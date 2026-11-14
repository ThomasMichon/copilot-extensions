"""Receiving-source custody for orphan reclamation, independent of old labels."""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
from pathlib import Path
from collections.abc import Iterator

from . import config as cfg, record_reporting, tracking


def _source_path(entry: dict, config: cfg.Config) -> Path | None:
    project, source = entry.get("project"), entry.get("source_worktree")
    if (
        not isinstance(project, str) or project != getattr(config, "repo_name", None)
        or cfg._PROJECT_NAME_RE.fullmatch(project) is None
        or not isinstance(source, str) or source in {"", ".", ".."}
        or Path(source).name != source or "/" in source or "\\" in source
    ):
        return None
    return cfg.project_dir(project) / "worktrees" / f"{source}.yaml"


def has_released_local_claim(entry: dict, config: cfg.Config) -> bool:
    """Require the actual receiving source and its completed claim disposition."""
    try:
        path = _source_path(entry, config)
        if path is None:
            return False
        record = tracking.load_record_by_id(
            path.stem, tracking_path=path.parent,
        )
        if (
            record is None or record.repo != entry["project"] or record.worktree_id != entry["source_worktree"]
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


@contextmanager
def claim_guard(entry: dict, config: cfg.Config, *, apply: bool) -> Iterator[bool]:
    """Pin fresh custody through bounded disposal and orphan settlement.

    This is a cleanup exclusion, not an ordinary metadata read/write hold.
    No persistent freeze or new cooperation protocol is required.
    """
    if not apply:
        yield has_released_local_claim(entry, config)
        return
    with ExitStack() as stack:
        try:
            path = _source_path(entry, config)
            lock = (
                stack.enter_context(tracking._RecordLock(path, blocking=False, require_sidecar=True))
                if path is not None and path.is_file() and not path.is_symlink() else None
            )
        except (OSError, ValueError, RuntimeError, TypeError):
            lock = None
        yield lock is not None and lock.acquired and has_released_local_claim(entry, config)
