"""``pause`` -- non-destructive worktree wrap-up, an alternative to
``finalize``'s all-or-nothing resource-obligation-settlement gate.

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
   record's lock must never block an unrelated claim writer for that long
   (the lock may never be held across network/git I/O at all -- see
   ``tracking._RecordLock``'s own scope invariant). Each cached verdict is
   fenced against a fingerprint of the exact claim it was computed for
   (every identity/state field: kind, ref, state, note, created_at,
   handoff_bundle, **and** ``ResourceClaim.revision`` -- bumped by
   :func:`tracking_claims.add_resource_claim` on every add/reactivate of a
   ``ref``) rather than a timestamp or a file-level stat -- a single
   in-memory read pairs the fingerprint with the claim deterministically
   (no separate ``stat()`` call to race against), and the ``revision``
   component specifically closes the ABA case a pure content comparison
   cannot: a release immediately followed by a re-add that restores
   byte-identical content still bumps ``revision``, so a stale verdict can
   never apply to that reactivated claim. A settled **``pr``-kind** claim is recorded into
   the durable claim-history ledger (tagged with the owning record's own
   project, never the ambient one) the same way the fleet-wide sweep
   does, so ``claims history <ref>`` reflects it immediately --
   ``claim_history.SUPPORTED_KINDS`` is ``pr``-only today, so a settled
   ``worktree``/``task``/``codespace``/``container`` claim flips in the
   YAML but is not (yet) recorded in that ledger, exactly as the
   fleet-wide sweep's own call site behaves.
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

import hashlib
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
    # that long, nor may the lock ever be held across network/git I/O at
    # all (mirrors claims_cli._claims_sweep's own verdicts-first pattern,
    # which this module reuses rather than duplicates).
    preview = tracking.load_record(yaml_path)
    gone_of, safe_of = sweep.make_resolvers(config)
    verdicts: dict[str, tuple[bool | None, bool | None, str]] = {}
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
        verdicts[claim.ref] = (gone, safe, _claim_fingerprint(claim))

    def _gone(claim: tracking.ResourceClaim) -> bool | None:
        cached = verdicts.get(claim.ref)
        if cached is None or cached[2] != _claim_fingerprint(claim):
            return None
        return cached[0]

    def _safe(claim: tracking.ResourceClaim) -> bool | None:
        cached = verdicts.get(claim.ref)
        if cached is None or cached[2] != _claim_fingerprint(claim):
            return None
        return cached[1]

    with tracking._RecordLock(yaml_path, require_sidecar=True):
        record = tracking.load_record(yaml_path)
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


def _claim_fingerprint(claim: tracking.ResourceClaim) -> str:
    """Fingerprint of every field that defines this claim's identity/state
    (kind, ref, state, note, created_at, handoff_bundle, revision).

    Deterministic and computed from a single in-memory read -- unlike a
    filesystem ``stat()``, there is no separate call to race against. The
    content fields alone are not quite enough: a release immediately
    followed by a re-add can restore byte-identical kind/ref/state/note/
    created_at (an ABA rewrite -- timestamps are only one-second resolution
    and some claims carry no timestamp at all), which a pure content
    fingerprint cannot distinguish from "nothing happened." Including
    ``revision`` closes that gap: :func:`tracking_claims.add_resource_claim`
    bumps it on every add/reactivate of a ``ref`` (never on a settle/
    release/sweep flip, which only ever makes a claim less live), so a
    release-and-re-add always changes it even when every other field
    round-trips to its original value.
    """
    raw = "\x00".join([
        claim.kind, claim.ref, claim.state, claim.note,
        claim.created_at, claim.handoff_bundle, str(claim.revision),
    ])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


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
