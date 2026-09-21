"""Reap / cleanup-support CLI surfaces extracted from ``__main__``."""

from __future__ import annotations

import argparse
import os
import time
from pathlib import Path

from . import activity, disposition_history, finalize as fin, git_ops, handoff_trace, locks, prune, procs, sessions, tracking
from . import claimant as claimant_mod
from . import config as cfg


def _core():
    from . import __main__ as core
    return core


def _core_helper(name: str, local):
    candidate = getattr(_core(), name, None)
    if callable(candidate) and candidate is not local:
        return candidate
    return local

REAP_SHELL_GRACE_SECS = 3600
_NO_AUTO_CLEAN_ENV = "AGENT_WORKTREES_NO_AUTO_CLEAN"
_AUTO_CLEAN_GRACE_ENV = "AGENT_WORKTREES_AUTO_CLEAN_GRACE_SECS"

def _enumerate_launcher_shells(): return _core()._enumerate_launcher_shells()
def _apply_tracking_override(*args, **kwargs): return _core()._apply_tracking_override(*args, **kwargs)
def _build_active_paths(*args, **kwargs): return _core()._build_active_paths(*args, **kwargs)
def _iso_epoch(*args, **kwargs): return _core()._iso_epoch(*args, **kwargs)
def _json_output(*args, **kwargs): return _core()._json_output(*args, **kwargs)
def _normalize_path(*args, **kwargs): return _core()._normalize_path(*args, **kwargs)
def _reap_worktree(*args, **kwargs): return _core()._reap_worktree(*args, **kwargs)
def _revalidate_cleanup_safety(*args, **kwargs): return _core()._revalidate_cleanup_safety(*args, **kwargs)
def reap_orphan_mux_sessions(*args, **kwargs): return _core().reap_orphan_mux_sessions(*args, **kwargs)
def select_orphan_launcher_shells(*args, **kwargs): return _core().select_orphan_launcher_shells(*args, **kwargs)


def add_parsers(sub) -> None:
    p = sub.add_parser("reap-sessions", help="Reap leaked tmux/psmux sessions whose worktree is finalized, gone, or untracked AND has been idle past the grace window (never touches attached, active, or busy sessions)")
    p.add_argument("--dry-run", action="store_true", help="Report what would be reaped without killing anything")
    p.add_argument("--id", default=None, help="Target a single worktree id; same spare-attached/active/busy predicate as the full sweep")
    p.add_argument("--grace-hours", type=float, default=None, help="Idle window before a finalized/idle session is eligible (default 6h); a busy session is never reaped")
    p.add_argument("--json", action="store_true", help="Emit a single JSON result object")
    p = sub.add_parser("reap-shells", help="Reap orphaned agent-worktrees launcher shells (pwsh/python left by a force-closed terminal). Reports candidates by default; only kills with --yes. Positive-signature + service-safe + idle-gated.")
    p.add_argument("--yes", action="store_true", help="Actually terminate the shells (default is a dry-run report -- nothing is killed without this flag)")
    p.add_argument("--grace-hours", type=float, default=None, help="Minimum age before an orphaned shell is eligible (default 1h)")
    p.add_argument("--json", action="store_true", help="Emit a single JSON result object")

def _enumerate_launcher_shells_posix() -> list[dict] | None:
    proc = Path("/proc")
    try:
        entries = [e for e in proc.iterdir() if e.name.isdigit()]
    except OSError:
        return None
    try:
        clk = os.sysconf("SC_CLK_TCK")
    except (ValueError, OSError):
        clk = 100
    boot = _proc_boot_time()
    procs: list[dict] = []
    for entry in entries:
        pid = int(entry.name)
        try:
            comm = (entry / "comm").read_text(errors="ignore").strip().lower()
        except OSError:
            continue
        # No name filter here: candidate selection (name + positive command-
        # line signature) happens in select_orphan_launcher_shells, but the
        # parent-alive check needs to walk the full ancestor chain up to the
        # real controlling terminal/session leader, not just launcher/witness
        # images. See _ancestor_chain_intact.
        try:
            cmdline = (
                (entry / "cmdline")
                .read_bytes()
                .replace(b"\x00", b" ")
                .decode(errors="ignore")
                .strip()
            )
        except OSError:
            cmdline = ""
        ppid, sid, start_ticks = -1, -1, None
        try:
            stat = (entry / "stat").read_text(errors="ignore")
            rparen = stat.rfind(")")
            rest = stat[rparen + 1 :].split()
            # After comm: state(0) ppid(1) pgrp(2) session(3) ... starttime(19).
            ppid = int(rest[1])
            sid = int(rest[3])
            start_ticks = int(rest[19])
        except (OSError, IndexError, ValueError):
            pass
        create_epoch = boot + (start_ticks / clk) if (boot and start_ticks is not None) else None
        procs.append(
            {
                "pid": pid,
                "ppid": ppid,
                "name": comm,
                "cmdline": cmdline,
                "create_epoch": create_epoch,
                "session_id": sid,
            }
        )
    return procs


def _proc_boot_time() -> float | None:
    try:
        for line in Path("/proc/stat").read_text(errors="ignore").splitlines():
            if line.startswith("btime "):
                return float(line.split()[1])
    except (OSError, ValueError, IndexError):
        pass
    return None


def reap_orphan_launcher_shells(
    *,
    dry_run: bool = True,
    idle_grace_secs: float = REAP_SHELL_GRACE_SECS,
    now: float | None = None,
    processes: list[dict] | None = None,
) -> dict:
    """Reap orphaned agent-worktrees launcher shells (pwsh/python).

    Conservative and dry-run by default: only shells that pass every safety
    layer in :func:`select_orphan_launcher_shells` are candidates, and none are
    killed unless ``dry_run=False``. ``processes`` may be injected for testing.

    Parent liveness is probed against the **real** process table
    (:func:`locks.pid_alive`) whenever this function did the enumeration itself,
    so a launcher whose terminal is a non-enumerated image (``cmd.exe``,
    ``bash``, Windows Terminal) is correctly seen as parented rather than
    orphaned. Injected ``processes`` keep the snapshot-membership fallback, so
    tests stay hermetic and deterministic.

    Returns::

        {"available": bool,                # False when enumeration is impossible
         "reaped":     [pid, ...],         # killed (or would-be, in dry-run)
         "candidates": [{"pid","cmdline"}] # the reap set, for report/preview
         "skipped":    [{"pid","reason"}],
         "errors":     [{"pid","reason"}]}
    """
    now = time.time() if now is None else now
    injected = processes is not None
    proc_list = processes if injected else _enumerate_launcher_shells()
    if proc_list is None:
        return {"available": False, "reaped": [], "candidates": [], "skipped": [], "errors": []}
    reap, skipped = select_orphan_launcher_shells(
        proc_list,
        now=now,
        idle_grace_secs=idle_grace_secs,
        self_pid=os.getpid(),
        pid_alive=None if injected else locks.pid_alive,
    )
    reaped: list[int] = []
    errors: list[dict] = []
    for p in reap:
        pid = int(p["pid"])
        if dry_run:
            reaped.append(pid)
            continue
        if procs.terminate_pid(pid):
            reaped.append(pid)
            try:
                activity.log_event(
                    "launcher_shell_reaped", pid=pid, cmdline=(p.get("cmdline") or "")[:200]
                )
            except Exception:
                pass
        else:
            errors.append({"pid": pid, "reason": "kill failed"})
    candidates = [{"pid": int(p["pid"]), "cmdline": p.get("cmdline") or ""} for p in reap]
    return {
        "available": True, "reaped": reaped, "candidates": candidates, "skipped": skipped,
        "errors": errors,
    }


def cmd_reap_shells(args: argparse.Namespace) -> int:
    """``reap-shells`` -- reap orphaned launcher shells (copilot-extensions #102).

    Reports candidates by default; requires ``--yes`` to actually terminate.
    """
    grace_hours = getattr(args, "grace_hours", None)
    kwargs: dict = {"dry_run": not getattr(args, "yes", False)}
    if grace_hours is not None:
        kwargs["idle_grace_secs"] = float(grace_hours) * 3600
    payload = _core_helper("reap_orphan_launcher_shells", reap_orphan_launcher_shells)(**kwargs)
    if getattr(args, "json", False):
        _json_output(payload)
        return 0
    if not payload["available"]:
        print("Process enumeration unavailable -- nothing to reap.")
        return 0
    dry = not getattr(args, "yes", False)
    verb = "Would reap" if dry else "Reaped"
    ids = payload["reaped"]
    print(f"{verb} {len(ids)} orphaned launcher shell(s)" + (":" if ids else "."))
    for c in payload["candidates"]:
        print(f"  pid {c['pid']}: {c['cmdline'][:100]}")
    for e in payload["errors"]:
        print(f"  ! pid {e['pid']}: {e['reason']}")
    if dry and ids:
        print("Re-run with --yes to terminate the shells above.")
    return 0


# Moved to tracking.py (module-size split); re-exported here for existing
# unqualified call sites and back-compat test access.
_repo_for_record = tracking._repo_for_record


def _remove_managed_worktree(
    rec,
    repo,
    tracking_path: Path,
    *,
    force: bool = False,
) -> tuple[bool, list[str]]:
    """Tear down one managed (system/bridge) worktree: mux, git worktree,
    branch, tracking record. Mirrors ``cmd_remove_system``'s removal steps.
    Returns ``(removed, warnings)``. The tracking record is retained whenever
    Git removal fails so a later managed sweep can retry."""
    warns: list[str] = []
    if force:
        try:
            sessions.kill_tmux_session(rec.worktree_id)
        except Exception:
            pass
    else:
        if sessions.has_mux_session(rec.worktree_id):
            return False, ["live mux appeared before removal"]
        context = sessions.scan_sessions_fast([rec])
        norm = _normalize_path(rec.worktree_path) if rec.worktree_path else ""
        if norm and norm in context.active_sessions:
            return False, ["live session appeared before removal"]
        if rec.branch and rec.worktree_path and Path(rec.worktree_path).exists():
            current_branch = git_ops.current_branch(rec.worktree_path)
            if current_branch != rec.branch:
                return False, ["branch changed before removal"]
    if rec.worktree_path:
        from . import gc as gc_mod

        try:
            worktree_path = Path(rec.worktree_path)
            path_key = os.path.normcase(os.path.abspath(rec.worktree_path))
            if force and git_ops.remove_worktree(
                repo.anchor,
                rec.worktree_path,
            ):
                removed = True
                reason = ""
            else:
                registered_paths = {
                    os.path.normcase(os.path.abspath(str(path)))
                    for path in git_ops.list_worktree_paths(
                        cwd=repo.anchor,
                        fail_on_error=True,
                    )
                }
                if path_key in registered_paths:
                    if force:
                        removed = False
                    else:
                        removed = (
                            git_ops.git(
                                "worktree",
                                "remove",
                                rec.worktree_path,
                                cwd=repo.anchor,
                                check=False,
                            ).returncode
                            == 0
                        )
                    reason = "worktree remove failed"
                else:
                    if not worktree_path.exists():
                        removed = True
                        reason = ""
                    else:
                        verdict = gc_mod.classify_orphan(
                            worktree_path,
                            min_settle_secs=0,
                        )
                        if verdict.action != "remove":
                            removed = False
                            reason = f"unregistered path {verdict.reason}"
                        else:
                            removed, detail = gc_mod.remove_tree(worktree_path)
                            reason = f"unregistered path removal failed: {detail}"
            if not removed:
                warns.append(reason)
        except Exception as exc:
            warns.append(f"worktree remove failed: {exc}")
        if warns:
            return False, warns
    if rec.branch:
        try:
            branch_ref = f"refs/heads/{rec.branch}"
            branch_head = git_ops.git(
                "rev-parse",
                "--verify",
                f"{branch_ref}^{{commit}}",
                cwd=repo.anchor,
                check=False,
            )
            if branch_head.returncode == 0:
                expected_branch_oid = branch_head.stdout.strip()
                if not force:
                    upstream = f"{repo.remote}/{repo.default_branch}"
                    ahead = git_ops.git(
                        "rev-list",
                        "--count",
                        f"{upstream}..{rec.branch}",
                        cwd=repo.anchor,
                        check=False,
                    )
                    if ahead.returncode != 0:
                        warns.append("branch ancestry recheck failed")
                        return False, warns
                    try:
                        ahead_count = int(ahead.stdout.strip())
                    except ValueError:
                        warns.append("branch ancestry recheck was invalid")
                        return False, warns
                    if ahead_count:
                        warns.append("branch gained local commits before removal")
                        return False, warns
                if force:
                    removed_branch = (
                        git_ops.git(
                            "branch",
                            "-D",
                            rec.branch,
                            cwd=repo.anchor,
                            check=False,
                        ).returncode
                        == 0
                    )
                else:
                    removed_branch = (
                        git_ops.git(
                            "update-ref",
                            "-d",
                            branch_ref,
                            expected_branch_oid,
                            cwd=repo.anchor,
                            check=False,
                        ).returncode
                        == 0
                    )
                if not removed_branch:
                    warns.append("branch remove failed")
        except Exception as exc:
            warns.append(f"branch remove failed: {exc}")
    if warns:
        return False, warns
    yaml_path = tracking_path / f"{rec.worktree_id}.yaml"
    try:
        yaml_path.unlink()
    except OSError as exc:
        return False, [f"tracking record remove failed: {exc}"]
    disposition_history.remove(rec.worktree_id)
    handoff_trace.remove_trace(cfg.active_project(), rec.worktree_id)
    return True, warns


def sweep_managed_worktrees(
    *,
    dry_run: bool = False,
    min_idle_secs: float | None = None,
    now: float | None = None,
    config=None,
    tracking_path: Path | None = None,
    worktree_ids: set[str] | None = None,
) -> dict:
    """GC leaked **system/bridge** worktrees (the daemon-owned kinds routine
    cleanup skips -- issue #1069).

    Reaps only the *provably dead* ones -- **FINAL or UNUSED, no active process
    (mux/session/attached), no follow-up flag, idle past the grace window** --
    via :func:`gc.classify_managed_worktree`. A dirty/WIP tree, a live session,
    an attached client, a follow-up mark, or a still-fresh worktree is spared.
    Returns ``{removed: [{id, reason}], skipped: [{id, reason}]}``.
    """
    from . import gc as gc_mod

    if min_idle_secs is None:
        min_idle_secs = gc_mod.MANAGED_GC_GRACE_SECS
    now = time.time() if now is None else now

    config = config or cfg.load_config()
    tracking_path = tracking_path or cfg.tracking_dir()
    records = tracking.list_records(tracking_path)
    managed = [r for r in records if r.kind in tracking.MANAGED_KINDS]
    if worktree_ids is not None:
        managed = [r for r in managed if r.worktree_id in worktree_ids]

    result: dict = {"removed": [], "skipped": []}
    if not managed:
        return result

    mux = sessions._list_mux_sessions() or {}
    activity_by_name = sessions._mux_session_activity()
    session_ctx = sessions.scan_sessions_fast(managed)
    active_paths = _build_active_paths(managed, session_ctx)

    for rec in managed:
        repo = _repo_for_record(config, rec)
        if repo is None:
            result["skipped"].append({"id": rec.worktree_id, "reason": "repo-unresolved"})
            continue
        name = sessions.mux_session_name(rec.worktree_id)
        has_live_mux = name in mux
        attached = bool(mux.get(name))
        norm = _normalize_path(rec.worktree_path) if rec.worktree_path else ""
        has_live_session = norm in session_ctx.active_sessions

        if rec.worktree_path and Path(rec.worktree_path).exists():
            info = git_ops.classify_worktree(
                rec.worktree_path,
                rec.branch,
                fetch=False,
                remote=repo.remote,
                default_branch=repo.default_branch,
                active_paths=active_paths,
            )
            git_state = info.state.value
        elif rec.status in ("finalized", "complete", "completed"):
            git_state = "completed"
        else:
            git_state = "gone"

        last_active = activity_by_name.get(name)
        if last_active is None:
            last_active = _iso_epoch(rec.last_resumed_at) or _iso_epoch(rec.started_at)
        idle_secs = None if last_active is None else (now - last_active)

        verdict = gc_mod.classify_managed_worktree(
            worktree_id=rec.worktree_id,
            kind=rec.kind,
            follow_up=bool(tracking.effective_open_follow_up_count(rec)),
            status=rec.status,
            git_state=git_state,
            has_live_mux=has_live_mux,
            attached=attached,
            has_live_session=has_live_session,
            idle_secs=idle_secs,
            min_idle_secs=min_idle_secs,
            held_claims=sum(1 for c in rec.resources if c.is_live),
        )
        if verdict.action == "skip":
            result["skipped"].append({"id": rec.worktree_id, "reason": verdict.reason})
            continue
        if dry_run:
            result["removed"].append(
                {"id": rec.worktree_id, "reason": f"would remove ({verdict.reason})"}
            )
            continue
        lifecycle_lock = fin.FinalizeLock(
            Path(repo.worktree_root) / ".finalize.lock",
            timeout=3,
            stale_after=3600,
        )
        try:
            lifecycle_lock.acquire()
        except TimeoutError:
            result["skipped"].append({"id": rec.worktree_id, "reason": "lifecycle-busy"})
            continue
        try:
            yaml_path = tracking_path / f"{rec.worktree_id}.yaml"
            if not yaml_path.is_file():
                result["skipped"].append({"id": rec.worktree_id, "reason": "record-missing"})
                continue
            try:
                current = tracking.load_record(yaml_path)
            except FileNotFoundError:
                result["skipped"].append({"id": rec.worktree_id, "reason": "record-missing"})
                continue
            if current.kind not in tracking.MANAGED_KINDS:
                result["skipped"].append({"id": rec.worktree_id, "reason": "not-managed"})
                continue

            fresh_mux = sessions._list_mux_sessions() or {}
            fresh_name = sessions.mux_session_name(current.worktree_id)
            fresh_ctx = sessions.scan_sessions_fast([current])
            fresh_active_paths = _build_active_paths([current], fresh_ctx)
            fresh_norm = _normalize_path(current.worktree_path) if current.worktree_path else ""
            if current.worktree_path and Path(current.worktree_path).exists():
                if (
                    current.branch
                    and git_ops.current_branch(current.worktree_path) != current.branch
                ):
                    result["skipped"].append(
                        {
                            "id": current.worktree_id,
                            "reason": "recheck-branch-drift",
                        }
                    )
                    continue
                fresh_info = git_ops.classify_worktree(
                    current.worktree_path,
                    current.branch,
                    fetch=False,
                    remote=repo.remote,
                    default_branch=repo.default_branch,
                    active_paths=fresh_active_paths,
                )
                fresh_git_state = fresh_info.state.value
            elif current.status in ("finalized", "complete", "completed"):
                fresh_git_state = "completed"
            else:
                fresh_git_state = "gone"
            fresh_activity = sessions._mux_session_activity().get(fresh_name)
            if fresh_activity is None:
                fresh_activity = _iso_epoch(current.last_resumed_at) or _iso_epoch(
                    current.started_at
                )
            fresh_idle = None if fresh_activity is None else (now - fresh_activity)
            fresh_verdict = gc_mod.classify_managed_worktree(
                worktree_id=current.worktree_id,
                kind=current.kind,
                follow_up=bool(tracking.effective_open_follow_up_count(current)),
                status=current.status,
                git_state=fresh_git_state,
                has_live_mux=fresh_name in fresh_mux,
                attached=bool(fresh_mux.get(fresh_name)),
                has_live_session=fresh_norm in fresh_ctx.active_sessions,
                idle_secs=fresh_idle,
                min_idle_secs=min_idle_secs,
                held_claims=sum(1 for c in current.resources if c.is_live),
            )
            if fresh_verdict.action == "skip":
                result["skipped"].append(
                    {
                        "id": current.worktree_id,
                        "reason": f"recheck-{fresh_verdict.reason}",
                    }
                )
                continue
            try:
                with tracking._RecordLock(
                    yaml_path,
                    timeout=3,
                    require_sidecar=True,
                ):
                    latest = tracking.load_record(yaml_path)
                    if (
                        latest.worktree_id != yaml_path.stem
                        or latest.worktree_id != current.worktree_id
                        or latest.kind not in tracking.MANAGED_KINDS
                        or latest.follow_up
                        or latest.live_resources
                        or latest.owner_ref
                        or latest.is_paired
                        or latest.pending_handoffs
                        or latest.resolved_head_session is not None
                        or latest.worktree_path != current.worktree_path
                        or latest.branch != current.branch
                        or any(pr.state in {"creating", "open"} for pr in latest.prs)
                    ):
                        result["skipped"].append(
                            {
                                "id": latest.worktree_id,
                                "reason": "recheck-record-changed",
                            }
                        )
                        continue
            except FileNotFoundError:
                result["skipped"].append({"id": current.worktree_id, "reason": "record-missing"})
                continue
            except TimeoutError:
                result["skipped"].append({"id": current.worktree_id, "reason": "record-lock-busy"})
                continue
            removed, warns = _core_helper("_remove_managed_worktree", _remove_managed_worktree)(
                latest,
                repo,
                tracking_path,
            )
        finally:
            lifecycle_lock.release()
        if not removed:
            result["skipped"].append(
                {
                    "id": rec.worktree_id,
                    "reason": "; ".join(warns) or "removal failed",
                }
            )
            continue
        try:
            activity.log_event(
                "managed_worktree_gc", worktree_id=rec.worktree_id, reason=fresh_verdict.reason
            )
        except Exception:
            pass
        result["removed"].append({"id": rec.worktree_id, "reason": fresh_verdict.reason})

    return result


#: Kill-switch: any non-empty value disables the finished-session auto-clean pass
#: on the no-daemon cadence, so the sweep only ever runs via explicit ``cleanup``.
_NO_AUTO_CLEAN_ENV = "AGENT_WORKTREES_NO_AUTO_CLEAN"
#: Override for :data:`gc.SESSION_GC_GRACE_SECS` (seconds a finished worktree must
#: be idle before the cadence auto-collects it).
_AUTO_CLEAN_GRACE_ENV = "AGENT_WORKTREES_AUTO_CLEAN_GRACE_SECS"


def auto_clean_enabled() -> bool:
    """Whether the finished-session auto-clean pass may run (kill-switch off)."""
    return not os.environ.get(_NO_AUTO_CLEAN_ENV)


def _auto_clean_grace_secs() -> float:
    """Resolve the finished-session idle-grace threshold (env override else default)."""
    from . import gc as gc_mod

    raw = os.environ.get(_AUTO_CLEAN_GRACE_ENV)
    if raw:
        try:
            val = float(raw)
            if val >= 0:
                return val
        except (TypeError, ValueError):
            pass
    return float(gc_mod.SESSION_GC_GRACE_SECS)


def sweep_finished_session_worktrees(
    *, dry_run: bool = False, min_idle_secs: float | None = None, now: float | None = None
) -> dict:
    """GC provably-safe **finished session** worktrees on the no-daemon cadence.

    The companion to :func:`sweep_managed_worktrees`: where that reaps leaked
    ``system``/``bridge`` worktrees, this reaps ordinary (non-managed) worktrees
    whose work is already landed -- the ``finalized`` / merged / git-COMPLETED
    ones the manual ``cleanup`` would remove -- so they stop accumulating without
    a background service or a manual sweep. Run at the same two lifecycle
    boundaries (picker launch + session end).

    Safety reuses the EXACT manual-cleanup decision so the cadence can never be
    more aggressive than an explicit ``cleanup``: :func:`prune.cleanup_disposition`
    with the conservative flags (``include_unused=False``,
    ``include_conversations=False``) collects ONLY the strictly-SAFE buckets
    (finalized / merged / completed-local); an ``empty``/``conversation-only``
    worktree, an in-flight claimed resource (via ``resolve_claimant_alive`` --
    which now also releases a provably-dead owner's claims), a follow-up-flagged
    or paired-pending worktree, and anything dirty/wip/unmerged are all spared.
    On top of that, a worktree is collected only once it has been **idle past the
    grace window** (default :data:`gc.SESSION_GC_GRACE_SECS`, 48h) and has no live
    bound session or live/attached mux. No network fetch is done (the cadence
    must be fast and offline-safe), so a merge not yet locally provable simply
    waits for a later pass -- the sweep never collects on unproven state.

    Returns ``{removed: [{id, reason}], skipped: [{id, reason}]}``. Never raises
    for control-flow; the caller wraps it best-effort. The kill-switch
    (:func:`auto_clean_enabled`) is checked by the cadence callers, not here, so
    this stays directly testable.
    """
    result: dict = {"removed": [], "skipped": []}
    now = time.time() if now is None else now
    grace = _auto_clean_grace_secs() if min_idle_secs is None else min_idle_secs

    config = cfg.load_config()
    repo = config.default_repo
    tracking_path = cfg.tracking_dir()
    records = [
        r for r in tracking.list_records(tracking_path) if r.kind not in tracking.MANAGED_KINDS
    ]
    if not records:
        return result

    session_ctx = sessions.scan_sessions_fast(records)
    active_paths = _build_active_paths(records, session_ctx)
    activity_by_name = sessions._mux_session_activity()
    mux = sessions._list_mux_sessions() or {}
    upstream = f"{repo.remote}/{repo.default_branch}"

    # Pass 1 (read-only): resolve the collectable set without mutating anything,
    # so the finalization lock is taken only when there is real work.
    candidates: list[tuple[tracking.WorktreeRecord, git_ops.WorktreeStateInfo, str]] = []
    for rec in records:
        name = sessions.mux_session_name(rec.worktree_id)
        norm = _normalize_path(rec.worktree_path) if rec.worktree_path else ""
        # Live guards: a bound session, or a live/attached mux, is never touched.
        if norm and norm in session_ctx.active_sessions:
            result["skipped"].append({"id": rec.worktree_id, "reason": "live session"})
            continue
        if name in mux:
            result["skipped"].append({"id": rec.worktree_id, "reason": "live mux session"})
            continue

        if rec.worktree_path and Path(rec.worktree_path).exists():
            info = git_ops.classify_worktree(
                rec.worktree_path,
                rec.branch,
                fetch=False,
                remote=repo.remote,
                default_branch=repo.default_branch,
                active_paths=active_paths,
            )
            info = _apply_tracking_override(rec, info)
        elif rec.status == "finalized":
            info = git_ops.WorktreeStateInfo(state=git_ops.WorktreeState.COMPLETED)
        else:
            info = git_ops.WorktreeStateInfo(state=git_ops.WorktreeState.GONE)

        # Idle-grace gate: the worktree must have been quiet past the window. Use
        # the freshest of the mux activity, last resume, completion, or creation.
        last_active = activity_by_name.get(name)
        if last_active is None:
            last_active = (
                _iso_epoch(rec.last_resumed_at)
                or _iso_epoch(rec.completed_at)
                or _iso_epoch(rec.started_at)
            )
        if last_active is not None and (now - last_active) < grace:
            result["skipped"].append({"id": rec.worktree_id, "reason": "idle grace not elapsed"})
            continue

        # Collectability reuses the exact manual-cleanup safety (conservative).
        if info.state == git_ops.WorktreeState.GONE:
            # Dir already gone: only collect once the branch content is on master
            # (the check manual cleanup owns for GONE).
            if rec.branch and not git_ops.is_branch_merged(rec.branch, upstream, cwd=repo.anchor):
                result["skipped"].append(
                    {"id": rec.worktree_id, "reason": "branch unmerged (worktree dir missing)"}
                )
                continue
            candidates.append((rec, info, "gone; branch merged"))
            continue

        turns = session_ctx.turn_count.get(norm, 0)
        disp = prune.cleanup_disposition(
            rec,
            info,
            turn_count=turns,
            include_unused=False,
            include_conversations=False,
            claimant_alive=claimant_mod.resolve_claimant_alive,
            paired_sibling_final=prune.default_paired_sibling_final,
        )
        if not disp.cleanable:
            result["skipped"].append({"id": rec.worktree_id, "reason": disp.bucket})
            continue
        candidates.append((rec, info, disp.reason))

    if not candidates:
        return result
    if dry_run:
        for rec, info, reason in candidates:
            result["removed"].append({"id": rec.worktree_id, "reason": f"would remove ({reason})"})
        return result

    # Pass 2: reap under the shared finalization lock. A short, non-blocking-ish
    # timeout keeps the cadence from wedging behind a concurrent finalize -- skip
    # this pass and let the next boundary retry rather than delay startup/exit.
    lock = fin.FinalizeLock(Path(repo.worktree_root) / ".finalize.lock", timeout=3)
    try:
        lock.acquire()
    except TimeoutError:
        return result
    try:
        for rec, info, reason in candidates:
            # Idle-grace revalidation (cleanup-toctou-revalidation, Phase 3):
            # re-derive activity at action time so a resume between candidate
            # selection (Pass 1) and this reap (Pass 2) postpones removal even
            # when no session remains live at this final check.
            name = sessions.mux_session_name(rec.worktree_id)
            fresh_activity = sessions._mux_session_activity()
            fresh_last_active = fresh_activity.get(name)
            if fresh_last_active is None:
                fresh_yaml = tracking_path / f"{rec.worktree_id}.yaml"
                fresh_rec = tracking.load_record(fresh_yaml) if fresh_yaml.exists() else rec
                fresh_last_active = (
                    _iso_epoch(fresh_rec.last_resumed_at)
                    or _iso_epoch(fresh_rec.completed_at)
                    or _iso_epoch(fresh_rec.started_at)
                )
            if fresh_last_active is not None and (time.time() - fresh_last_active) < grace:
                result["skipped"].append(
                    {"id": rec.worktree_id,
                     "reason": "idle grace not elapsed (activity since selection)"}
                )
                continue

            rr = _core_helper("_revalidate_cleanup_safety", _revalidate_cleanup_safety)(
                rec.worktree_id,
                repo=repo,
                tracking_path=tracking_path,
                include_unused=False,
                include_conversations=False,
                reap=lambda latest, fresh_info: _reap_worktree(
                    latest, fresh_info, repo, tracking_path),
            )
            if not rr.cleanable:
                result["skipped"].append({"id": rec.worktree_id, "reason": rr.reason})
                continue
            try:
                activity.log_event(
                    "session_worktree_autoclean", worktree_id=rec.worktree_id,
                    reason=rr.reason,
                )
            except Exception:
                pass
            full = rr.reason + (f"; {'; '.join(rr.warnings)}" if rr.warnings else "")
            result["removed"].append({"id": rec.worktree_id, "reason": full})
        git_ops.prune_worktrees(cwd=repo.anchor)
    finally:
        lock.release()
    return result


def cmd_reap_sessions(args: argparse.Namespace) -> int:
    """``reap-sessions`` -- sweep orphaned mux sessions (issue #713).

    With ``--id`` it targets a single worktree, applying the identical
    spare-attached/system/active/busy predicate as the full sweep.
    """
    dry = getattr(args, "dry_run", False)
    only_id = getattr(args, "id", None)
    grace_hours = getattr(args, "grace_hours", None)
    kwargs = {"dry_run": dry, "only_id": only_id}
    if grace_hours is not None:
        kwargs["idle_grace_secs"] = float(grace_hours) * 3600
    payload = reap_orphan_mux_sessions(**kwargs)
    if getattr(args, "json", False):
        _json_output(payload)
        return 0
    if not payload["available"]:
        print("No multiplexer available -- nothing to reap.")
        return 0
    verb = "Would reap" if dry else "Reaped"
    ids = payload["reaped"]
    print(f"{verb} {len(ids)} orphaned mux session(s): " + (", ".join(ids) if ids else "(none)"))
    for e in payload["errors"]:
        print(f"  ! {e['id']}: {e['reason']}")
    return 0
