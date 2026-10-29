"""Shared daemon-health audit + repair helpers for ``zdd`` consumers."""

from __future__ import annotations

import os
import platform
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import breadcrumb, routing
from .diagnostic_recovery import IdentityTerminator
from .generations import (
    GROUPING_UNCERTAIN,
    Generation,
    block_on_uncertain_grouping,
    generation_of,
    group_generations,
    is_venv_redirector,
    owner_pids,
    same_generation,
)
from .generations import inspect_duplicates as _inspect_duplicates
from .generations import inspect_superseded_generations as _inspect_superseded_generations

__all__ = [
    "DaemonCandidate",
    "DiagnosticContext",
    "Generation",
    "apply_daemon_health",
    "audit_daemon_health",
    "is_venv_redirector",
    "lock_data_is_live",
    "process_start_time",
    "terminate_pid_if_identity",
]


@dataclass(frozen=True)
class DaemonCandidate:
    pid: int
    start_time: str | None = None
    note: str | None = None
    # Structural facts for generation grouping (``DiagnosticContext.group_launchers``).
    ppid: int | None = None
    image: str | None = None
    # For a parent the census did not capture: its executable (``None`` when
    # unreadable) and whether it is provably gone (dead, or its PID now names
    # a process started after this candidate). See ``zdd.generations``.
    parent_image: str | None = None
    parent_gone: bool = False

    def to_dict(self) -> dict[str, object]:
        data: dict[str, object] = {"pid": self.pid, "start_time": self.start_time}
        if self.note:
            data["note"] = self.note
        if self.ppid is not None:
            data["ppid"] = self.ppid
        if self.image is not None:
            data["image"] = self.image
        if self.parent_image is not None:
            data["parent_image"] = self.parent_image
        if self.parent_gone:
            data["parent_gone"] = True
        return data


def process_start_time(pid: int) -> str | None:
    """A stable process-identity token for ``pid``, or ``None``."""
    if pid <= 0:
        return None
    if platform.system() == "Windows":
        return _start_time_windows(pid)
    return _start_time_posix(pid)


def _start_time_windows(pid: int) -> str | None:
    import ctypes
    from ctypes import wintypes

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    k32.CloseHandle.restype = wintypes.BOOL
    k32.GetProcessTimes.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
    ]
    k32.GetProcessTimes.restype = wintypes.BOOL

    handle = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        creation = wintypes.FILETIME()
        exit_ = wintypes.FILETIME()
        kernel = wintypes.FILETIME()
        user = wintypes.FILETIME()
        ok = k32.GetProcessTimes(
            handle,
            ctypes.byref(creation),
            ctypes.byref(exit_),
            ctypes.byref(kernel),
            ctypes.byref(user),
        )
        if not ok:
            return None
        ticks = (creation.dwHighDateTime << 32) | creation.dwLowDateTime
        return str(ticks)
    except OSError:
        return None
    finally:
        k32.CloseHandle(handle)


def _start_time_posix(pid: int) -> str | None:
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(errors="ignore")
    except OSError:
        return None
    rparen = stat.rfind(")")
    if rparen == -1:
        return None
    rest = stat[rparen + 1 :].split()
    if len(rest) < 20:
        return None
    token = rest[19]
    return token if token.isdigit() else None


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if platform.system() == "Windows":
        from .diagnostic_exit import pid_alive_windows

        return pid_alive_windows(pid)
    pidfd_open = getattr(os, "pidfd_open", None)
    if not callable(pidfd_open) and platform.system() == "Linux":
        from .singleton_linux import _open_pidfd

        pidfd_open = _open_pidfd
    if callable(pidfd_open):
        from .diagnostic_exit import wait_posix

        try:
            descriptor = pidfd_open(pid)
        except ProcessLookupError:
            return False
        except (OSError, NotImplementedError):
            pass
        else:
            try:
                return not wait_posix(descriptor, 0)
            except OSError:
                return True
            finally:
                os.close(descriptor)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return True
    return True


def lock_data_is_live(data: dict | None) -> bool:
    """Whether ``data`` denotes a currently-live lock owner."""
    if not isinstance(data, dict):
        return False
    pid = data.get("pid")
    if not isinstance(pid, int) or not _pid_alive(pid):
        return False
    recorded = data.get("start_time")
    if not recorded:
        return True
    current = process_start_time(pid)
    if current is None:
        return True
    return str(recorded) == str(current)


def _terminate_windows_if_identity(
    pid: int, expected_start_time: str, exit_timeout: float | None = None,
) -> dict:
    import ctypes
    from ctypes import wintypes
    from .diagnostic_exit import PROCESS_QUERY_LIMITED_INFORMATION, PROCESS_TERMINATE, SYNCHRONIZE

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    k32.CloseHandle.restype = wintypes.BOOL
    k32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    k32.TerminateProcess.restype = wintypes.BOOL
    k32.GetProcessTimes.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
    ]
    k32.GetProcessTimes.restype = wintypes.BOOL

    handle = k32.OpenProcess(
        PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_TERMINATE
        | (SYNCHRONIZE if exit_timeout is not None else 0), False, pid,
    )
    if not handle:
        return {
            "killed": False,
            "identity_verified": False,
            "method": "windows-handle-unavailable",
        }
    try:
        creation = wintypes.FILETIME()
        exit_ = wintypes.FILETIME()
        kernel = wintypes.FILETIME()
        user = wintypes.FILETIME()
        ok = k32.GetProcessTimes(
            handle,
            ctypes.byref(creation),
            ctypes.byref(exit_),
            ctypes.byref(kernel),
            ctypes.byref(user),
        )
        if not ok:
            return {
                "killed": False,
                "identity_verified": False,
                "method": "windows-handle-start-time-unavailable",
            }
        current = str((creation.dwHighDateTime << 32) | creation.dwLowDateTime)
        if current != str(expected_start_time):
            return {
                "killed": False,
                "identity_verified": False,
                "method": "windows-handle-identity-mismatch",
            }
        killed = bool(k32.TerminateProcess(handle, 1))
        result = {
            "killed": killed,
            "identity_verified": True,
            "method": "windows-verified-handle",
        }
        if exit_timeout is not None:
            from .diagnostic_exit import wait_windows

            result["exit_confirmed"] = killed and wait_windows(k32, handle, exit_timeout)
        return result
    finally:
        k32.CloseHandle(handle)


def _terminate_posix_if_identity(
    pid: int, expected_start_time: str, exit_timeout: float | None = None,
) -> dict:
    import signal

    pidfd_open = getattr(os, "pidfd_open", None)
    pidfd_send_signal = getattr(signal, "pidfd_send_signal", None)
    if not callable(pidfd_open) or not callable(pidfd_send_signal):
        if platform.system() != "Linux":
            return {
                "killed": False,
                "identity_verified": False,
                "method": "pidfd-unavailable",
            }
        from .singleton_linux import _open_pidfd, _send_pidfd_signal

        pidfd_open = _open_pidfd
        pidfd_send_signal = _send_pidfd_signal
    try:
        fd = pidfd_open(pid)
    except NotImplementedError:
        return {
            "killed": False,
            "identity_verified": False,
            "method": "pidfd-unavailable",
        }
    except (OSError, NotImplementedError):
        return {
            "killed": False,
            "identity_verified": False,
            "method": "pidfd-open-failed",
        }
    try:
        if process_start_time(pid) != str(expected_start_time):
            return {
                "killed": False,
                "identity_verified": False,
                "method": "pidfd-identity-mismatch",
            }
        try:
            pidfd_send_signal(fd, signal.SIGTERM)
        except OSError:
            return {
                "killed": False,
                "identity_verified": True,
                "method": "pidfd-signal-failed",
            }
        result = {
            "killed": True,
            "identity_verified": True,
            "method": "pidfd",
        }
        if exit_timeout is not None:
            from .diagnostic_exit import wait_posix

            result["exit_confirmed"] = wait_posix(fd, exit_timeout)
        return result
    finally:
        os.close(fd)


def terminate_pid_if_identity(
    pid: int, expected_start_time: str | None, *, exit_timeout: float | None = None,
) -> dict:
    """Terminate only through an OS object bound to the verified process."""
    from .diagnostic_exit import validate_timeout

    validate_timeout(exit_timeout)
    if pid <= 0 or not expected_start_time:
        return {
            "killed": False,
            "identity_verified": False,
            "method": "identity-unavailable",
        }
    if platform.system() == "Windows":
        terminate = _terminate_windows_if_identity
    else:
        terminate = _terminate_posix_if_identity
    if exit_timeout is None:
        return terminate(pid, str(expected_start_time))
    return terminate(pid, str(expected_start_time), exit_timeout)


@dataclass(frozen=True)
class DiagnosticContext:
    service: str
    config_dir: str | os.PathLike[str]
    read_lock: Callable[[], dict | None]
    list_candidates: Callable[[], list[DaemonCandidate]]
    is_superseded: Callable[[int, int], bool]
    acquire_cutover_guard: Callable[[float], Any] | None = None
    repair_supported: bool = True
    reachability_check: Callable[[str, int], bool] | None = None
    terminate_pid_if_identity: IdentityTerminator = terminate_pid_if_identity
    lock_is_live: Callable[[dict | None], bool] = lock_data_is_live
    make_client: Callable[[str], Any] | None = None
    health_check: Callable[[str, int], bool] | None = None
    abandoned_passive_grace_seconds: float = breadcrumb.DEFAULT_ABANDONED_PASSIVE_GRACE_S
    # Opt in where a daemon may run as a launcher/worker process pair (a
    # Windows venv redirector): group them as one generation (zdd.generations).
    group_launchers: bool = False
    is_launcher_image: Callable[[str], bool] | None = None


def _candidate_map(candidates: list[DaemonCandidate]) -> dict[int, DaemonCandidate]:
    return {
        candidate.pid: candidate
        for candidate in candidates
        if isinstance(candidate.pid, int) and candidate.pid > 0
    }


def _validated_lock_owner(
    ctx: DiagnosticContext,
    candidates: dict[int, DaemonCandidate],
    lock_data: dict | None,
    active_pid: int | None,
    generations: list[Generation] = (),
) -> tuple[dict[str, object] | None, str | None]:
    """The lock-file-based validation :func:`_validated_owner` used to be
    the entirety of. Split out unchanged so the routing-table fallback
    below can retry with a different, independent source of truth when
    this one can't vouch for anyone."""
    if not ctx.lock_is_live(lock_data):
        return None, "no validated live owner"
    if not isinstance(lock_data, dict):
        return None, "lock unreadable"
    pid = lock_data.get("pid")
    if not isinstance(pid, int):
        return None, "lock missing owner pid"
    if isinstance(active_pid, int) and not same_generation(generations, active_pid, pid):
        return None, "lock owner disagrees with routed active pid"
    candidate = candidates.get(pid)
    if candidate is None:
        return None, "validated owner absent from fresh daemon census"
    recorded_start = lock_data.get("start_time")
    if not recorded_start or not candidate.start_time:
        return None, "validated owner lacks a provable start-time token"
    if str(recorded_start) != str(candidate.start_time):
        return None, "lock owner token disagrees with fresh process census"
    return {
        "pid": candidate.pid,
        "start_time": candidate.start_time,
        "lock": {
            "pid": lock_data.get("pid"),
            "start_time": recorded_start,
        },
    }, None


def _with_generation(
    owner: dict[str, object], generations: list[Generation]
) -> dict[str, object]:
    generation = generation_of(generations, owner.get("pid"))
    if generation is not None and generation.launcher is not None:
        owner["generation_pids"] = sorted(generation.pids)
    return owner


def _validated_owner(
    ctx: DiagnosticContext,
    candidates: dict[int, DaemonCandidate],
    table: dict | None,
    generations: list[Generation] = (),
) -> tuple[dict[str, object] | None, str | None]:
    """The validated live owner; a route or lock PID naming either member of
    a launcher/worker generation identifies that whole generation."""
    active_raw = table.get("active") if isinstance(table, dict) else None
    active_pid = active_raw.get("pid") if isinstance(active_raw, dict) else None

    lock_data = ctx.read_lock()
    owner, reason = _validated_lock_owner(
        ctx, candidates, lock_data, active_pid, generations
    )
    if owner is not None:
        return _with_generation(owner, generations), None

    # Only fall back when the lock's own pid actively DISAGREES with the
    # routing table -- exactly what an old generation stuck mid-retire
    # (superseded but never relinquishing its own lock claim) looks like:
    # a cutover already rewrote the table's "active" entry to the new
    # generation, but the old one never got far enough to rewrite (or
    # release) the lock file naming itself. Any OTHER validation failure
    # (an unreadable/absent lock, a missing owner pid, an unprovable
    # start-time token, a lock owner that isn't even in the fresh daemon
    # census) is a genuine ambiguity this fallback must not paper over --
    # it fails closed exactly as before. The routing table's "active"
    # entry is this service's own single, generation-numbered source of
    # truth for who is CURRENTLY being routed to; if a live, in-census
    # candidate holds that exact pid right now, trust it rather than
    # refuse to pick anyone in this one specific, provable-disagreement
    # case -- otherwise a merely-slow-to-retire old generation
    # permanently blocks every repair path that depends on a validated
    # owner (not just this one: a daemon more than one generation behind
    # is also invisible to `_inspect_superseded_generations`'s own
    # active/previous-slot-only lookback, so without this fallback it has
    # no path to ever being reaped at all).
    if reason == "lock owner disagrees with routed active pid" and isinstance(
        active_pid, int
    ):
        candidate = candidates.get(active_pid)
        if candidate is not None:
            return _with_generation({
                "pid": candidate.pid,
                "start_time": candidate.start_time,
                "lock": None,
                "routed_active_pid": active_pid,
            }, generations), None
    return None, reason


def _describe_old_endpoint(record: dict) -> tuple[str, int] | None:
    old = record.get("old") if isinstance(record.get("old"), dict) else None
    if not isinstance(old, dict):
        return None
    bind = str(old.get("bind") or "127.0.0.1")
    host = "127.0.0.1" if bind in ("0.0.0.0", "", "::") else bind
    if bind == "::":
        host = "::1"
    try:
        port = int(old["port"])
    except (KeyError, TypeError, ValueError):
        return None
    if port <= 0:
        return None
    return host, port


def _inspect_stranded_survivor(
    ctx: DiagnosticContext,
    record: dict | None,
) -> dict[str, object] | None:
    if not breadcrumb.is_stale(record):
        return None
    if not isinstance(record, dict):
        return None
    endpoint = _describe_old_endpoint(record)
    if endpoint is None:
        return None
    reachable = False
    if ctx.reachability_check is not None:
        reachable = bool(ctx.reachability_check(*endpoint))
    elif ctx.make_client is not None:
        base_url = f"http://{routing.format_authority(endpoint[0], endpoint[1])}"
        try:
            ctx.make_client(base_url).health()
            reachable = True
        except Exception:  # noqa: BLE001 - probe is best-effort
            reachable = False
    elif ctx.health_check is not None:
        reachable = bool(ctx.health_check(*endpoint))
    if not reachable:
        return None
    return {
        "kind": "stranded_survivor",
        "summary": "stale cutover breadcrumb still points at a live old survivor",
        "repairable": ctx.make_client is not None,
        "blocked_reason": (
            None
            if ctx.make_client is not None
            else "cutover recovery hooks unavailable"
        ),
        "target": {"host": endpoint[0], "port": endpoint[1]},
    }


def _record_age_seconds(record: dict, *, now: datetime | None = None) -> float | None:
    updated = record.get("updated_at")
    if not isinstance(updated, str):
        return None
    try:
        ts = datetime.fromisoformat(updated)
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ((now or datetime.now(timezone.utc)) - ts).total_seconds()


def _inspect_abandoned_passive(
    ctx: DiagnosticContext,
    record: dict | None,
    active_pid: int | None,
    candidates: dict[int, DaemonCandidate],
    *,
    now: datetime | None = None,
    generations: list[Generation] = (),
) -> tuple[dict[str, object] | None, int | None]:
    if not breadcrumb.is_stale(record):
        return None, None
    if not isinstance(record, dict):
        return None, None
    new_pid = record.get("new_pid")
    if not isinstance(new_pid, int) or new_pid <= 0:
        return None, None
    age_s = _record_age_seconds(record, now=now)
    if age_s is None or age_s < ctx.abandoned_passive_grace_seconds:
        return None, None
    if active_pid is not None and same_generation(generations, new_pid, active_pid):
        return None, None
    candidate = candidates.get(new_pid)
    if candidate is None:
        return None, None
    generation = generation_of(generations, new_pid)
    targets = generation.targets() if generation is not None else [candidate.to_dict()]
    repairable = ctx.repair_supported and all(item.get("start_time") for item in targets)
    return (
        {
            "kind": "abandoned_passive",
            "summary": "stale cutover breadcrumb names a passive candidate",
            "repairable": repairable,
            "blocked_reason": (
                None
                if repairable
                else (
                    "identity-bound repair unsupported on this platform"
                    if not ctx.repair_supported
                    else "candidate start_time unavailable"
                )
            ),
            "targets": targets,
            "age_seconds": age_s,
        },
        new_pid,
    )


def _counts(findings: list[dict[str, object]]) -> dict[str, int]:
    counts = {"total": len(findings)}
    for finding in findings:
        kind = finding.get("kind")
        if isinstance(kind, str):
            counts[kind] = counts.get(kind, 0) + 1
    return counts


def _try_acquire_cutover_guard(ctx: DiagnosticContext):
    if ctx.acquire_cutover_guard is None:
        return None, None
    try:
        return ctx.acquire_cutover_guard(0.0), None
    except Exception as exc:  # noqa: BLE001 - consumer-specific lock classes vary
        return None, str(exc)


def _audit_daemon_health(
    ctx: DiagnosticContext,
    *,
    now: datetime | None = None,
    cutover_state: str = "auto",
) -> dict[str, object]:
    guard = None
    guard_error = None
    cutover_busy = False
    if cutover_state == "auto":
        guard, guard_error = _try_acquire_cutover_guard(ctx)
        cutover_busy = ctx.acquire_cutover_guard is not None and guard is None
    elif cutover_state == "busy":
        cutover_busy = True

    if cutover_busy:
        return {
            "service": ctx.service,
            "mode": "report",
            "validated_owner": None,
            "owner_validation_reason": None,
            "cutover_in_progress": True,
            "cutover_guard_error": guard_error,
            "candidate_count": 0,
            "candidates": [],
            "findings": [],
            "counts": {"total": 0},
        }

    findings: list[dict[str, object]] = []
    try:
        from .diagnostic_recovery import conditional_recovery_report

        conditional_report = conditional_recovery_report(ctx)
        if conditional_report is not None:
            return conditional_report
        try:
            candidates = _candidate_map(ctx.list_candidates())
        except (OSError, subprocess.SubprocessError) as exc:
            finding = {
                "kind": "census_unavailable",
                "summary": "fresh daemon process census unavailable",
                "repairable": False,
                "blocked_reason": type(exc).__name__,
                "error": str(exc),
            }
            return {
                "service": ctx.service, "mode": "report",
                "validated_owner": None,
                "owner_validation_reason": "fresh daemon process census unavailable",
                "cutover_in_progress": False, "cutover_guard_error": None,
                "census_available": False, "candidate_count": None, "candidates": [],
                "findings": [finding], "counts": _counts([finding]),
            }
        generations, grouping_reason = group_generations(
            candidates,
            structural=ctx.group_launchers,
            is_launcher_image=ctx.is_launcher_image,
        )
        table = routing.read_table(ctx.config_dir)
        owner, owner_reason = _validated_owner(ctx, candidates, table, generations)
        record = breadcrumb.read_breadcrumb(ctx.config_dir)
        active_raw = table.get("active") if isinstance(table, dict) else None
        active_pid = active_raw.get("pid") if isinstance(active_raw, dict) else None
        stranded = _inspect_stranded_survivor(ctx, record)
        if stranded is not None:
            findings.append(stranded)

        abandoned, abandoned_pid = _inspect_abandoned_passive(
            ctx,
            record,
            int(active_pid) if isinstance(active_pid, int) else None,
            candidates,
            now=now,
            generations=generations,
        )
        if abandoned is not None:
            findings.append(abandoned)

        superseded, superseded_pids = _inspect_superseded_generations(
            ctx, table, candidates, generations
        )
        if superseded is not None:
            findings.append(superseded)

        excluded = set(superseded_pids)
        if abandoned_pid is not None:
            abandoned_generation = generation_of(generations, abandoned_pid)
            excluded |= abandoned_generation.pids if abandoned_generation else {abandoned_pid}
        duplicate = _inspect_duplicates(ctx, generations, owner, excluded)
        if duplicate is not None:
            findings.append(duplicate)
        from .diagnostic_recovery import mark_custody_refusals

        mark_custody_refusals(findings, table or {}, record or {})
        block_on_uncertain_grouping(findings, grouping_reason)
    finally:
        if guard is not None and hasattr(guard, "release"):
            guard.release()

    return {
        "service": ctx.service,
        "mode": "report",
        "validated_owner": owner,
        "owner_validation_reason": owner_reason,
        "cutover_in_progress": cutover_busy,
        "cutover_guard_error": guard_error,
        "candidate_count": len(candidates),
        "candidates": [candidate.to_dict() for _, candidate in sorted(candidates.items())],
        "generation_count": len(generations),
        "generations": [generation.to_dict() for generation in generations],
        "grouping_uncertain": grouping_reason is not None,
        "grouping_uncertain_reason": grouping_reason,
        "findings": findings,
        "counts": _counts(findings),
    }


def audit_daemon_health(
    ctx: DiagnosticContext,
    *,
    now: datetime | None = None,
) -> dict[str, object]:
    return _audit_daemon_health(ctx, now=now)


def _target_pid_sequence(report: dict[str, object]) -> list[tuple[str, dict[str, object]]]:
    findings = report.get("findings")
    if not isinstance(findings, list):
        return []
    order = ("abandoned_passive", "superseded_generation", "duplicate_resident")
    targets: list[tuple[str, dict[str, object]]] = []
    for kind in order:
        for finding in findings:
            if finding.get("kind") != kind:
                continue
            items = finding.get("targets")
            if not isinstance(items, list):
                continue
            for item in items:
                if isinstance(item, dict) and isinstance(item.get("pid"), int):
                    targets.append((kind, item))
    return targets


def _apply_daemon_health_locked(
    ctx: DiagnosticContext,
    *,
    now: datetime | None = None,
) -> dict[str, object]:
    from .diagnostic_recovery import terminate_unpromoted

    guard, guard_error = _try_acquire_cutover_guard(ctx)
    if ctx.acquire_cutover_guard is not None and guard is None:
        blocked = _audit_daemon_health(ctx, now=now, cutover_state="busy")
        return {
            "service": ctx.service,
            "mode": "apply",
            "before": blocked,
            "after": blocked,
            "findings": blocked["findings"],
            "remaining_findings": blocked["findings"],
            "counts": blocked["counts"],
            "actions": [
                {
                    "kind": "cutover_guard",
                    "blocked": True,
                    "reason": guard_error or "cutover in progress",
                }
            ],
        }
    try:
        before = _audit_daemon_health(ctx, now=now, cutover_state="held")
        actions: list[dict[str, object]] = []
        def blocked_census(report, kind="census_unavailable",
                           reason="fresh daemon process census unavailable"):
            return {
                "service": ctx.service, "mode": "apply", "before": before,
                "after": report, "findings": before["findings"],
                "remaining_findings": report["findings"], "counts": report["counts"],
                "actions": actions + [{"kind": kind, "blocked": True, "reason": reason}],
            }

        def refuse_termination(report):
            # Fail closed: never terminate on a census that cannot be grouped
            # into generations with confidence (zdd.generations).
            if report.get("census_available") is False:
                return blocked_census(report)
            if report.get("grouping_uncertain"):
                return blocked_census(report, "generation_grouping", GROUPING_UNCERTAIN)
            return None

        if before.get("census_available") is False:
            return blocked_census(before)

        for finding in before.get("findings", []):
            if finding.get("kind") != "stranded_survivor":
                continue
            if ctx.make_client is None:
                actions.append(
                    {
                        "kind": "stranded_survivor",
                        "blocked": True,
                        "reason": "cutover recovery hooks unavailable",
                    }
                )
                break
            recovery = breadcrumb._recover_stale_cutover_locked(
                ctx.config_dir,
                ctx.make_client,
            )
            actions.append({"kind": "stranded_survivor", "result": recovery})
            if not recovery.get("recovered"):
                return {
                    "service": ctx.service,
                    "mode": "apply",
                    "before": before,
                    "after": before,
                    "findings": before["findings"],
                    "remaining_findings": before["findings"],
                    "counts": before["counts"],
                    "actions": actions,
                }
            break

        current = _audit_daemon_health(ctx, now=now, cutover_state="held")
        refused = refuse_termination(current)
        if refused is None and before.get("grouping_uncertain"):
            # The abandoned-passive targets below come from ``before``: a
            # census that could not be grouped never supplies a target, even
            # when a later census groups cleanly.
            refused = blocked_census(current, "generation_grouping", GROUPING_UNCERTAIN)
        if refused is not None:
            return refused
        attempted: set[int] = set()
        for finding in before.get("findings", []):
            if finding.get("kind") != "abandoned_passive":
                continue
            targets = finding.get("targets")
            if not isinstance(targets, list):
                continue
            owner = current.get("validated_owner")
            for item in targets:
                if not isinstance(item, dict) or not isinstance(item.get("pid"), int):
                    continue
                if not ctx.repair_supported:
                    actions.append(
                        {
                            "kind": "abandoned_passive",
                            "pid": item["pid"],
                            "blocked": True,
                            "reason": "identity-bound repair unsupported on this platform",
                        }
                    )
                    attempted.add(item["pid"])
                    continue
                if not isinstance(owner, dict):
                    actions.append(
                        {
                            "kind": "abandoned_passive",
                            "pid": item["pid"],
                            "blocked": True,
                            "reason": "no validated live owner",
                        }
                    )
                    attempted.add(item["pid"])
                    continue
                if item["pid"] in owner_pids(owner):
                    actions.append(
                        {
                            "kind": "abandoned_passive",
                            "pid": item["pid"],
                            "blocked": True,
                            "reason": "target is the validated live owner",
                        }
                    )
                    attempted.add(item["pid"])
                    continue
                termination = terminate_unpromoted(
                    ctx, item["pid"], item.get("start_time")
                )
                actions.append(
                    {
                        "kind": "abandoned_passive",
                        "pid": item["pid"],
                        "owner_pid": owner.get("pid"),
                        "termination": termination,
                    }
                )
                attempted.add(item["pid"])

        max_iterations = max(1, before.get("candidate_count", 0) * 3)
        for _ in range(max_iterations):
            current = _audit_daemon_health(ctx, now=now, cutover_state="held")
            refused = refuse_termination(current)
            if refused is not None:
                return refused
            next_target = None
            for kind, item in _target_pid_sequence(current):
                if item["pid"] not in attempted:
                    next_target = (kind, item, current)
                    break
            if next_target is None:
                break
            kind, item, current = next_target
            owner = current.get("validated_owner")
            if not ctx.repair_supported:
                actions.append(
                    {
                        "kind": kind,
                        "pid": item["pid"],
                        "blocked": True,
                        "reason": "identity-bound repair unsupported on this platform",
                    }
                )
                attempted.add(item["pid"])
                continue
            if not isinstance(owner, dict):
                actions.append(
                    {
                        "kind": kind,
                        "pid": item["pid"],
                        "blocked": True,
                        "reason": "no validated live owner",
                    }
                )
                attempted.add(item["pid"])
                continue
            if item["pid"] in owner_pids(owner):
                actions.append(
                    {
                        "kind": kind,
                        "pid": item["pid"],
                        "blocked": True,
                        "reason": "target is the validated live owner",
                    }
                )
                attempted.add(item["pid"])
                continue
            termination = terminate_unpromoted(
                ctx, item["pid"], item.get("start_time")
            )
            actions.append(
                {
                    "kind": kind,
                    "pid": item["pid"],
                    "owner_pid": owner.get("pid"),
                    "termination": termination,
                }
            )
            attempted.add(item["pid"])

        after = _audit_daemon_health(ctx, now=now, cutover_state="held")
        # The final census is held to the same fail-closed rule: a refusal it
        # reveals is reported, never summarized as a clean apply.
        refused = refuse_termination(after)
        if refused is not None:
            return refused
        return {
            "service": ctx.service,
            "mode": "apply",
            "before": before,
            "after": after,
            "findings": before["findings"],
            "remaining_findings": after["findings"],
            "counts": after["counts"],
            "actions": actions,
        }
    finally:
        if guard is not None and hasattr(guard, "release"):
            guard.release()


def apply_daemon_health(
    ctx: DiagnosticContext, *, now: datetime | None = None,
) -> dict[str, object]:
    from .diagnostic_recovery import guarded_apply

    return guarded_apply(ctx, now=now)
