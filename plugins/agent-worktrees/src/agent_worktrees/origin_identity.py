"""Private originating-worktree identity captured at PR publication."""

from __future__ import annotations

import logging
from typing import TypedDict

from . import config as cfg
from . import root_chain, tracking

log = logging.getLogger("agent-worktrees")


class OriginIdentity(TypedDict):
    worktree_id: str
    machine: str
    project: str


def resolve_origin_identity(
    record: tracking.WorktreeRecord, *, project: str, this_machine: str,
) -> OriginIdentity | None:
    """Resolve a stable local root snapshot, respecting its current opt-out.

    Missing, remote, unsafe or changing chains omit the origin rather than
    guessing. Each pushed marker captures the chain at that publication;
    earlier ciphertext remains a historical snapshot after a handoff.
    """
    if not project or not this_machine:
        return None
    if not root_chain._is_safe_path_component(project) or (
        not root_chain._is_safe_path_component(record.worktree_id)
    ):
        log.warning("Private PR origin omitted: invalid source identity.")
        return None
    try:
        walked = root_chain._walk_to_root(
            record, project=project, this_machine=this_machine,
        )
        if walked is None:
            return None
        root, root_project, chain = walked
        fingerprint = root_chain._chain_identity_key(chain)
        if fingerprint is None or not root_project or root.machine != this_machine:
            return None
        root_config = cfg.load_project_config(root_project)
        if root_config.default_repo.pr.source_attribution not in (True, "codename"):
            return None
        current = root_chain._load_local_record(project, record.worktree_id)
        if current is None:
            return None
        checked = root_chain._walk_to_root(
            current, project=project, this_machine=this_machine,
        )
        if checked is None or root_chain._chain_identity_key(checked[2]) != fingerprint:
            log.warning("Private PR origin omitted: ownership changed during capture.")
            return None
        return OriginIdentity(
            worktree_id=root.worktree_id, machine=root.machine, project=root_project,
        )
    except (OSError, ValueError, AttributeError, TimeoutError):
        log.warning("Private PR origin omitted: ownership or policy unavailable.")
        return None
