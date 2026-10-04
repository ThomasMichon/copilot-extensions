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
   exposes). This DOES update the branch's ref/HEAD (a rebase) -- "never
   destructive" here means never removing the worktree directory or its
   branch, never force-pushing, never touching permissions/trustedFolders,
   not "never changes anything at all." A dirty tree or a genuine conflict
   stops the sync with the same clear message ``sync_forward`` already
   gives, and ``pause`` aborts immediately without touching the claim
   ledger at all -- a failed sync must never still persist a claim
   transition. **Known scope limit**: ``sync_forward`` resolves the
   worktree path via ``tracking.resolve_worktree_path``, which (like
   ``_resolve_worktree_id``'s short-ID lookup) reads the **ambient**
   ``cfg.tracking_dir()`` rather than the supplied ``config``'s project --
   the same pre-existing, shared limitation every other multi-project
   verb here has (``finalize``, ``git sync``, ...), not something this
   module introduces or can safely fix in isolation without touching that
   shared resolution path for every caller. A ``--config <other-project>``
   invocation is therefore only as cross-project-safe for the sync step as
   those other verbs already are; the claim-ledger access *after* the sync
   (point 2 below) is what this module scopes correctly via
   ``cfg.tracking_dir(config.repo_name)``.
2. Auto-settle this worktree's own claims that are **provably** resolved
   -- reusing the same gone+safe reclaim sweep the obligation gate's
   self-heal path is built on, scoped to just this one record rather than
   the fleet-wide ``claims sweep``. Provider/lease probes run *before* the
   record lock is taken (mirroring ``claims_cli._claims_sweep``'s own
   verdicts-first pattern) -- a probe can take tens of seconds, and this
   record's lock must never block an unrelated claim writer for that long.
   The preview read and its paired ``stat()`` are themselves taken under
   one short, immediately-released lock acquisition -- without it, a
   writer could replace the file in the gap between an unlocked
   ``load_record()`` and the following ``stat()``, leaving the preview
   content paired with a *newer* file's stat and defeating the staleness
   check below by construction. The cached verdicts are then fenced
   against that paired record file's own ``(mtime_ns, size)``: if another
   process writes the record at all between the preview and the main
   lock, the sweep is skipped entirely for this pass (a per-claim fence
   keyed on a single field -- e.g. ``created_at``, whose ``_now_iso()``
   stamp has only one-second resolution -- cannot reliably distinguish
   a released and immediately re-added claim sharing the same ref, kind, and
   timestamp from the original). A later ``pause`` retries with a fresh
   preview. A settled claim is recorded into the durable claim-history
   ledger (tagged with the owning record's own project, never the
   ambient one) the same way the fleet-wide sweep does, so ``claims
   history <ref>`` reflects
   it immediately.
3. Report every claim that remains genuinely open -- and stop there.
   Unlike ``finalize``, this is never an error: ``pause`` never requires
   ``--abandon``, never demands a ``--handoff-to`` recipient, and never
   removes the worktree directory or branch. The operator (or the agent
   driving this CLI) reads the report and decides, claim by claim, via the
   existing ``claims settle``/``claims release`` commands -- ``pause`` only
   tidies and reports; it never forces a disposition on a claim it can't
   prove is already safe, and an empty remaining-claims report is not by
   itself a guarantee that ``finalize`` will now succeed (finalize also
   checks claim-handoff bundles, provider/PR state, and whether the
   branch's content actually landed upstream).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import claim_history, git_collab, sweep, tracking
from . import config as cfg
from . import obligations as ob


@dataclass
class PauseResult:
    worktree_id: str
    synced: bool
    settled: list[dict[str, str]] = field(default_factory=list)
    remaining: list[dict[str, str]] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        """True when no unsettled, non-session claim remains open.

        Not itself a guarantee that ``finalize`` will now succeed -- see
        the module docstring's point 3.
        """
        return not self.remaining


def pause_worktree(
    worktree_id: str,
    config: cfg.Config,
    *,
    dry_run: bool = False,
) -> PauseResult:
    """Sync, auto-settle what's provably done, and report what's still open.

    Never raises on a remaining open claim and never removes the worktree's
    directory or branch -- see the module docstring for the full contract.
    A sync failure (dirty tree, conflict, missing upstream) is reported via
    ``synced=False`` and returns immediately, before any claim-ledger
    access at all -- a failed sync must never still persist a claim
    transition.
    """
    synced = git_collab.sync_forward(worktree_id, config, dry_run=dry_run)
    if not synced:
        return PauseResult(worktree_id=worktree_id, synced=False)

    tracking_dir = cfg.tracking_dir(getattr(config, "repo_name", None))
    yaml_path = tracking_dir / f"{worktree_id}.yaml"

    if dry_run:
        # A dry run must not mutate the claim ledger either -- report the
        # current unsettled set as a preview of what a real run would be
        # left deciding on, without running the reclaim sweep at all.
        remaining: list[dict[str, str]] = []
        if yaml_path.exists():
            record = tracking.load_record(yaml_path)
            remaining = _describe_unsettled(record)
        return PauseResult(worktree_id=worktree_id, synced=True, remaining=remaining)

    if not yaml_path.exists():
        return PauseResult(worktree_id=worktree_id, synced=True)

    # Compute gone/safe verdicts OUTSIDE the record lock -- a provider/lease
    # probe (e.g. a `gh`/`az` call per claim) can take tens of seconds, and
    # this record's lock must never block an unrelated claim writer for
    # that long (mirrors claims_cli._claims_sweep's own verdicts-first
    # pattern, which this module reuses rather than duplicates).
    #
    # Take the SAME short record lock around the preview read and its
    # paired stat, then release it immediately: without this, a writer
    # could replace the file between an unlocked `load_record()` and the
    # following `stat()`, leaving `preview` describing the OLD content
    # while `preview_stat` already describes the NEW file -- the later
    # staleness check would then wrongly see "unchanged" and apply a
    # cached verdict to a claim that was, in fact, rewritten underneath
    # the preview.
    with tracking._RecordLock(yaml_path, require_sidecar=True):
        preview = tracking.load_record(yaml_path)
        preview_stat = yaml_path.stat()
    gone_of, safe_of = sweep.make_resolvers(config)
    verdicts: dict[str, tuple[bool | None, bool | None]] = {}
    for claim in preview.resources:
        if not claim.is_unsettled or tracking.claim_handoff_reservation(preview, claim):
            continue
        try:
            gone = gone_of(claim)
        except Exception:
            gone = None
        try:
            safe = safe_of(claim)
        except Exception:
            safe = None
        verdicts[claim.ref] = (gone, safe)

    def _gone(claim: tracking.ResourceClaim) -> bool | None:
        return verdicts.get(claim.ref, (None, None))[0]

    def _safe(claim: tracking.ResourceClaim) -> bool | None:
        return verdicts.get(claim.ref, (None, None))[1]

    with tracking._RecordLock(yaml_path, require_sidecar=True):
        # Fence the cached verdicts against the record FILE's own identity
        # (mtime_ns, size), not a single claim field: between the preview
        # read above and this lock, another process could have released
        # and re-added the exact same `ref` as a genuinely different
        # incarnation (same kind/state, even the same one-second
        # `_now_iso()` timestamp) -- a per-claim fence on any one field
        # cannot reliably catch that. If the file changed at all, skip the
        # reclaim sweep entirely for this pass rather than risk applying a
        # stale verdict to a claim that changed identity underneath it; a
        # later `pause` retries with a fresh preview.
        current_stat = yaml_path.stat()
        stale = (
            current_stat.st_mtime_ns != preview_stat.st_mtime_ns
            or current_stat.st_size != preview_stat.st_size
        )
        record = tracking.load_record(yaml_path)
        if stale:
            flipped: list[tracking.ResourceClaim] = []
        else:
            flipped = tracking.sweep_abandoned_obligations(
                record, gone_of=_gone, safe_of=_safe, save=False,
            )
        if flipped:
            tracking.save_record(record, yaml_path)
            # Append immediately, still inside this record's own lock,
            # using THIS record's own machine (never the ambient config's)
            # -- only after the save is confirmed, so a persisted
            # transition is never recorded as history before it's durable.
            for c in flipped:
                claim_history.record_event(
                    kind=c.kind, ref=c.ref, worktree_id=record.worktree_id,
                    machine=record.machine, event="released",
                    note="merged" if c.state == ob.RELEASED else "abandoned",
                    project=record.repo,  # THIS record's own project, never ambient
                )
        settled = [{"kind": c.kind, "ref": c.ref, "state": c.state} for c in flipped]
        remaining = _describe_unsettled(record)

    return PauseResult(
        worktree_id=worktree_id, synced=True, settled=settled, remaining=remaining,
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
