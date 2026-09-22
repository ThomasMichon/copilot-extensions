"""Periodic ground-truth audit for the worktree-status accelerator
(agent-worktrees-external-status-accelerator effort, Phase 7 -- ongoing
accuracy monitoring).

Once other consumers (the Tasks-board card, this effort's own in-process
reference consumer, a future cross-venv one) rely on the accelerator's
cache for their whole picture of a worktree, a silent drift between what
the cache reports and reality would be invisible until an operator noticed
something felt wrong. This module gives that drift a signal: read the
durable cache's own snapshot, independently recompute a sample of the same
worktrees via :func:`worktree_status_compute.compute` (bypassing the cache
entirely -- the same ground truth :mod:`worktree_status_compute` itself is),
diff the two, and append one structured JSON line per run to a local log so
an operator (or `worktree-status-audit`'s own exit code, scriptable by a
scheduled task) has both a live check and a durable trail of the
accelerator's refresh cadence and liveness over time.

Never raises past :func:`run_audit`: a per-worktree audit failure is
recorded as that entry's own ``error``, never lets one bad worktree abort
the whole run; telemetry-log write failures are swallowed (best-effort,
same durability posture as the cache's own SQLite writes).

Deliberately imports :mod:`worktree_status_daemon` lazily inside the
functions that need it, never at module scope: this module is imported
eagerly by ``__main__.py`` (for its CLI alias), and the daemon module pulls
in the vendored ``work_coalescing_singleton`` package, which not every
environment importing ``agent_worktrees.__main__`` has installed --
mirrors ``__main__.py``'s own ``cmd_status_monitor`` and
``session_tracking_cli.cmd_worktree_status_bundle``, which do the same.
"""

from __future__ import annotations

import argparse
import json
import random
import sqlite3
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import config as cfg
from . import installer as inst
from . import locks
from . import tracking
from . import worktree_status_compute
from .worktree_status_cache import DEFAULT_TTL_SECONDS


def _core():
    from . import __main__ as core

    return core


CACHE_DB_NAME = "worktree-status-cache.sqlite3"
DEFAULT_LOG_FILENAME = "worktree-status-audit.jsonl"
DEFAULT_SAMPLE_SIZE = 10

#: How far past its own TTL + sweep interval a cache entry's `computed_at`
#: may lag "now" before this audit flags it as unexpectedly stale -- slack
#: absorbs ordinary scheduling jitter (a busy sweep thread, a slow git
#: probe on the worktree ahead of it in line) without false-flagging.
FRESHNESS_SLACK_SECONDS = 60.0


@dataclass
class FieldMismatch:
    check: str
    detail: str


@dataclass
class WorktreeAudit:
    project: str
    worktree_id: str
    had_cache_entry: bool
    cache_age_seconds: float | None
    demand_age_seconds: float | None
    mismatches: list[FieldMismatch] = field(default_factory=list)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and not self.mismatches


@dataclass
class DaemonLiveness:
    lock_present: bool
    rendezvous_present: bool
    responsive: bool | None  # None: no real worktree available to probe with
    error: str | None = None


@dataclass
class AuditReport:
    timestamp: float
    sampled: list[WorktreeAudit]
    daemon: DaemonLiveness
    cache_row_count: int

    @property
    def mismatch_count(self) -> int:
        return sum(1 for w in self.sampled if w.mismatches)

    @property
    def error_count(self) -> int:
        return sum(1 for w in self.sampled if w.error)


def cache_db_path(runtime_home: Path) -> Path:
    return runtime_home / CACHE_DB_NAME


def read_cache_snapshot(db_path: Path) -> list[dict]:
    """Every durable row, read-only.

    Opens its own read-only connection (SQLite URI ``mode=ro``) rather than
    going through :class:`worktree_status_cache.WorktreeStatusCache` --
    this must never create a second writer, warm-restore, or otherwise
    compete with the resident daemon's own connection for anything. Missing
    file, corrupt rows, or any SQLite error degrade to an empty snapshot
    (never raises) -- an audit finding no cache at all is itself meaningful
    (see :data:`DaemonLiveness`), not a reason to abort the run.
    """
    if not db_path.exists():
        return []
    try:
        # `Path.as_uri()` builds a correct `file://` URI on every platform
        # (percent-encodes spaces, handles a Windows drive letter and
        # backslashes) -- a plain f"file:{db_path}" is not a valid URI on
        # Windows (a bare drive letter/backslash path) and can silently
        # misparse, which would make this degrade to an empty snapshot
        # even though the cache file genuinely exists and is readable.
        uri = f"{db_path.resolve().as_uri()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=2)
        conn.row_factory = sqlite3.Row
    except (sqlite3.Error, OSError, ValueError):
        return []
    try:
        try:
            rows = conn.execute(
                "SELECT project, worktree_id, bundle_json, computed_at, demanded_at"
                " FROM worktree_status_cache"
            ).fetchall()
        except sqlite3.Error:
            return []
    finally:
        conn.close()
    out: list[dict] = []
    for row in rows:
        try:
            bundle = json.loads(row["bundle_json"])
        except (ValueError, TypeError):
            bundle = None
        if not isinstance(bundle, dict):
            bundle = None
        out.append({
            "project": row["project"],
            "worktree_id": row["worktree_id"],
            "bundle": bundle,
            "computed_at": row["computed_at"],
            "demanded_at": row["demanded_at"],
        })
    return out


def _all_project_names() -> list[str]:
    try:
        names = inst.read_projects_registry().get("projects", {})
        return [str(name) for name in names] if isinstance(names, dict) else []
    except Exception:
        return []


def _fallback_candidates() -> list[tuple[str, str]]:
    """``(project, worktree_id)`` pairs to sample when the cache is
    cold/empty -- every actively-tracked worktree across every registered
    project, so an audit still means something before anything's ever been
    demanded through the accelerator."""
    candidates: list[tuple[str, str]] = []
    for name in _all_project_names():
        try:
            tracking_path = cfg.project_dir(name) / "worktrees"
            for record in tracking.list_records(tracking_path, status_filter="active"):
                candidates.append((name, record.worktree_id))
        except Exception:
            continue
    return candidates


def select_sample(
    cache_rows: list[dict], *, sample_size: int, rng: random.Random
) -> list[tuple[str, str, dict | None]]:
    """A random rotating sample of ``(project, worktree_id, cache_row)``.

    Seed ``rng`` differently each run (the default constructor already
    does, via system entropy) so repeated scheduled runs eventually cover
    the whole population instead of always picking the same subset.
    Prefers cached entries -- they reflect *real* demand, the identities an
    actual consumer is asking about -- falling back to every actively-
    tracked worktree only when the cache has nothing yet.
    """
    if cache_rows:
        population: list[tuple[str, str, dict | None]] = [
            (row["project"], row["worktree_id"], row) for row in cache_rows
        ]
    else:
        population = [(project, wt_id, None) for project, wt_id in _fallback_candidates()]
    # `random.sample` raises ValueError for a negative count (and this
    # module's own "never raises" contract extends to a misconfigured
    # `--sample` from a caller, not just internal data) -- clamp to 0
    # rather than let a non-positive value reach it.
    sample_size = max(0, sample_size)
    if len(population) <= sample_size:
        return population
    return rng.sample(population, sample_size)


def _fact_value(bundle: dict | None, fact: str) -> tuple[dict | None, bool]:
    """Return ``(value, confirmed)`` for one fact, tolerating a missing/
    malformed bundle or fact entirely (never raises)."""
    if not isinstance(bundle, dict):
        return None, False
    entry = (bundle.get("facts") or {}).get(fact)
    if not isinstance(entry, dict):
        return None, False
    return entry.get("value"), bool(entry.get("confirmed"))


def _diff_git_state(cached_bundle: dict, live_bundle: dict) -> list[FieldMismatch]:
    cached_value, cached_ok = _fact_value(cached_bundle, "git_state")
    live_value, live_ok = _fact_value(live_bundle, "git_state")
    if not cached_ok or not live_ok or not isinstance(cached_value, dict) or not isinstance(live_value, dict):
        # An unconfirmed fact on either side already declares its own
        # uncertainty -- diffing values across a probe that admits it
        # couldn't confirm would produce noise, not a real finding.
        return []
    mismatches = []
    for field_name in ("state", "current_branch", "ahead", "behind", "dirty"):
        if cached_value.get(field_name) != live_value.get(field_name):
            mismatches.append(FieldMismatch(
                check="git_state_accuracy",
                detail=(
                    f"{field_name}: cached={cached_value.get(field_name)!r} "
                    f"live={live_value.get(field_name)!r}"
                ),
            ))
    return mismatches


def _diff_liveness(cached_bundle: dict, live_bundle: dict) -> list[FieldMismatch]:
    cached_value, cached_ok = _fact_value(cached_bundle, "liveness")
    live_value, live_ok = _fact_value(live_bundle, "liveness")
    if not cached_ok or not live_ok or not isinstance(cached_value, dict) or not isinstance(live_value, dict):
        return []
    cached_active = cached_value.get("active")
    live_active = live_value.get("active")
    if cached_active != live_active:
        return [FieldMismatch(
            check="liveness_accuracy",
            detail=f"active: cached={cached_active!r} live={live_active!r}",
        )]
    return []


def _check_identity(
    project: str, worktree_id: str, bundle: dict | None, *, source: str
) -> list[FieldMismatch]:
    if not isinstance(bundle, dict):
        return []
    if bundle.get("project") != project or bundle.get("worktree_id") != worktree_id:
        return [FieldMismatch(
            check="identity_consistency",
            detail=(
                f"{source} bundle declares "
                f"{bundle.get('project')!r}/{bundle.get('worktree_id')!r} "
                f"for key {project!r}/{worktree_id!r}"
            ),
        )]
    return []


def _check_freshness(cache_entry: dict | None, *, now: float) -> list[FieldMismatch]:
    if cache_entry is None:
        return []
    computed_at = cache_entry.get("computed_at")
    if not isinstance(computed_at, (int, float)):
        return []
    # Lazy import: `worktree_status_daemon` pulls in the vendored
    # `work_coalescing_singleton` package, which not every environment
    # importing `agent_worktrees.__main__` has installed (e.g. the
    # standalone `worktree-manager` package's own test suite) -- mirrors
    # `__main__.py`'s own `cmd_status_monitor` and `session_tracking_cli
    # .cmd_worktree_status_bundle`, which import it the same way rather
    # than at module scope.
    from .worktree_status_daemon import SWEEP_INTERVAL_SECONDS
    from .worktree_status_cache import DEMAND_TTL_SECONDS

    # `WorktreeStatusCache.sweep_due` only refreshes an entry while it is
    # still *demanded* (`get_or_refresh` registers demand on every read;
    # the sweep drops that bookkeeping -- and eventually the row itself --
    # once nothing has asked about this worktree within DEMAND_TTL_SECONDS,
    # per that cache's own "an operator who moved on stops paying the
    # sweep cost for it" design). A cache_age past the TTL+sweep+slack
    # bound is therefore only ever a real sweep malfunction while the entry
    # is still within its demand window; once demand itself has aged past
    # DEMAND_TTL_SECONDS, a growing cache_age is the cache correctly
    # ceasing to refresh a worktree nobody is asking about (it will be
    # evicted, not endlessly kept warm) -- flagging that here would be a
    # false positive for exactly the same "confuse a real caller's own
    # tolerated resting state for an outage" mistake this module's
    # daemon-liveness check was redesigned to avoid.
    #
    # Use the cache's own exact DEMAND_TTL_SECONDS cutoff here, not a
    # widened one (Copilot review, 2026-09-21, round 2): the persisted
    # `demanded_at` can lag true in-memory demand by roughly one TTL+sweep
    # cycle (a fresh hit extends demand only in memory -- see
    # `get_or_refresh`'s own docstring), which could in principle let a
    # still-genuinely-demanded entry's persisted timestamp read just past
    # this cutoff and get wrongly exempted here. But padding the cutoff to
    # cover that narrow, self-correcting race (the next sweep tick, or the
    # next audit run, observes an updated `demanded_at` regardless) would
    # systematically reintroduce the opposite, more damaging false
    # positive this whole check exists to avoid: a row genuinely past
    # `DEMAND_TTL_SECONDS` is -- by the cache's own real, unpadded cutoff
    # -- no longer being swept at all, so flagging it during any padding
    # window would be exactly the "expected resting state reported as an
    # outage" mistake again, just delayed and confined to that window
    # instead of eliminated. The narrow lag risk is accepted as the lesser
    # cost.
    demanded_at = cache_entry.get("demanded_at")
    if isinstance(demanded_at, (int, float)) and now - demanded_at > DEMAND_TTL_SECONDS:
        return []

    age = now - computed_at
    bound = DEFAULT_TTL_SECONDS + SWEEP_INTERVAL_SECONDS + FRESHNESS_SLACK_SECONDS
    if age > bound:
        return [FieldMismatch(
            check="cache_freshness_bounds",
            detail=(
                f"cached entry is {age:.0f}s old, exceeding the {bound:.0f}s "
                "bound (TTL + sweep interval + slack) while still within "
                "its demand window -- the sweep may not be keeping this "
                "actively-demanded entry warm"
            ),
        )]
    return []


def _safe_age(cache_entry: dict | None, key: str, *, now: float) -> float | None:
    """``now - cache_entry[key]``, tolerating a missing/malformed field
    (an older schema row, a corrupt entry) -- never raises, per this
    module's "never raises past run_audit" contract."""
    if cache_entry is None:
        return None
    value = cache_entry.get(key)
    if not isinstance(value, (int, float)):
        return None
    return now - value


def audit_one(
    project: str, worktree_id: str, cache_entry: dict | None, *, now: float
) -> WorktreeAudit:
    """Audit one ``(project, worktree_id)`` against its cache entry (if any).

    Always recomputes ground truth via :func:`worktree_status_compute
    .compute` -- the exact same fact-assembly a cache miss would run,
    bypassing :class:`worktree_status_cache.WorktreeStatusCache` and the
    daemon entirely, so a mismatch here can only mean the *cache* drifted
    from what a fresh compute would produce, never that this audit's own
    ground truth is itself cache-derived.
    """
    mismatches: list[FieldMismatch] = []
    cached_bundle = cache_entry.get("bundle") if cache_entry else None
    mismatches += _check_identity(project, worktree_id, cached_bundle, source="cached")
    mismatches += _check_freshness(cache_entry, now=now)

    cache_age = _safe_age(cache_entry, "computed_at", now=now)
    demand_age = _safe_age(cache_entry, "demanded_at", now=now)

    try:
        live_bundle = worktree_status_compute.compute(project, worktree_id)
    except Exception as exc:
        return WorktreeAudit(
            project=project, worktree_id=worktree_id,
            had_cache_entry=cache_entry is not None,
            cache_age_seconds=cache_age, demand_age_seconds=demand_age,
            mismatches=mismatches, error=f"{type(exc).__name__}: {exc}",
        )

    mismatches += _check_identity(project, worktree_id, live_bundle, source="live")
    if cached_bundle is not None:
        mismatches += _diff_git_state(cached_bundle, live_bundle)
        mismatches += _diff_liveness(cached_bundle, live_bundle)

    return WorktreeAudit(
        project=project, worktree_id=worktree_id,
        had_cache_entry=cache_entry is not None,
        cache_age_seconds=cache_age, demand_age_seconds=demand_age,
        mismatches=mismatches,
    )


def check_daemon_liveness(
    lock_path: Path,
    *,
    probe: tuple[str, str] | None = None,
    ensure_monitor: Callable[[], bool] | None = None,
    boot_wait_s: float | None = None,
) -> DaemonLiveness:
    """Whether the resident status-monitor's worktree-status daemon
    answers a request the way a real consumer actually experiences it --
    not a bare "is a resident daemon already up right now" snapshot.

    Every real caller (``worktree-status-bundle``'s own direct-compute
    fallback path, a future cross-venv consumer) reaches the daemon
    through :func:`worktree_status_daemon.status_with_boot`: dial, and if
    nothing is currently publishing a worktree-status endpoint, trigger
    ``ensure_monitor`` and wait up to its own ``BOOT_WAIT_S`` before giving
    up. The resident monitor idle-exits when nothing has demanded it
    recently (see `cmd_status_monitor`'s own empty-strike logic) -- a bare
    probe with no boot path would then report "unresponsive" for a
    perfectly healthy accelerator that simply hasn't been asked about
    anything in a while, which is not a fault; it is *this exact system's
    own resting state* between callers, and every real caller already
    tolerates it via ``ensure_monitor``. Using the same path here means a
    finding actually means what it says: the accelerator failed a real
    consumer's own request, not merely "no one had asked recently".

    ``probe``, when given a real ``(project, worktree_id)`` pair, is what
    makes this call meaningfully answerable at all -- without one (nothing
    to probe with, an empty population), this still gives an idle-exited
    monitor the same boot-and-wait chance to come back (when
    ``ensure_monitor`` is available) before taking a static snapshot, but
    never reports ``responsive=False`` for it: without a real request
    there is nothing to have actually failed, only "no one to ask", so
    this always reports ``responsive=None`` rather than treating an
    absent/resting monitor as an outage.

    ``boot_wait_s``, when given, overrides the wait window used for both
    branches above (default: ``worktree_status_daemon.BOOT_WAIT_S``) --
    a pure testability knob so a test can exercise a real poll loop
    without paying the full production wait; production callers never
    need to pass it.
    """
    # Lazy import: see `_check_freshness`'s own comment -- keeps this
    # module importable without the vendored `work_coalescing_singleton`
    # package installed.
    from . import worktree_status_daemon

    def _live_endpoint(data: dict | None) -> tuple[str, int, str] | None:
        if data is None or not locks.lock_is_live(data):
            return None
        return worktree_status_daemon.endpoint_from_rendezvous(data)

    effective_boot_wait_s = (
        worktree_status_daemon.BOOT_WAIT_S if boot_wait_s is None else boot_wait_s
    )

    if probe is None:
        data = locks.read_lock(lock_path)
        endpoint = _live_endpoint(data)
        if endpoint is None and ensure_monitor is not None:
            # Boot-only wait: give an idle-exited monitor the same chance
            # to come back that a real caller's `status_with_boot` would
            # give it -- but skip issuing an actual coalesced request,
            # since there is no real (project, worktree_id) pair to ask
            # about. Mirrors that function's own poll loop/timeout.
            started = time.time()
            ensure_monitor()
            while endpoint is None and time.time() - started < effective_boot_wait_s:
                time.sleep(0.1)
                data = locks.read_lock(lock_path)
                endpoint = _live_endpoint(data)
        return DaemonLiveness(
            lock_present=data is not None,
            rendezvous_present=endpoint is not None,
            responsive=None,
        )

    project, worktree_id = probe
    reached_fallback = False

    def _fallback() -> dict:
        nonlocal reached_fallback
        reached_fallback = True
        return {}

    def _read_live_lock() -> dict | None:
        # `status_with_boot`'s own dial step treats any syntactically
        # parseable rendezvous as reachable -- it does not check whether
        # the lock's owner PID is still alive. A crashed monitor that
        # never cleaned up its own lock file would otherwise look
        # "dialable" forever, so this probe would keep trying (and
        # failing) to reach a dead process instead of ever triggering
        # `ensure_monitor` to boot a live replacement. Reject a non-live
        # owner here (same `lock_is_live` guard `check_daemon_liveness`'s
        # own static-read branch already applies) so a stale lock is
        # treated exactly like no lock at all.
        data = locks.read_lock(lock_path)
        if data is not None and not locks.lock_is_live(data):
            return None
        return data

    worktree_status_daemon.status_with_boot(
        read_lock_data=_read_live_lock,
        ensure_monitor=ensure_monitor,
        key=worktree_status_daemon.coalescing_key(project, worktree_id),
        payload={"project": project, "worktree_id": worktree_id},
        fallback=_fallback,
        boot_wait_s=effective_boot_wait_s,
    )
    data_after = locks.read_lock(lock_path)
    lock_present = data_after is not None
    rendezvous_present = (
        lock_present
        and locks.lock_is_live(data_after)
        and worktree_status_daemon.endpoint_from_rendezvous(data_after) is not None
    )
    return DaemonLiveness(
        lock_present=lock_present,
        rendezvous_present=rendezvous_present,
        responsive=not reached_fallback,
    )


def report_to_dict(report: AuditReport) -> dict:
    return {
        "version": 1,
        "timestamp": report.timestamp,
        "cache_row_count": report.cache_row_count,
        "sampled_count": len(report.sampled),
        "mismatch_count": report.mismatch_count,
        "error_count": report.error_count,
        "daemon": asdict(report.daemon),
        "entries": [
            {
                "project": w.project,
                "worktree_id": w.worktree_id,
                "had_cache_entry": w.had_cache_entry,
                "cache_age_seconds": w.cache_age_seconds,
                "demand_age_seconds": w.demand_age_seconds,
                "ok": w.ok,
                "mismatches": [asdict(m) for m in w.mismatches],
                "error": w.error,
            }
            for w in report.sampled
        ],
    }


def _append_log(log_path: Path, payload: dict) -> None:
    """Append one JSON line -- best-effort, mirrors the cache's own
    degrade-on-failure durability contract (telemetry is a nice-to-have,
    never a requirement for the audit itself to have run)."""
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, separators=(",", ":")) + "\n")
    except OSError:
        pass


def run_audit(
    *,
    runtime_home: Path,
    sample_size: int = DEFAULT_SAMPLE_SIZE,
    log_path: Path | None = None,
    rng: random.Random | None = None,
    ensure_monitor: Callable[[], bool] | None = None,
) -> AuditReport:
    """Run one full audit pass: snapshot the cache, sample it, diff each
    sampled entry against a fresh recompute, probe the daemon, and
    (best-effort) append a telemetry line to ``log_path``.

    ``ensure_monitor``, when given, is threaded straight through to
    :func:`check_daemon_liveness` -- see that function's own docstring for
    why booting (not just snapshotting) reflects what a real caller
    actually experiences.

    Never raises: every internal step already degrades on its own failure
    (an unreadable cache -> empty snapshot; a per-worktree compute
    exception -> that entry's own ``error``; a telemetry-log write failure
    -> silently skipped).
    """
    rng = rng or random.Random()
    now = time.time()
    cache_rows = read_cache_snapshot(cache_db_path(runtime_home))
    sample = select_sample(cache_rows, sample_size=sample_size, rng=rng)
    audits = [audit_one(project, wt_id, entry, now=now) for project, wt_id, entry in sample]
    probe = (audits[0].project, audits[0].worktree_id) if audits else None
    daemon = check_daemon_liveness(
        runtime_home / "status-monitor.lock", probe=probe, ensure_monitor=ensure_monitor
    )
    report = AuditReport(
        timestamp=now, sampled=audits, daemon=daemon, cache_row_count=len(cache_rows)
    )
    if log_path is not None:
        _append_log(log_path, report_to_dict(report))
    return report


def add_parsers(sub) -> None:
    p = sub.add_parser(
        "worktree-status-audit",
        help=(
            "Audit the worktree-status accelerator's cache against a fresh "
            "ground-truth recompute for a sample of worktrees, and append a "
            "telemetry line to a local log (JSON). Exit code is nonzero "
            "when any mismatch or per-worktree error was found -- "
            "scriptable by a scheduled task."
        ),
    )
    p.add_argument(
        "--sample", type=int, default=DEFAULT_SAMPLE_SIZE,
        help=f"How many worktrees to audit this run (default: {DEFAULT_SAMPLE_SIZE})",
    )
    p.add_argument(
        "--log-path", dest="log_path", default=None,
        help="Override the telemetry log path (default: <runtime home>/"
        f"{DEFAULT_LOG_FILENAME})",
    )
    p.add_argument(
        "--no-log", dest="no_log", action="store_true",
        help="Run the audit without appending a telemetry log line",
    )
    p.add_argument(
        "--seed", type=int, default=None,
        help="Seed the sample's RNG (reproducible runs, e.g. for tests)",
    )


def cmd_worktree_status_audit(args: argparse.Namespace) -> int:
    """``worktree-status-audit`` -- ground-truth audit for the worktree-
    status accelerator, JSON out (see :func:`run_audit`'s own docstring for
    the full contract).

    Exit code: ``0`` when every sampled entry matched its fresh recompute
    and the daemon (when probed) answered; ``1`` when any mismatch or
    per-worktree error was found, or the daemon failed to answer a real
    probe -- meant to be scriptable by a scheduled task, not just read by a
    human.
    """
    core = _core()
    runtime_home = core._aw_runtime_home()
    if getattr(args, "no_log", False):
        log_path = None
    elif getattr(args, "log_path", None):
        log_path = Path(args.log_path)
    else:
        log_path = runtime_home / DEFAULT_LOG_FILENAME

    seed = getattr(args, "seed", None)
    rng = random.Random(seed) if seed is not None else random.Random()

    # Mirror `session_tracking_cli.cmd_worktree_status_bundle`'s own
    # daemon-first opt-out check exactly: this audit's daemon probe uses
    # the same production boot path, so it must honor the same opt-out
    # (`AGENT_WORKTREES_STATUS_MONITOR=0`) that disables the resident
    # monitor -- an operator who's turned it off deliberately should never
    # have this audit spawn one anyway.
    ensure_monitor = core._ensure_status_monitor if core._status_monitor_enabled() else None

    report = run_audit(
        runtime_home=runtime_home,
        sample_size=getattr(args, "sample", DEFAULT_SAMPLE_SIZE),
        log_path=log_path,
        rng=rng,
        ensure_monitor=ensure_monitor,
    )
    core._json_output(report_to_dict(report))
    daemon_failed = report.daemon.responsive is False
    return 1 if (report.mismatch_count or report.error_count or daemon_failed) else 0

