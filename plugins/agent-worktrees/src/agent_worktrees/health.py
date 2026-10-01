"""Worktree/session health checks and repairs -- the engine behind ``doctor``.

Pure, side-effect-scoped helpers that diagnose (and optionally repair) drift in
a project's tracking records and the shared Copilot session store. ``doctor``
(in ``__main__``) orchestrates these and renders text/JSON; keeping the logic
here makes each pass unit-testable without argparse or a real project.

Passes:
  1. YAML integrity -- tracking records that fail to parse (e.g. an unquoted
     ``title:`` scalar containing ``:`` from before the serializer quoted them)
     are invisible to ``list_records`` (it skips them). Detect and, with
     ``apply``, re-quote the offending scalar so the record loads again.
  2. Stale status -- ``status: active`` with a ``completed_at`` set is a record
     the lifecycle never closed out; repair to ``complete``.
  3. Empty session-state GC -- 0-user-message session shells (left by aborted
     starts / pre-fix cross-cwd resumes) are removed, with age/lock/current/
     registered guards, and their orphaned ``session-store.db`` rows purged.
  4. Alignment audit (report-only) -- worktrees with no own session but a
     ``parent_session`` whose cwd differs from their own path (the class of
     drift that used to make a tab open in another worktree's directory).
  5. Orphaned handoff -- a worktree whose head was lost to a handoff-cutover
     whose successor never registered (e.g. it died on the CLI resume-hang), so
     the derived head is None and the worktree is un-resumable. Detected only
     when dark + stale (never a healthy in-flight cutover); with ``apply`` the
     orchestrator re-activates the handed-off tail so the head derives again.
  6. Stale active reconciliation -- ``status: active`` records the *raw*
     lifecycle write path never closes (a killed terminal, a rebooted VM, a
     crashed Copilot process -- anything skipping the graceful
     ``finalize``/``restart``/``conclude-session`` transitions that stamp
     ``complete``/``completed_at``). Pass 2 only catches this once
     ``completed_at`` is already set; it never fires for a record simply
     abandoned mid-flight. This pass recomputes each ``active`` record's real
     state the way ``status``/the Picker already do
     (:func:`git_ops.classify_worktree`, gated by the same mux/lock/bridge
     ``active_paths``), flagging only the unambiguous terminal outcomes
     (:class:`git_ops.WorktreeState.GONE`/``COMPLETED``/``UNUSED``) with zero
     liveness evidence -- never a live, dirty, orphaned, or timed-out
     worktree. Closes the gap that let Reclaim ("nothing to reclaim" was a
     correct answer) and Restore/Resume then fail against a record the
     Picker still rendered Active forever.

Registry/title backfill is delegated to ``sessions.backfill_sessions`` by the
orchestrator; it is not duplicated here.
"""
from __future__ import annotations

import re
import shutil
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import yaml

from . import config as cfg
from . import git_ops
from . import handoff_trace
from . import tracking
from .handoff_diagnostics import HANDOFF_STAGES

# Mirrors the serializer's quoting predicate in ``tracking.save_record`` so a
# repaired scalar is quoted on exactly the same chars the writer would have.
_NEEDS_QUOTE = set(":{}[]#&*!|>'\",")
# Free-text scalar fields the (older) serializer could emit unquoted.
_QUOTABLE_FIELDS = ("title", "summary")


@dataclass
class PairIntegrityFinding:
    """A paired worktree record is absent from its owning project registry."""

    worktree_id: str
    project: str
    found_in_project: str
    detail: str
    repairable: bool
    repaired: bool = False

    def as_dict(self) -> dict:
        return {
            "worktree_id": self.worktree_id,
            "project": self.project,
            "found_in_project": self.found_in_project,
            "detail": self.detail,
            "repairable": self.repairable,
            "repaired": self.repaired,
        }


def _tracking_dir(project: str) -> Path:
    return cfg.project_dir(project) / "worktrees"


def _same_path(left: str, right: str) -> bool:
    if not left or not right:
        return False
    candidate = Path(left).resolve(strict=False)
    root = Path(right).resolve(strict=False)
    return candidate == root or root in candidate.parents


def find_record_by_cwd_across_projects(
    cwd: str,
    project_names: list[str],
):
    """Find one unambiguous record containing ``cwd`` across all projects."""
    matches = []
    for project in project_names:
        for record in tracking.list_records(_tracking_dir(project)):
            if _same_path(cwd, record.worktree_path):
                matches.append(record)
    identities = {
        (record.machine, record.repo, record.worktree_id): record
        for record in matches
    }
    return next(iter(identities.values())) if len(identities) == 1 else None


def audit_pair_integrity(
    project_names: list[str],
    *,
    apply: bool,
) -> list[PairIntegrityFinding]:
    """Detect and repair records stored outside their owning project registry.

    The paired carve historically wrote the knowledge sibling into the active
    harness tracking directory. A repair is safe only when exactly one misplaced
    record has the identity named by a qualified same-machine pair reference and
    its checkout still exists. The legacy copy is retained during rollout so
    older runtimes can continue resolving active pairs.
    """
    records_by_project = {
        project: tracking.list_records(_tracking_dir(project))
        for project in project_names
    }
    findings: list[PairIntegrityFinding] = []
    seen: set[tuple[str, str]] = set()

    for source_project, records in records_by_project.items():
        for record in records:
            if record.pair_kind != "worktree":
                continue
            ref = tracking.parse_claim_ref(record.pair_ref or "")
            if not (
                ref
                and ref.is_qualified
                and ref.machine == record.machine
                and ref.project
                and ref.project in records_by_project
            ):
                continue
            target_key = (ref.project, ref.worktree_id)
            if target_key in seen:
                continue
            target_path = _tracking_dir(ref.project) / f"{ref.worktree_id}.yaml"
            if target_path.exists():
                continue

            candidates = [
                (project, candidate)
                for project, project_records in records_by_project.items()
                for candidate in project_records
                if (
                    candidate.worktree_id == ref.worktree_id
                    and candidate.machine == ref.machine
                    and candidate.repo == ref.project
                )
            ]
            repairable = (
                len(candidates) == 1
                and candidates[0][0] != ref.project
                and Path(candidates[0][1].worktree_path).is_dir()
            )
            found_in = (
                candidates[0][0]
                if len(candidates) == 1
                else ", ".join(sorted(project for project, _ in candidates))
            )
            if len(candidates) > 1:
                location = (
                    "has multiple candidate records across project registries: "
                    f"{found_in}"
                )
            elif found_in:
                location = f"is stored under {found_in!r}"
            else:
                location = "is missing from every project registry"
            finding = PairIntegrityFinding(
                worktree_id=ref.worktree_id,
                project=ref.project,
                found_in_project=found_in,
                detail=(
                    f"record belongs in project {ref.project!r} but {location}"
                ),
                repairable=repairable,
            )
            if apply and repairable:
                _source, candidate = candidates[0]
                target_path.parent.mkdir(parents=True, exist_ok=True)
                tracking.save_record(candidate, target_path)
                finding.repaired = True
            findings.append(finding)
            seen.add(target_key)
    return findings


# --------------------------------------------------------------------------- #
# Pass 1: tracking-YAML integrity
# --------------------------------------------------------------------------- #
@dataclass
class YamlFinding:
    path: Path
    error: str
    repairable: bool
    repaired: bool = False


def _quote_scalar(val: str) -> str:
    return "'" + val.replace("'", "''") + "'"


def repair_yaml_text(raw: str) -> str | None:
    """Return a repaired copy of *raw* with unquoted free-text scalars quoted,
    or ``None`` when nothing needed quoting (so callers can tell a no-op apart
    from a fix)."""
    lines = raw.splitlines(keepends=True)
    changed = False
    for i, line in enumerate(lines):
        for fieldname in _QUOTABLE_FIELDS:
            m = re.match(rf"^({fieldname}:[ \t]+)(.*?)([ \t]*\r?\n?)$", line)
            if not m:
                continue
            prefix, val, tail = m.group(1), m.group(2), m.group(3)
            if not val or val[0] in "'\"":
                continue  # empty or already quoted
            if val in ("null", "|", "|-", ">", ">-"):
                continue  # block/placeholder scalars -- leave alone
            if any(ch in _NEEDS_QUOTE for ch in val):
                lines[i] = f"{prefix}{_quote_scalar(val)}{tail}"
                changed = True
            break
    return "".join(lines) if changed else None


def repair_yaml_integrity(tracking_dir: Path, *, apply: bool) -> list[YamlFinding]:
    """Find (and optionally repair) tracking records that fail to parse.

    Scans the raw ``*.yaml`` -- not ``list_records`` -- because a corrupt
    record is silently skipped there and would otherwise stay invisible.
    """
    findings: list[YamlFinding] = []
    if not tracking_dir.exists():
        return findings
    for y in sorted(tracking_dir.glob("*.yaml")):
        try:
            raw = y.read_text(encoding="utf-8")
        except OSError as e:
            findings.append(YamlFinding(y, f"unreadable: {e}", repairable=False))
            continue
        try:
            data = yaml.safe_load(raw)
        except Exception as e:
            fixed = repair_yaml_text(raw)
            finding = YamlFinding(y, _first_line(str(e)), repairable=fixed is not None)
            if fixed is not None and apply:
                try:
                    y.write_text(fixed, encoding="utf-8")
                    finding.repaired = isinstance(yaml.safe_load(fixed), dict)
                except Exception:
                    finding.repaired = False
            findings.append(finding)
            continue
        if not isinstance(data, dict):
            findings.append(YamlFinding(y, "not a YAML mapping", repairable=False))
    return findings


def _first_line(text: str) -> str:
    return text.strip().splitlines()[0].strip() if text.strip() else "parse error"


# --------------------------------------------------------------------------- #
# Pass 2: stale status
# --------------------------------------------------------------------------- #
_TERMINAL_STATUSES = frozenset({"complete", "finalized"})


def find_stale_status(records) -> list:
    """Records marked ``active`` yet carrying a ``completed_at`` -- the
    lifecycle finished but the status was never closed out."""
    return [
        r for r in records
        if getattr(r, "completed_at", None) and r.status == "active"
    ]


# --------------------------------------------------------------------------- #
# Pass 6: stale active reconciliation
# --------------------------------------------------------------------------- #
# Only these raw git-state outcomes are safe to reconcile automatically -- each
# is an unambiguous, no-work-lost terminal: GONE (the checkout is simply gone),
# COMPLETED (merged/landed, clean), UNUSED (clean, commit-less, never used).
# DIRTY, ORPHAN (no merge-base), and UNKNOWN (classification timeout) all need
# a human -- never auto-closed.
_SAFE_TERMINAL_STATES = frozenset({
    git_ops.WorktreeState.GONE,
    git_ops.WorktreeState.COMPLETED,
    git_ops.WorktreeState.UNUSED,
})


@dataclass
class StaleActiveFinding:
    """An ``active`` record whose real (live-checked) state is terminal."""

    worktree_id: str
    computed_state: str

    def as_dict(self) -> dict:
        return {"worktree_id": self.worktree_id, "computed_state": self.computed_state}


def find_stale_active_records(
    records,
    *,
    active_paths: frozenset[str] | set[str],
    remote: str = "origin",
    default_branch: str = "master",
) -> list[StaleActiveFinding]:
    """``active`` records whose real git/liveness state is unambiguously done.

    Recomputes each candidate exactly the way ``status``/the Picker already
    do (:func:`git_ops.classify_worktree`, no network fetch -- doctor never
    reaches out to a remote on its own), gated by the caller-supplied
    *active_paths* (the same batched mux/lock/bridge liveness set
    ``__main__._build_active_paths`` builds). ``classify_worktree`` itself
    treats any path in *active_paths* as unconditionally ``ACTIVE`` regardless
    of git state, so a live worktree can never be flagged here no matter what
    its git history looks like. Only :data:`_SAFE_TERMINAL_STATES` are
    reported -- a dirty, orphaned, or timed-out classification is left alone
    for a human to look at.
    """
    findings: list[StaleActiveFinding] = []
    for r in records:
        if r.status != "active" or not r.worktree_path:
            continue
        info = git_ops.classify_worktree(
            r.worktree_path,
            r.branch,
            fetch=False,
            remote=remote,
            default_branch=default_branch,
            active_paths=active_paths,
        )
        if info.state in _SAFE_TERMINAL_STATES:
            findings.append(StaleActiveFinding(r.worktree_id, info.state.value))
    return findings


def apply_stale_active_repairs(records, findings: list[StaleActiveFinding]) -> int:
    """Close each :func:`find_stale_active_records` finding: ``status ->
    complete``, stamping ``completed_at`` if it was never set. Returns the
    number of records repaired and saved."""
    by_id = {r.worktree_id: r for r in records}
    now_iso = datetime.now().isoformat(timespec="seconds")
    fixed = 0
    for finding in findings:
        record = by_id.get(finding.worktree_id)
        if record is None:
            continue
        record.status = "complete"
        if not getattr(record, "completed_at", None):
            record.completed_at = now_iso
        tracking.save_record(record)
        fixed += 1
    return fixed


def reconcile_stale_active(records, *, apply: bool) -> tuple[list[StaleActiveFinding], int]:
    """Doctor's pass-6 entry point: find, then (if ``apply``) repair.

    Resolves the repo config + liveness set itself so ``maintenance_cli``
    stays a thin orchestrator; a repo config that can't be resolved (no
    anchor registered for this project) skips the pass rather than raising.
    """
    try:
        repo_cfg = cfg.load_config().default_repo
    except Exception:
        return [], 0
    from . import __main__ as _core  # lazy: avoid a module-load cycle
    findings = find_stale_active_records(
        records,
        active_paths=_core._build_active_paths(records),
        remote=repo_cfg.remote,
        default_branch=repo_cfg.default_branch,
    )
    return findings, (apply_stale_active_repairs(records, findings) if apply else 0)


# --------------------------------------------------------------------------- #
# Pass 3: empty session-state GC
# --------------------------------------------------------------------------- #
@dataclass
class EmptyShell:
    session_id: str
    age_h: float


def find_empty_session_shells(
    session_state_dir: Path,
    *,
    min_age_h: float = 2.0,
    exclude_ids: frozenset[str] = frozenset(),
) -> list[EmptyShell]:
    """Session-state dirs whose ``events.jsonl`` has **no** ``user.message`` --
    empty shells. Guards: minimum age, no lock file, and not in *exclude_ids*
    (the current session + every registered session)."""
    out: list[EmptyShell] = []
    if not session_state_dir.exists():
        return out
    now = time.time()
    for e in session_state_dir.iterdir():
        if not e.is_dir() or e.name in exclude_ids:
            continue
        ef = e / "events.jsonl"
        if not ef.exists():
            continue
        if (e / "session.lock").exists() or (e / "live.lock").exists():
            continue
        try:
            with ef.open(encoding="utf-8", errors="replace") as f:
                if any('"user.message"' in line for line in f):
                    continue
            age_h = (now - e.stat().st_mtime) / 3600
        except OSError:
            continue
        if age_h < min_age_h:
            continue
        out.append(EmptyShell(e.name, age_h))
    return out


def purge_store_rows(store_db: Path, session_ids: list[str]) -> int:
    """Delete every row keyed to *session_ids* from the session store. Returns
    the total rows removed. Best-effort: a locked/absent DB removes nothing."""
    if not session_ids or not store_db.exists():
        return 0
    ph = ",".join("?" * len(session_ids))
    total = 0
    try:
        con = sqlite3.connect(str(store_db), timeout=10)
    except sqlite3.Error:
        return 0
    try:
        con.execute("PRAGMA busy_timeout=8000")
        sid_tables = []
        for (name,) in con.execute("SELECT name FROM sqlite_master WHERE type='table'"):
            cols = [c[1] for c in con.execute(f"PRAGMA table_info({name})")]
            if "session_id" in cols:
                sid_tables.append(name)
        con.execute("BEGIN")
        for t in sid_tables:
            try:
                cur = con.execute(f"DELETE FROM {t} WHERE session_id IN ({ph})", session_ids)
                total += cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
            except sqlite3.Error:
                pass  # FTS/virtual tables may reject a plain DELETE -- skip
        try:
            cur = con.execute(f"DELETE FROM sessions WHERE id IN ({ph})", session_ids)
            total += cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
        except sqlite3.Error:
            pass
        con.execute("COMMIT")
    except sqlite3.Error:
        try:
            con.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        return 0
    finally:
        con.close()
    return total


def gc_empty_shells(
    session_state_dir: Path,
    store_db: Path,
    shells: list[EmptyShell],
    *,
    apply: bool,
) -> dict:
    """Remove the given empty shells' directories and purge their store rows."""
    ids = [s.session_id for s in shells]
    removed_dirs = 0
    removed_rows = 0
    if apply:
        for sid in ids:
            shutil.rmtree(session_state_dir / sid, ignore_errors=True)
            if not (session_state_dir / sid).is_dir():
                removed_dirs += 1
        removed_rows = purge_store_rows(store_db, ids)
    return {
        "count": len(ids),
        "removed_dirs": removed_dirs,
        "removed_rows": removed_rows,
        "ids": ids,
    }


# --------------------------------------------------------------------------- #
# Pass 4: alignment audit (report-only)
# --------------------------------------------------------------------------- #
def _norm(p: str) -> str:
    return p.rstrip("/\\").lower()


def _session_cwd(session_state_dir: Path, session_id: str) -> str | None:
    ws = session_state_dir / session_id / "workspace.yaml"
    if not ws.exists():
        return None
    try:
        data = yaml.safe_load(ws.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data.get("cwd") if isinstance(data, dict) else None


def audit_alignment(records, session_state_dir: Path) -> list[dict]:
    """Worktrees with no own session but a ``parent_session`` whose cwd differs
    from the worktree's own path -- the drift that made a tab open in another
    worktree's directory (fixed on the resume side, surfaced here)."""
    out: list[dict] = []
    for r in records:
        if getattr(r, "sessions", None) or not getattr(r, "parent_session", None):
            continue
        pcwd = _session_cwd(session_state_dir, r.parent_session)
        if pcwd and _norm(pcwd) != _norm(r.worktree_path):
            out.append({
                "worktree_id": r.worktree_id,
                "parent_session": r.parent_session,
                "parent_cwd": pcwd,
            })
    return out


# --------------------------------------------------------------------------- #
# Pass 5: orphaned handoff (head lost to a failed cutover)
# --------------------------------------------------------------------------- #
# Mirrors ``tracking._CONCLUDED_SESSION_STATES`` (kept local so health stays
# self-contained and unit-testable without importing tracking).
_CONCLUDED_STATES = ("handed-off", "concluded")
_HANDED_OFF = "handed-off"
_YIELDED = "yielded"
# A tail in either state leaves the worktree headless (mirrors
# ``tracking._HEAD_INELIGIBLE_STATES`` minus "concluded", which never has a
# successor to wait for): "handed-off" is a cutover whose successor never
# registered; "yielded" is a session that opened a handoff intent (itself
# normal) that was never formally linked to a successor. Both are the same
# "orphaned, nobody ever took head" shape and get the same detection/fix.
_ORPHANABLE_TAIL_STATES = (_HANDED_OFF, _YIELDED)
# Other sessions in the worktree must also be non-head-eligible for the
# derived head to be None -- a "yielded" peer counts here the same as a
# concluded one (see `_ORPHANABLE_TAIL_STATES` above).
_NON_HEAD_STATES = (*_CONCLUDED_STATES, _YIELDED)


@dataclass
class OrphanedHandoff:
    """A worktree whose head was orphaned by a handoff that never completed.

    Carries the ``record`` so the orchestrator can apply the fix inline (as it
    does for stale status); ``session_id`` is the handed-off tail to re-activate.
    """
    record: object
    session_id: str
    age_h: float
    last_stage: int | None = None
    last_stage_name: str | None = None
    reactivated: bool = False

    @property
    def worktree_id(self) -> str:
        return getattr(self.record, "worktree_id", "?")


def _parse_iso_epoch(value) -> float | None:
    """Parse an ISO-8601 stamp (naive-local or tz-aware) to an epoch, or None."""
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value).timestamp()
    except (ValueError, OSError):
        return None


def _record_last_activity(record, orphan_entry=None) -> float | None:
    """Newest known activity epoch across a record's timestamp fields and the
    given (or, absent one, tail) session entry. ``None`` when nothing
    parseable is present (so staleness cannot be proven -- the detector then
    conservatively skips the record)."""
    stamps = [
        getattr(record, "last_resumed_at", None),
        getattr(record, "started_at", None),
        getattr(record, "session_state_at", None),
        getattr(record, "mux_live_at", None),
        getattr(record, "bound_live_at", None),
    ]
    sessions = getattr(record, "sessions", None) or []
    tail = orphan_entry if orphan_entry is not None else (sessions[-1] if sessions else None)
    if tail is not None:
        stamps.append(getattr(tail, "ended_at", None))
        stamps.append(getattr(tail, "started_at", None))
        # A "yielded" tail's own handoff-open time is its most recent asserted
        # activity -- `open_handoff()` only runs on an *active* session, so a
        # long-dormant `last_resumed_at` must never outrank a handoff this
        # tail opened moments ago (the orphan-handoff detector would otherwise
        # misjudge a freshly yielded, still-in-flight handoff as stale).
        tail_id = getattr(tail, "session_id", None)
        for handoff in getattr(record, "handoffs", None) or ():
            if getattr(handoff, "predecessor", None) == tail_id:
                stamps.append(getattr(handoff, "opened_at", None))
                # `associate_handoff_candidate()` leaves a handoff "pending"
                # while a successor is mid-pickup, and that successor may not
                # yet make `mux_live`/`bound_live` true -- a recent candidate
                # association is itself activity just as fresh as a recent
                # `opened_at`, so it must count the same way here.
                stamps.append(getattr(handoff, "candidate_at", None))
    epochs = [e for e in (_parse_iso_epoch(s) for s in stamps) if e is not None]
    return max(epochs) if epochs else None


def find_orphaned_handoffs(
    records,
    *,
    now: float | None = None,
    min_age_h: float = 0.5,
) -> list[OrphanedHandoff]:
    """Worktrees whose head was orphaned by a handoff whose successor never came.

    A handoff-cutover concludes the predecessor ``handed-off`` *before* the
    successor registers, so a transient headless window is **normal and correct**
    (``resolved_head_session`` is intentionally None then). This detects the
    *permanent* case -- the successor never materialized (e.g. it died on the CLI
    resume-hang before ``register-session``/``link-succession`` landed), **or**
    the equivalent "yielded" case -- a session opened a handoff intent (itself
    normal) that was never formally linked to a successor, and nobody ever
    reclaimed head -- with conservative guards so a healthy in-flight cutover
    is **never** touched:

      * the worktree is non-terminal (``status == "active"``);
      * ``resolved_head_session`` is ``None`` -- the ledger-aware resolution
        (replaying ``head_transitions`` when present, else the legacy
        head/newest-eligible fallback), not merely "is the OLDEST session
        head-eligible": an older session left ``active`` doesn't save a
        worktree whose authoritative head -- e.g. a later session that has
        since yielded its own handoff -- is no longer eligible. (Lightweight
        test doubles without a ``resolved_head_session`` fall back to a naive
        any-head-eligible scan over ``_NON_HEAD_STATES``.);
      * the **tail** session is ``handed-off`` or ``yielded`` with **no linked
        successor** (the cutover/handoff began but no successor was ever
        recorded -- see ``_ORPHANABLE_TAIL_STATES``);
      * the worktree is **dark** -- ``mux_live`` and ``bound_live`` both falsy
        (nothing live that could be a successor starting up); and
      * its last activity is **stale** past ``min_age_h`` (a successor would have
        registered long ago -- unprovable staleness conservatively skips).

    Report-only. The orchestrator re-activates the tail (``handed-off``/
    ``yielded`` -> ``active``) under ``--fix`` so ``resolved_head_session``
    derives it again and the worktree becomes resumable -- the mechanical form
    of the manual repair.
    """
    now = time.time() if now is None else now
    out: list[OrphanedHandoff] = []
    project = cfg.active_project()
    for r in records:
        if getattr(r, "status", None) != "active":
            continue
        sessions = getattr(r, "sessions", None) or []
        if not sessions:
            continue
        if hasattr(r, "resolved_head_session"):
            # The real ledger-aware resolution (replays `head_transitions`
            # when present, else the legacy head/newest-eligible fallback):
            # an older session left ``active`` doesn't save a record whose
            # AUTHORITATIVE head -- e.g. a later session that has since
            # yielded its own handoff -- is no longer eligible. A naive
            # "any non-eligible session" scan would miss that case.
            if r.resolved_head_session is not None:
                continue
            # The orphan candidate is the ledger's own named (now-ineligible)
            # session when one exists -- an earlier entry can stay the list
            # TAIL while a LATER session is the one the ledger actually
            # named and left orphaned (e.g. old registers, new
            # registers/concludes, old is explicitly re-adopted, old
            # yields): the list-tail fallback only applies to legacy/
            # explicitly-cleared ledgers with no named transition at all.
            replayed = r.replayed_head_transition
            named = (
                r.session_entry(replayed.session_id)
                if replayed is not None and replayed.session_id is not None
                else None
            )
            tail = named if named is not None else sessions[-1]
        else:
            # Lightweight test doubles (e.g. SimpleNamespace fixtures) don't
            # implement ledger-aware resolution; fall back to the naive scan
            # -- any head-eligible session => the head resolves to it.
            if any(getattr(s, "state", "active") not in _NON_HEAD_STATES
                   for s in sessions):
                continue
            tail = sessions[-1]
        if getattr(tail, "state", None) not in _ORPHANABLE_TAIL_STATES:
            continue
        # A linked successor is a different (completed/handled) shape; only an
        # unlinked handed-off/yielded tail is the "successor never came" case.
        if getattr(tail, "successor", None):
            continue
        if getattr(r, "mux_live", None) or getattr(r, "bound_live", None):
            continue
        last = _record_last_activity(r, orphan_entry=tail)
        if last is None or (now - last) < min_age_h * 3600:
            continue
        last_stage = None
        last_stage_name = None
        if project:
            try:
                for event in handoff_trace.read_trace(project, getattr(r, "worktree_id", "")):
                    raw_stage = event.get("stage")
                    try:
                        stage_num = int(raw_stage) if raw_stage is not None else None
                    except (TypeError, ValueError):
                        stage_num = None
                    if stage_num is None:
                        continue
                    if last_stage is None or stage_num >= last_stage:
                        last_stage = stage_num
                        last_stage_name = (
                            str(event.get("stage_name") or "").strip()
                            or HANDOFF_STAGES.get(stage_num)
                        )
            except Exception:
                last_stage = None
                last_stage_name = None
        out.append(OrphanedHandoff(
            record=r,
            session_id=getattr(tail, "session_id", ""),
            age_h=(now - last) / 3600,
            last_stage=last_stage,
            last_stage_name=last_stage_name,
        ))
    return out


def reactivate_orphaned_handoff(orphan: OrphanedHandoff) -> bool:
    """The orchestrator's ``--fix`` mutate for one :func:`find_orphaned_handoffs`
    finding: reactivates the orphaned tail (``handed-off``/``yielded`` ->
    ``active``), cancels the stale pending handoff that produced that state,
    sets head, and bumps ``lifecycle_revision``. Returns whether it repaired
    anything (sets ``orphan.reactivated`` on success).

    ``orphan.record`` is a possibly-stale snapshot (``find_orphaned_handoffs``
    runs outside any lock), so this re-reads and re-validates under the
    record's own lock before mutating -- a live session may have linked or
    otherwise moved this handoff on in the interim. ``set_head_session`` is a
    no-op when the ledger already names this candidate (true here, since it
    IS the orphan candidate), so the revision bump is explicit -- otherwise a
    stale equal-revision writer could later silently undo the repair.
    """
    yaml_path = orphan.record.yaml_path
    with tracking._RecordLock(yaml_path, blocking=False) as lk:
        if not lk.acquired:
            return False  # contended -- a live writer owns this record now
        fresh = tracking.load_record(yaml_path)
        if not any(o.session_id == orphan.session_id
                   for o in find_orphaned_handoffs([fresh])):
            return False
        entry = fresh.session_entry(orphan.session_id)
        if entry is None or entry.state not in _ORPHANABLE_TAIL_STATES:
            return False
        entry.state = "active"
        tracking._cancel_pending_handoffs(fresh)
        tracking.set_head_session(fresh, orphan.session_id, save=False)
        tracking._next_lifecycle_revision(fresh, orphan.session_id)
        tracking.save_record(fresh)
    orphan.reactivated = True
    return True


# --------------------------------------------------------------------------- #
# Shared helpers for the orchestrator
# --------------------------------------------------------------------------- #
def default_store_db(session_state_dir: Path) -> Path:
    """``session-store.db`` sits next to the ``session-state`` directory."""
    return session_state_dir.parent / "session-store.db"


def registered_session_ids(records) -> set[str]:
    """Every session id referenced by any record's registry -- never GC these."""
    ids: set[str] = set()
    for r in records:
        for s in (getattr(r, "sessions", None) or []):
            sid = getattr(s, "session_id", None)
            if sid:
                ids.add(sid)
    return ids


def find_stale_head_caches(records) -> list:
    """Records whose materialized head cache disagrees with ledger replay."""
    stale = []
    for record in records:
        transitions = getattr(record, "head_transitions", None) or []
        if not transitions:
            continue
        transition = record.replayed_head_transition
        if (
            record.head_session != record.replayed_head_session
            or transition is None
            or record.head_revision != transition.revision
        ):
            stale.append(record)
    return stale
