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

# Mirrors the serializer's quoting predicate in ``tracking.save_record`` so a
# repaired scalar is quoted on exactly the same chars the writer would have.
_NEEDS_QUOTE = set(":{}[]#&*!|>'\",")
# Free-text scalar fields the (older) serializer could emit unquoted.
_QUOTABLE_FIELDS = ("title", "summary")


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


@dataclass
class OrphanedHandoff:
    """A worktree whose head was orphaned by a handoff that never completed.

    Carries the ``record`` so the orchestrator can apply the fix inline (as it
    does for stale status); ``session_id`` is the handed-off tail to re-activate.
    """
    record: object
    session_id: str
    age_h: float
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


def _record_last_activity(record) -> float | None:
    """Newest known activity epoch across a record's timestamp fields and its
    tail session entry. ``None`` when nothing parseable is present (so staleness
    cannot be proven -- the detector then conservatively skips the record)."""
    stamps = [
        getattr(record, "last_resumed_at", None),
        getattr(record, "started_at", None),
        getattr(record, "session_state_at", None),
        getattr(record, "mux_live_at", None),
        getattr(record, "bound_live_at", None),
    ]
    sessions = getattr(record, "sessions", None) or []
    if sessions:
        tail = sessions[-1]
        stamps.append(getattr(tail, "ended_at", None))
        stamps.append(getattr(tail, "started_at", None))
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
    resume-hang before ``register-session``/``link-succession`` landed) -- with
    conservative guards so a healthy in-flight cutover is **never** touched:

      * the worktree is non-terminal (``status == "active"``);
      * it has sessions, and **none** is non-concluded (so the derived head is
        None -- there is no current session);
      * the **tail** session is ``handed-off`` with **no linked successor** (the
        cutover began but no successor was ever recorded);
      * the worktree is **dark** -- ``mux_live`` and ``bound_live`` both falsy
        (nothing live that could be a successor starting up); and
      * its last activity is **stale** past ``min_age_h`` (a successor would have
        registered long ago -- unprovable staleness conservatively skips).

    Report-only. The orchestrator re-activates the tail (``handed-off`` ->
    ``active``) under ``--fix`` so ``resolved_head_session`` derives it again and
    the worktree becomes resumable -- the mechanical form of the manual repair.
    """
    now = time.time() if now is None else now
    out: list[OrphanedHandoff] = []
    for r in records:
        if getattr(r, "status", None) != "active":
            continue
        sessions = getattr(r, "sessions", None) or []
        if not sessions:
            continue
        # Any non-concluded session => the head resolves to it => not orphaned.
        if any(getattr(s, "state", "active") not in _CONCLUDED_STATES
               for s in sessions):
            continue
        tail = sessions[-1]
        if getattr(tail, "state", None) != _HANDED_OFF:
            continue
        # A linked successor is a different (completed/handled) shape; only an
        # unlinked handed-off tail is the "successor never came" case.
        if getattr(tail, "successor", None):
            continue
        if getattr(r, "mux_live", None) or getattr(r, "bound_live", None):
            continue
        last = _record_last_activity(r)
        if last is None or (now - last) < min_age_h * 3600:
            continue
        out.append(OrphanedHandoff(
            record=r,
            session_id=getattr(tail, "session_id", ""),
            age_h=(now - last) / 3600,
        ))
    return out


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
