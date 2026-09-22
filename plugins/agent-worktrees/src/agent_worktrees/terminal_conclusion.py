"""Safely prime a disposable worker worktree for managed garbage collection.

This module deliberately does not remove worktrees. It concludes an exact
recorded session when the worker is already gone, reconciles a checkout only
when it has no valuable local work, and converts the tracking record into a
managed final state. The existing managed sweep remains the deletion authority
and re-checks liveness plus its idle grace before removing anything.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from . import config as cfg
from . import finalize, git_ops, sessions, tracking

DISPOSABLE_CLI_POLICY = "disposable-cli"
DISPATCH_ATTEMPT_POLICY = "dispatch-attempt"

def _normalized(path: str | Path) -> str:
    return os.path.normcase(os.path.normpath(str(path)))


def _session_is_live(record: tracking.WorktreeRecord) -> str | None:
    if sessions.has_mux_session(record.worktree_id):
        return "live-mux"
    context = sessions.scan_sessions_fast([record])
    worktree_path = _normalized(record.worktree_path)
    if any(_normalized(path) == worktree_path for path in context.active_sessions):
        return "live-session"
    return None


def _dirty_entries(worktree_path: Path) -> list[tuple[str, str]]:
    result = git_ops.git(
        "status",
        "--porcelain=v1",
        "-z",
        "--untracked-files=all",
        "--no-renames",
        cwd=worktree_path,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            result.stderr.strip() or "could not inspect the worker checkout"
        )
    entries: list[tuple[str, str]] = []
    for raw in result.stdout.split("\0"):
        if not raw:
            continue
        if len(raw) < 4:
            raise RuntimeError("could not parse worker checkout status")
        entries.append((raw[:2], raw[3:]))
    return entries


def _save_session_conclusion(
    record: tracking.WorktreeRecord,
    record_path: Path,
    session_id: str | None,
) -> tuple[dict[str, Any], bool]:
    if not session_id:
        return {"action": "skipped", "reason": "session-unavailable"}, False
    entry = record.session_entry(session_id)
    if entry is None:
        return {
            "action": "skipped",
            "reason": "session-not-tracked",
            "session": session_id,
        }, False
    already = entry.state == "concluded"
    tracking.conclude_session(
        record,
        session_id,
        state="concluded",
        save=False,
    )
    tracking.save_record(record, record_path)
    return {
        "action": "already-concluded" if already else "concluded",
        "session": session_id,
    }, not already


def _dispatch_ownership_confirmed(
    record: tracking.WorktreeRecord,
    *,
    reservation_key: str | None,
    owner: str,
) -> bool:
    """Whether ``record``'s own ``dispatch_attempt`` provenance exactly
    matches the caller's claimed reservation/owner identity -- the same
    match the ``dispatch-attempt`` policy gate already requires before
    priming. Reused right before releasing this worktree's own ``session``
    claim(s) (see :func:`_release_dispatch_session_claims`) so that release
    is never granted on provenance the two-phase lock's unlocked
    git-inspection window could have let drift underneath -- defense in
    depth; the policy gate's own earlier check already enforces this for
    the ordinary path, this just avoids trusting a now-stale in-memory
    ``record`` a second time.
    """
    allocation = record.dispatch_attempt
    return (
        allocation is not None
        and reservation_key is not None
        and allocation.reservation_key == reservation_key
        and allocation.creator_machine.casefold() == record.machine.casefold()
        and allocation.driver == owner
    )


def _release_dispatch_session_claims(
    record: tracking.WorktreeRecord,
    *,
    session_id: str | None,
) -> list[tracking.ResourceClaim]:
    """Release the live ``session``-kind resource claim(s) for
    ``session_id`` specifically -- never every live session claim on the
    record, which could over-release an unrelated still-relevant claim
    (e.g. a distinct, separately-tracked session this same worktree also
    holds).

    Only called once the caller has confirmed (a) this is a
    ``dispatch-attempt`` conclusion whose reservation/attribution exactly
    matches the record's own ``dispatch_attempt``
    (:func:`_dispatch_ownership_confirmed`), and (b) the backstop liveness
    probe (:func:`_session_is_live`, itself built on the pid-lock/mux
    check -- never a query to agent-bridge) already found no live process
    for this worktree. Under those two conditions, agent-dispatch
    concluding this attempt (complete, abandoned, or a spawn attempt that
    definitively failed) IS the same kind of authoritative "we are done
    with this session" signal an operator's explicit ``finalize`` or
    handoff carries -- so its own outward ``session`` claim may be
    released rather than left wedged forever. A clean-exit ``sessionEnd``
    hook is the *only other* release path for a ``session`` claim
    (:func:`tracking.release_resource_claim`'s own docstring; the reclaim
    sweep in :mod:`sweep` deliberately has no gone-verdict resolver for
    ``session`` claims, by design -- an abnormal agent-bridge death alone
    must never look like a reason to reap a worktree we never declared
    done with, copilot-extensions#3179 follow-up), and that hook never
    fires for a session that crashed or was reaped before it could run.

    Mutates ``record`` in place (does not itself persist -- the caller
    saves alongside its own conclusion write) and returns the claims
    released, for observability. A missing ``session_id`` releases
    nothing (there is no specific session to attribute the release to).
    """
    if not session_id:
        return []
    # Mirrors `deregister_session`'s own `self_session_ref` construction
    # exactly (tracking.py) -- an exact-string match on the fully qualified
    # ref, not merely the parsed session fragment, so a claim is only ever
    # released when its machine, project, AND worktree id all match this
    # record too, not just an incidental session-id substring collision.
    expected_ref = tracking.format_claim_ref(
        record.machine, record.repo, record.worktree_id, session=session_id,
    )
    released = [
        claim for claim in record.resources
        if claim.is_live and claim.kind == "session" and claim.ref == expected_ref
    ]
    for claim in released:
        tracking.release_resource_claim(record, claim.ref, save=False)
    return released


def _preservation_reason(
    record: tracking.WorktreeRecord, *, policy: str, pair_clear: bool = False,
) -> str | None:
    """``pair_clear=True`` means a dispatch attempt's own reciprocal-claim
    check (:func:`_dispatch_pair_clearance`) has already confirmed this
    record's paired knowledge sibling is itself safe to dispose of
    together with this worktree -- so ``is_paired`` alone must NOT block
    here (the caller will conclude both halves as one unit). Every other
    caller (a human CLI/finalize conclusion, or a dispatch attempt whose
    sibling is not yet safe to dispose) keeps the original unconditional
    block: pairing is still an outstanding obligation.
    """
    if policy == DISPOSABLE_CLI_POLICY and record.resolved_interface != "cli":
        return "not-cli-worktree"
    if record.pending_handoffs:
        return "pending-handoff"
    if record.follow_up or record.active_effort is not None:
        return "follow-up"
    if record.live_resources or record.owner_ref:
        return "outstanding-obligations"
    if record.is_paired and not pair_clear:
        return "outstanding-obligations"
    if any(pr.state in {"creating", "open"} for pr in record.prs):
        return "open-pull-request"
    return None


def _sibling_preservation_reason(sibling: tracking.WorktreeRecord) -> str | None:
    """Preservation gate for the RECIPROCAL sibling of a dispatch-owned
    pair concluding together (see :func:`_dispatch_pair_clearance`).
    Deliberately narrower than :func:`_preservation_reason`: the sibling
    being paired back to the very harness worktree we are concluding is
    expected and must NOT itself block disposal; every other obligation on
    the sibling's own record is checked exactly as it would be for the
    harness side.
    """
    if sibling.pending_handoffs:
        return "pair-sibling-pending-handoff"
    if sibling.follow_up or sibling.active_effort is not None:
        return "pair-sibling-follow-up"
    if sibling.live_resources or sibling.owner_ref:
        return "pair-sibling-outstanding-obligations"
    if any(pr.state in {"creating", "open"} for pr in sibling.prs):
        return "pair-sibling-open-pull-request"
    if sibling.resolved_head_session is not None:
        # A tracked, still-resumable lifecycle head is independent of live
        # -process detection (`_session_is_live`): an ENDED session can
        # still carry an active head that a human/agent could resume.
        # Marking the sibling bridge/complete without concluding that
        # session first would silently strand it.
        return "pair-sibling-active-lifecycle-head"
    return None


def _reciprocal_pair_valid(
    record: tracking.WorktreeRecord,
    sibling: tracking.WorktreeRecord,
    sibling_path: Path,
) -> bool:
    """Whether ``sibling`` (loaded from ``sibling_path``) validates as
    ``record``'s genuine RECIPROCAL harness/knowledge partner -- mirrors
    the invariant :func:`state_root.resolve_pair` enforces, so a stale or
    malformed link can never redirect this destructive path onto an
    unrelated tracking record:

    * ``sibling``'s own identity matches its filename;
    * ``sibling``'s own worktree path/branch match its own identity
      (mirrors :func:`_identity_reason`'s harness-side checks) -- a
      malformed record (e.g. a stale ``branch: main`` claim on a clean
      checkout) must never be marked ``bridge`` and later let the managed
      reaper delete/mutate an unrelated branch;
    * both records share the same non-empty ``pair_id``;
    * ``pair_role`` is complementary (``{"harness", "knowledge"}``, exactly
      one of each);
    * both share ``pair_kind == "worktree"`` (an ``anchor``-kind pair has no
      sibling worktree of its own to dispose of here);
    * the sibling's OWN ``pair_ref`` resolves back to this exact record's
      identity (machine/repo/worktree_id) -- the link is reciprocal, not
      merely one-directional.
    """
    if sibling.worktree_id != sibling_path.stem:
        return False
    if Path(sibling.worktree_path).name != sibling.worktree_id:
        return False
    if sibling.branch != f"worktree/{sibling.worktree_id}":
        return False
    current_pair_id = (record.pair_id or "").strip()
    sibling_pair_id = (sibling.pair_id or "").strip()
    if not current_pair_id or current_pair_id != sibling_pair_id:
        return False
    current_role = (record.pair_role or "").strip()
    sibling_role = (sibling.pair_role or "").strip()
    if {current_role, sibling_role} != {"harness", "knowledge"}:
        return False
    if (record.pair_kind or "").strip() != "worktree" or (sibling.pair_kind or "").strip() != "worktree":
        return False
    sibling_ref = sibling.pair_claim_ref
    expected = tracking.format_claim_ref(record.machine, record.repo, record.worktree_id)
    if sibling_ref is None or sibling_ref.canonical() != expected:
        return False
    return True


def _resolve_pair_sibling(
    record: tracking.WorktreeRecord,
) -> tuple[tracking.WorktreeRecord, Path] | None:
    """Resolve the sibling record AND its tracking file path, or ``None``
    when the pair cannot be resolved OR does not validate as a genuine
    reciprocal harness/knowledge pair (:func:`_reciprocal_pair_valid`).
    Mirrors :func:`tracking.find_paired_record`'s own qualification rules
    for locating the file. Also returns the path so a caller can re-lock
    and persist the sibling.
    """
    ref = record.pair_claim_ref
    if ref is None or not ref.is_qualified or ref.machine != record.machine or not ref.project:
        return None
    path = cfg.project_dir(ref.project) / "worktrees" / f"{ref.worktree_id}.yaml"
    if not path.exists():
        return None
    try:
        sibling = tracking.load_record(path)
    except Exception:
        return None
    if not _reciprocal_pair_valid(record, sibling, path):
        return None
    return sibling, path


def _sibling_ancestry_reason(sibling: tracking.WorktreeRecord) -> str | None:
    """Conservative git-ancestry gate for the RECIPROCAL sibling of a
    dispatch-owned pair concluding together -- mirrors the harness side's
    own branch-drift/upstream/ahead-count/dirty-work checks (the unlocked
    git-inspection section of :func:`conclude_disposable_worktree`), so a
    merely-clean-looking sibling with unpushed local-only commits is never
    silently marked managed/complete and lost to a later GC sweep. Also
    mirrors the harness side's MISSING-checkout fallback: even when the
    sibling's own worktree directory is gone, its branch may still exist
    (in the sibling project's own anchor) with unpushed commits.
    """
    try:
        sibling_config = cfg.load_config(project=sibling.repo)
        default_repo = sibling_config.default_repo
        remote = default_repo.remote
        default_branch = default_repo.default_branch
        anchor = default_repo.anchor
    except Exception:
        return "pair-sibling-repo-config-unavailable"
    if not remote or not default_branch:
        return "pair-sibling-repo-config-unavailable"

    worktree_path = Path(sibling.worktree_path)
    if not worktree_path.exists():
        if not sibling.branch:
            return None
        branch = git_ops.git(
            "rev-parse", "--verify", f"refs/heads/{sibling.branch}^{{commit}}",
            cwd=anchor, check=False,
        )
        if branch.returncode != 0:
            return None
        upstream = f"{remote}/{default_branch}"
        verified = git_ops.git(
            "rev-parse", "--verify", f"{upstream}^{{commit}}", cwd=anchor, check=False,
        )
        if verified.returncode != 0:
            return "pair-sibling-upstream-unavailable"
        ahead = git_ops.git(
            "rev-list", "--count", f"{upstream}..{sibling.branch}", cwd=anchor, check=False,
        )
        if ahead.returncode != 0:
            return "pair-sibling-upstream-unavailable"
        try:
            ahead_count = int(ahead.stdout.strip())
        except ValueError:
            return "pair-sibling-upstream-unavailable"
        if ahead_count:
            return "pair-sibling-local-commits"
        return None

    if not (worktree_path / ".git").exists():
        return "pair-sibling-invalid-worktree"
    if sibling.branch:
        current_branch = git_ops.current_branch(worktree_path)
        if current_branch != sibling.branch:
            return "pair-sibling-branch-drift"
    upstream = f"{remote}/{default_branch}"
    verified = git_ops.git(
        "rev-parse", "--verify", f"{upstream}^{{commit}}", cwd=worktree_path, check=False,
    )
    if verified.returncode != 0:
        return "pair-sibling-upstream-unavailable"
    ahead = git_ops.git(
        "rev-list", "--count", f"{upstream}..HEAD", cwd=worktree_path, check=False,
    )
    if ahead.returncode != 0:
        return "pair-sibling-upstream-unavailable"
    try:
        ahead_count = int(ahead.stdout.strip())
    except ValueError:
        return "pair-sibling-upstream-unavailable"
    if ahead_count:
        return "pair-sibling-local-commits"
    if _dirty_entries(worktree_path):
        return "pair-sibling-dirty-work"
    return None


def _dispatch_pair_clearance(
    record: tracking.WorktreeRecord,
    *,
    policy: str,
    reservation_key: str | None,
    owner: str,
) -> tuple[bool, str | None]:
    """Whether THIS dispatch attempt may dispose of its own paired
    knowledge sibling together with the harness worktree it concludes --
    the "reciprocal claim", symmetric to
    :func:`_release_dispatch_session_claims`'s own session-claim release.

    Returns ``(pair_clear, block_reason)``:

    * ``(False, None)`` -- unpaired, not a dispatch-attempt conclusion, or
      ownership is unconfirmed: :func:`_preservation_reason`'s ordinary
      unconditional ``is_paired`` block still applies unchanged (a human
      ``finalize``/CLI conclusion, or a dispatch attempt that doesn't
      exactly match this record's own provenance).
    * ``(False, <reason>)`` -- paired, dispatch-owned, ownership confirmed,
      but the sibling itself is not safe to dispose (still live, dirty, or
      otherwise obligated) -- the WHOLE pair stays preserved rather than
      unblocking the harness half and orphaning the knowledge half.
    * ``(True, None)`` -- safe to dispose of both halves together.

    Read-only: never mutates or persists anything.
    """
    if not record.is_paired:
        return False, None
    if policy != DISPATCH_ATTEMPT_POLICY:
        return False, None
    if not _dispatch_ownership_confirmed(record, reservation_key=reservation_key, owner=owner):
        return False, None
    if (record.pair_kind or "").strip() == "anchor":
        # An anchor-kind pair (a non-worktree-class/singleton knowledge
        # repo, see `_carve_paired_knowledge`) has no disposable sibling
        # WORKTREE at all -- `pair_ref` names the anchor's own long-lived
        # checkout directory, never a tracked worktree record. Only THIS
        # side's own pairing link needs discharging once it concludes; the
        # anchor itself is a shared resource that must never be touched
        # here (see `_conclude_pair_sibling`'s caller for the discharge).
        return True, None
    resolved = _resolve_pair_sibling(record)
    if resolved is None:
        return False, "pair-sibling-unresolved"
    sibling, _sibling_path = resolved
    if _session_is_live(sibling):
        return False, "pair-sibling-live"
    if reason := _sibling_preservation_reason(sibling):
        return False, reason
    if reason := _sibling_ancestry_reason(sibling):
        return False, reason
    return True, None


def _conclude_pair_sibling(record: tracking.WorktreeRecord) -> dict[str, Any]:
    """Dispose of the RECIPROCAL sibling of a dispatch-owned pair, once the
    harness half's own conclusion has just been decided and persisted
    under lock. Re-resolves and re-locks the sibling's own record file
    (never trusts the read taken during :func:`_dispatch_pair_clearance`,
    which ran without the sibling's own lock held) and re-verifies the
    FULL reciprocal-pair invariant, ancestry, and preservation gates while
    holding that lock -- a concurrent rewrite/repair could otherwise
    retarget the path, or a last-instant change (a new live session, new
    dirty work) could make disposal unsafe, between the earlier unlocked
    read and this lock acquisition. Any such finding defers this call
    (returns a ``skipped`` action) rather than forcing disposal through --
    the harness half's own conclusion is NOT rolled back in that case:
    orphaning the sibling once more, to be picked up again on a later
    conclusion/reap pass, is preferable to blocking the whole disposal on
    a last-instant sibling race.

    On success, DISCHARGES the pairing link on the sibling (clears
    ``pair_id``/``pair_role``/``pair_ref``/``pair_kind``): once both halves
    of a dispatch-owned pair have been jointly concluded, neither one still
    needs the pairing link to reap independently, and the ordinary managed
    sweep's own paired-worktree guard (``reap_cli.py``) treats ``is_paired``
    as unconditionally disqualifying -- leaving it set would strand both
    now-"primed" records forever, never actually reaped. The caller
    discharges the harness side's own pairing fields the same way once this
    returns success (see :func:`conclude_disposable_worktree`).
    """
    resolved = _resolve_pair_sibling(record)
    if resolved is None:
        return {"action": "skipped", "reason": "pair-sibling-unresolved"}
    _sibling, sibling_path = resolved
    try:
        with tracking._RecordLock(sibling_path, timeout=3, require_sidecar=True):
            sibling = tracking.load_record(sibling_path)
            if not _reciprocal_pair_valid(record, sibling, sibling_path):
                return {"action": "skipped", "reason": "pair-sibling-identity-mismatch"}
            if live_reason := _session_is_live(sibling):
                return {"action": "skipped", "reason": live_reason}
            if reason := _sibling_preservation_reason(sibling):
                return {"action": "skipped", "reason": reason}
            if reason := _sibling_ancestry_reason(sibling):
                return {"action": "skipped", "reason": reason}
            already_primed = (
                sibling.kind in tracking.MANAGED_KINDS
                and sibling.status in {"complete", "finalized"}
            )
            sibling.kind = "bridge"
            sibling.owner = record.owner
            sibling.origin = "delegate"
            tracking.update_status(sibling, "complete", save=False)
            # Discharge the pairing link -- see the docstring above.
            sibling.pair_id = None
            sibling.pair_role = None
            sibling.pair_ref = None
            sibling.pair_kind = None
            tracking.save_record(sibling, sibling_path)
            return {
                "action": "already-primed" if already_primed else "primed",
                "worktree_id": sibling.worktree_id,
            }
    except TimeoutError:
        return {"action": "skipped", "reason": "pair-sibling-lock-timeout"}


def _identity_reason(
    record: tracking.WorktreeRecord,
    record_path: Path,
) -> str | None:
    if Path(record.worktree_path).name != record.worktree_id:
        return "worktree-path-mismatch"
    if record.branch != f"worktree/{record.worktree_id}":
        return "worktree-branch-mismatch"
    path_key = _normalized(record.worktree_path)
    for peer in tracking.list_records(record_path.parent):
        if peer.worktree_id == record.worktree_id:
            continue
        if _normalized(peer.worktree_path) == path_key:
            return "worktree-path-conflict"
        if peer.branch and peer.branch == record.branch:
            return "worktree-branch-conflict"
    return None


def conclude_disposable_worktree(
    record_path: Path,
    repo: Any,
    *,
    session_id: str | None,
    owner: str,
    policy: str,
    reservation_key: str | None = None,
) -> dict[str, Any]:
    """Prime one exact disposable worktree for the existing managed GC.

    Safe skips return ``action="skipped"`` with a stable reason. Operational
    failures raise so a higher layer can distinguish a failed conclusion from a
    deliberate preservation decision.
    """
    if policy not in {DISPOSABLE_CLI_POLICY, DISPATCH_ATTEMPT_POLICY}:
        raise ValueError(f"unsupported terminal conclusion policy: {policy!r}")

    record_path = Path(record_path)
    record = tracking.load_record(record_path)
    if record.worktree_id != record_path.stem:
        raise RuntimeError(
            "tracking record identity does not match its filename"
        )
    result: dict[str, Any] = {
        "worktree_id": record.worktree_id,
        "policy": policy,
        "session": {"action": "skipped", "reason": "not-attempted"},
        "managed_gc_eligible": False,
    }

    lock = finalize.FinalizeLock(
        Path(repo.worktree_root) / ".finalize.lock",
        timeout=3,
        stale_after=3600,
    )
    try:
        lock.acquire()
    except TimeoutError as exc:
        raise RuntimeError("timed out waiting for the worktree lifecycle lock") from exc

    try:
        with tracking._RecordLock(
            record_path,
            timeout=3,
            require_sidecar=True,
        ):
            record = tracking.load_record(record_path)
            if record.worktree_id != record_path.stem:
                raise RuntimeError(
                    "tracking record identity changed during conclusion"
                )
            if reason := _identity_reason(record, record_path):
                result.update(action="skipped", reason=reason)
                return result
            if policy == DISPATCH_ATTEMPT_POLICY:
                allocation = record.dispatch_attempt
                if allocation is None:
                    result.update(
                        action="skipped",
                        reason="dispatch-provenance-missing",
                    )
                    return result
                if not reservation_key:
                    result.update(
                        action="skipped",
                        reason="reservation-identity-missing",
                    )
                    return result
                if allocation.reservation_key != reservation_key:
                    result.update(
                        action="skipped",
                        reason="reservation-mismatch",
                    )
                    return result
                if allocation.creator_machine.casefold() != record.machine.casefold():
                    result.update(
                        action="skipped",
                        reason="creator-machine-mismatch",
                    )
                    return result
                if allocation.driver != owner:
                    result.update(
                        action="skipped",
                        reason="driver-owner-mismatch",
                    )
                    return result
            live_reason = _session_is_live(record)
            if live_reason:
                result.update(action="skipped", reason=live_reason)
                result["session"] = {
                    "action": "preserved",
                    "reason": live_reason,
                    **({"session": session_id} if session_id else {}),
                }
                return result

            if record.pending_handoffs:
                result.update(action="skipped", reason="pending-handoff")
                result["session"] = {
                    "action": "preserved",
                    "reason": "pending-handoff",
                    **({"session": session_id} if session_id else {}),
                }
                return result

            head_session = record.resolved_head_session
            if head_session and not session_id:
                result.update(action="skipped", reason="session-unavailable")
                result["session"] = {
                    "action": "preserved",
                    "reason": "session-unavailable",
                    "head_session": head_session,
                }
                return result
            if head_session and session_id != head_session:
                result.update(action="skipped", reason="session-mismatch")
                result["session"] = {
                    "action": "preserved",
                    "reason": "session-mismatch",
                    "session": session_id,
                    "head_session": head_session,
                }
                return result

            if policy == DISPATCH_ATTEMPT_POLICY and _dispatch_ownership_confirmed(
                record, reservation_key=reservation_key, owner=owner
            ):
                # In-memory only here -- deliberately NOT persisted. This
                # first phase is a preliminary gate that decides whether the
                # (potentially slow) unlocked git inspection below is even
                # worth running; if it or the second locked phase's
                # revalidation later finds a different reason to skip
                # (dirty work, branch drift, a lifecycle change underneath),
                # persisting the release here would leave an irreversible
                # side effect on a call that ultimately never primed the
                # worktree. The second phase re-derives and persists this
                # same release only once the conclusion has fully succeeded.
                _release_dispatch_session_claims(record, session_id=session_id)

            pair_clear, pair_block_reason = _dispatch_pair_clearance(
                record, policy=policy, reservation_key=reservation_key, owner=owner,
            )
            if pair_block_reason:
                result.update(action="skipped", reason=pair_block_reason)
                return result

            if reason := _preservation_reason(record, policy=policy, pair_clear=pair_clear):
                result.update(action="skipped", reason=reason)
                return result

            lifecycle_revision = record.lifecycle_revision
            expected_path = record.worktree_path
            expected_branch = record.branch

        worktree_path = Path(expected_path)
        if worktree_path.exists():
            if not (worktree_path / ".git").exists():
                result.update(action="skipped", reason="invalid-worktree")
                return result
            current_branch = git_ops.current_branch(worktree_path)
            if current_branch != expected_branch:
                result.update(action="skipped", reason="branch-drift")
                return result

            upstream = f"{repo.remote}/{repo.default_branch}"
            verified = git_ops.git(
                "rev-parse",
                "--verify",
                f"{upstream}^{{commit}}",
                cwd=worktree_path,
                check=False,
            )
            if verified.returncode != 0:
                result.update(action="skipped", reason="upstream-unavailable")
                return result
            ahead = git_ops.git(
                "rev-list",
                "--count",
                f"{upstream}..HEAD",
                cwd=worktree_path,
                check=False,
            )
            if ahead.returncode != 0:
                raise RuntimeError(
                    ahead.stderr.strip() or "could not inspect local commits"
                )
            try:
                ahead_count = int(ahead.stdout.strip())
            except ValueError as exc:
                raise RuntimeError("could not parse local commit count") from exc
            if ahead_count:
                result.update(
                    action="skipped",
                    reason="local-commits",
                    local_commits=ahead_count,
                )
                return result

            dirty = _dirty_entries(worktree_path)
            if dirty:
                result.update(
                    action="skipped",
                    reason="dirty-work",
                    dirty_paths=sorted(relative for _status, relative in dirty),
                )
                return result

        elif expected_branch:
            branch = git_ops.git(
                "rev-parse",
                "--verify",
                f"refs/heads/{expected_branch}^{{commit}}",
                cwd=repo.anchor,
                check=False,
            )
            if branch.returncode == 0:
                upstream = f"{repo.remote}/{repo.default_branch}"
                verified = git_ops.git(
                    "rev-parse",
                    "--verify",
                    f"{upstream}^{{commit}}",
                    cwd=repo.anchor,
                    check=False,
                )
                if verified.returncode != 0:
                    result.update(
                        action="skipped",
                        reason="upstream-unavailable",
                    )
                    return result
                ahead = git_ops.git(
                    "rev-list",
                    "--count",
                    f"{upstream}..{expected_branch}",
                    cwd=repo.anchor,
                    check=False,
                )
                if ahead.returncode != 0:
                    raise RuntimeError(
                        ahead.stderr.strip()
                        or "could not inspect the missing checkout's branch"
                    )
                try:
                    ahead_count = int(ahead.stdout.strip())
                except ValueError as exc:
                    raise RuntimeError(
                        "could not parse the missing checkout's commit count"
                    ) from exc
                if ahead_count:
                    result.update(
                        action="skipped",
                        reason="local-commits",
                        local_commits=ahead_count,
                    )
                    return result

        with tracking._RecordLock(
            record_path,
            timeout=3,
            require_sidecar=True,
        ):
            record = tracking.load_record(record_path)
            if record.worktree_id != record_path.stem:
                raise RuntimeError(
                    "tracking record identity changed during conclusion"
                )
            if reason := _identity_reason(record, record_path):
                result.update(action="skipped", reason=reason)
                return result
            if live_reason := _session_is_live(record):
                result.update(action="skipped", reason=live_reason)
                return result
            if (
                record.lifecycle_revision != lifecycle_revision
                or record.worktree_path != expected_path
                or record.branch != expected_branch
            ):
                result.update(action="skipped", reason="lifecycle-changed")
                return result
            if policy == DISPATCH_ATTEMPT_POLICY and _dispatch_ownership_confirmed(
                record, reservation_key=reservation_key, owner=owner
            ):
                _release_dispatch_session_claims(record, session_id=session_id)
            pair_clear, pair_block_reason = _dispatch_pair_clearance(
                record, policy=policy, reservation_key=reservation_key, owner=owner,
            )
            if pair_block_reason:
                result.update(action="skipped", reason=pair_block_reason)
                return result
            if reason := _preservation_reason(record, policy=policy, pair_clear=pair_clear):
                result.update(action="skipped", reason=reason)
                return result
            session_result, _session_changed = _save_session_conclusion(
                record,
                record_path,
                session_id,
            )
            result["session"] = session_result
            if record.resolved_head_session is not None:
                result.update(action="skipped", reason="active-lifecycle-head")
                return result
            already_primed = (
                record.kind in tracking.MANAGED_KINDS
                and record.status in {"complete", "finalized"}
                and record.owner == owner
                and record.resolved_origin == "delegate"
                and (
                    policy == DISPATCH_ATTEMPT_POLICY
                    or record.resolved_interface == "cli"
                )
            )
            record.kind = "bridge"
            record.owner = owner
            if policy == DISPOSABLE_CLI_POLICY:
                record.interface = "cli"
            record.origin = "delegate"
            tracking.update_status(record, "complete", save=False)
            tracking.save_record(record, record_path)

            result.update(
                action="already-primed" if already_primed else "primed",
                reason="managed-gc-candidate",
                reconciled=False,
                managed_gc_eligible=True,
            )
            if pair_clear:
                # Reciprocal disposal (catch-22 follow-up to #3198/#3207):
                # this dispatch attempt's own paired knowledge sibling is
                # part of the SAME disposable unit as the harness worktree
                # just primed above -- dispose of it too rather than
                # leaving it wedged forever behind `is_paired`.
                if (record.pair_kind or "").strip() == "anchor":
                    # No disposable sibling worktree exists for an anchor
                    # pair (see `_dispatch_pair_clearance`) -- the anchor
                    # itself is a shared, long-lived checkout that must
                    # never be touched here. Only this side's own link
                    # needs discharging below.
                    sibling_result: dict[str, Any] = {
                        "action": "not-applicable",
                        "reason": "anchor-pair",
                    }
                    pairing_discharged = True
                else:
                    sibling_result = _conclude_pair_sibling(record)
                    pairing_discharged = sibling_result.get("action") in (
                        "primed", "already-primed",
                    )
                result["pair_sibling"] = sibling_result
                if pairing_discharged:
                    # Both halves are now concluded together -- discharge
                    # THIS side's own pairing link too (see
                    # `_conclude_pair_sibling`'s docstring for why: the
                    # managed sweep's paired-worktree guard would otherwise
                    # strand this now-"primed" record forever, since
                    # `is_paired` still reads True). Persisted as its own
                    # save so a failure here never rolls back the
                    # conclusion already durably recorded above.
                    record.pair_id = None
                    record.pair_role = None
                    record.pair_ref = None
                    record.pair_kind = None
                    tracking.save_record(record, record_path)
                else:
                    # A last-instant sibling-side race left the pairing
                    # link intact (`record.is_paired` still True): the
                    # managed sweep's own paired-worktree guard will keep
                    # skipping THIS record too, so it must not be reported
                    # as GC-eligible -- a caller keying off this exact
                    # field (e.g. `Supervisor`'s conclusion tracking) would
                    # otherwise treat the whole reservation as cleanly
                    # concluded and never retry the stranded sibling. The
                    # harness side's own `bridge`/`complete` transition
                    # above is NOT rolled back (still a real, durable,
                    # retryable step); only the eligibility claim is
                    # corrected.
                    result["managed_gc_eligible"] = False
            return result
    finally:
        lock.release()


__all__ = [
    "DISPATCH_ATTEMPT_POLICY",
    "DISPOSABLE_CLI_POLICY",
    "conclude_disposable_worktree",
]
