"""``pause`` -- non-destructive worktree wrap-up (requested alongside the
resource-obligation-settlement finalize gate: see issue #677 in a
consuming harness repo).

``finalize`` is intentionally all-or-nothing on its obligation gate: a
worktree with any unsettled outbound claim either settles every one of
them first, or the operator passes ``--abandon --handoff-to <recipient>``
to re-home the whole set elsewhere. Neither path fits the very common
shape of "sync and tidy everything that's actually done, but I know this
one claim (e.g. a deliberate pending ``context-handoff`` task meant to
resume in this exact worktree) is still genuinely open -- leave it."

``pause`` is a distinct, non-destructive operation:

1. Sync the worktree branch forward onto the latest default branch
   (:func:`git_collab.sync_forward` -- the same "pull forward / build on
   top of a just-merged PR" primitive ``agent-worktrees git sync`` already
   exposes). A dirty tree or a genuine conflict stops it with the same
   clear message ``sync_forward`` already gives; ``pause`` never guesses
   past either.
2. Auto-settle this worktree's own claims that are **provably** resolved
   -- reusing the same gone+safe reclaim sweep the obligation gate's
   self-heal path is built on (:func:`sweep.self_heal`), scoped to just
   this one record rather than the fleet-wide ``claims sweep``.
3. Report every claim that remains genuinely open -- and stop there.
   Unlike ``finalize``, this is never an error: ``pause`` never requires
   ``--abandon``, never demands a ``--handoff-to`` recipient, and never
   touches the worktree directory, branch, permissions, or trustedFolders
   entry. The operator (or the agent driving this CLI) reads the report
   and decides, claim by claim, via the existing ``claims settle``/
   ``claims release`` commands -- ``pause`` only tidies and reports, it
   never forces a disposition on a claim it can't prove is already safe.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from . import config as cfg, git_collab, output, sweep, tracking


@dataclass
class PauseResult:
    worktree_id: str
    synced: bool
    settled: list[dict[str, str]] = field(default_factory=list)
    remaining: list[dict[str, str]] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        """True when nothing remains open -- ``finalize`` would now succeed."""
        return not self.remaining


def pause_worktree(
    worktree_id: str,
    config: cfg.Config,
    *,
    dry_run: bool = False,
) -> PauseResult:
    """Sync, auto-settle what's provably done, and report what's still open.

    Never raises on a remaining open claim and never mutates the worktree's
    directory/branch/permissions -- see the module docstring for the full
    contract. A sync failure (dirty tree, conflict, missing upstream) is
    reported via ``synced=False``; the claim ledger is left untouched in
    that case so a retry after resolving the sync issue sees the same
    state.
    """
    synced = git_collab.sync_forward(worktree_id, config, dry_run=dry_run)

    settled: list[dict[str, str]] = []
    remaining: list[dict[str, str]] = []

    if dry_run:
        # A dry run must not mutate the claim ledger either -- report the
        # current unsettled set as a preview of what a real run would be
        # left deciding on, without running the reclaim sweep at all.
        yaml_path = cfg.tracking_dir() / f"{worktree_id}.yaml"
        if yaml_path.exists():
            record = tracking.load_record(yaml_path)
            remaining = _describe_unsettled(record)
        return PauseResult(
            worktree_id=worktree_id, synced=synced, settled=settled, remaining=remaining,
        )

    yaml_path = cfg.tracking_dir() / f"{worktree_id}.yaml"
    if not yaml_path.exists():
        return PauseResult(worktree_id=worktree_id, synced=synced)

    with tracking._RecordLock(yaml_path, require_sidecar=True):
        record = tracking.load_record(yaml_path)
        flipped = sweep.self_heal(record, config, path=yaml_path, save=True)
        settled = [{"kind": c.kind, "ref": c.ref, "state": c.state} for c in flipped]
        # self_heal persists on a real flip; reload is unnecessary since
        # `record` was mutated in place by sweep_abandoned_obligations.
        remaining = _describe_unsettled(record)

    return PauseResult(
        worktree_id=worktree_id, synced=synced, settled=settled, remaining=remaining,
    )


def _describe_unsettled(record: tracking.WorktreeRecord) -> list[dict[str, str]]:
    """The same unsettled-claim filter ``finalize``'s obligation gate uses
    (excluding ``session`` claims, which never block finalize on their own --
    see ``finalize._assert_obligations_settled``), rendered for reporting."""
    return [
        {
            "kind": c.kind,
            "ref": c.ref,
            "state": c.state,
            **({"note": c.note} if c.note else {}),
        }
        for c in record.resources
        if c.is_unsettled and c.kind != "session"
    ]
