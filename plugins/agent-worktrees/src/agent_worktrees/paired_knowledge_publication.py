"""Publish reciprocal pair claims before composing the harness overlay."""

from __future__ import annotations

import sys
from typing import Any

from . import config as cfg, tracking


def stamp_and_compose(
    config: cfg.Config,
    record: tracking.WorktreeRecord,
    worktree_path: str,
    pair_stamp: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Persist a new pair before composing its launch-time plugin overlay."""
    if not pair_stamp:
        return None
    record.pair_id = pair_stamp.get("pair_id")
    record.pair_role = pair_stamp.get("pair_role")
    record.pair_ref = pair_stamp.get("pair_ref")
    record.pair_kind = pair_stamp.get("pair_kind")
    from . import paired_claims
    paired_claims.ensure_claim(record, save=False)
    tracking.save_record(record)

    from . import knowledge_plugins

    try:
        return knowledge_plugins.compose_from_pair(cwd=worktree_path, config=config)
    except knowledge_plugins.KnowledgePluginError as exc:
        # Preserve the durable pair; normal launch preflight retries composition.
        print(
            f"paired-knowledge plugin composition failed; the launch preflight will retry: {exc}",
            file=sys.stderr,
        )
        return {"action": "error", "error": str(exc)}
