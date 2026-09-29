"""Shared daemon-health audit + repair helpers for ``zdd`` consumers."""

from __future__ import annotations

import os
import platform
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import breadcrumb, routing

__all__ = [
    "DaemonCandidate",
    "DiagnosticContext",
    "apply_daemon_health",
    "audit_daemon_health",
    "lock_data_is_live",
    "process_start_time",
    "terminate_pid_if_identity",
]


@dataclass(frozen=True)
class DaemonCandidate:
    pid: int
    start_time: str | None = None
    note: str | None = None

    def to_dict(self) -> dict[str, object]:
        data: dict[str, object] = {"pid": self.pid, "start_time": self.start_time}
        if self.note:
            data["note"] = self.note
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
        try:
            import ctypes
            from ctypes import wintypes

            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            ERROR_INVALID_PARAMETER = 87
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            k32.OpenProcess.restype = wintypes.HANDLE
            k32.CloseHandle.argtypes = [wintypes.HANDLE]
            k32.CloseHandle.restype = wintypes.BOOL
            handle = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if handle:
                k32.CloseHandle(handle)
                return True
            return ctypes.get_last_error() != ERROR_INVALID_PARAMETER
        except (OSError, ctypes.ArgumentError):
            return True
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


def _terminate_windows_if_identity(pid: int, expected_start_time: str) -> dict:
    import ctypes
    from ctypes import wintypes

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    PROCESS_TERMINATE = 0x0001
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
        PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_TERMINATE, False, pid
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
        return {
            "killed": bool(k32.TerminateProcess(handle, 1)),
            "identity_verified": True,
            "method": "windows-verified-handle",
        }
    finally:
        k32.CloseHandle(handle)


def _terminate_posix_if_identity(pid: int, expected_start_time: str) -> dict:
    import signal

    pidfd_open = getattr(os, "pidfd_open", None)
    pidfd_send_signal = getattr(signal, "pidfd_send_signal", None)
    if not callable(pidfd_open) or not callable(pidfd_send_signal):
        return {
            "killed": False,
            "identity_verified": False,
            "method": "pidfd-unavailable",
        }
    try:
        fd = pidfd_open(pid, 0)
    except OSError:
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
        return {
            "killed": True,
            "identity_verified": True,
            "method": "pidfd",
        }
    finally:
        os.close(fd)


def terminate_pid_if_identity(pid: int, expected_start_time: str | None) -> dict:
    """Terminate only through an OS object bound to the verified process."""
    if pid <= 0 or not expected_start_time:
        return {
            "killed": False,
            "identity_verified": False,
            "method": "identity-unavailable",
        }
    if platform.system() == "Windows":
        return _terminate_windows_if_identity(pid, str(expected_start_time))
    return _terminate_posix_if_identity(pid, str(expected_start_time))


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
    terminate_pid_if_identity: Callable[[int, str | None], dict] = terminate_pid_if_identity
    lock_is_live: Callable[[dict | None], bool] = lock_data_is_live
    make_client: Callable[[str], Any] | None = None
    health_check: Callable[[str, int], bool] | None = None
    abandoned_passive_grace_seconds: float = breadcrumb.DEFAULT_ABANDONED_PASSIVE_GRACE_S


def _candidate_map(candidates: list[DaemonCandidate]) -> dict[int, DaemonCandidate]:
    return {
        candidate.pid: candidate
        for candidate in candidates
        if isinstance(candidate.pid, int) and candidate.pid > 0
    }


def _validated_owner(
    ctx: DiagnosticContext,
    candidates: dict[int, DaemonCandidate],
    table: dict | None,
) -> tuple[dict[str, object] | None, str | None]:
    lock_data = ctx.read_lock()
    if not ctx.lock_is_live(lock_data):
        return None, "no validated live owner"
    if not isinstance(lock_data, dict):
        return None, "lock unreadable"
    pid = lock_data.get("pid")
    if not isinstance(pid, int):
        return None, "lock missing owner pid"
    active_raw = table.get("active") if isinstance(table, dict) else None
    active_pid = active_raw.get("pid") if isinstance(active_raw, dict) else None
    if isinstance(active_pid, int) and active_pid != pid:
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
    if active_pid is not None and new_pid == active_pid:
        return None, None
    candidate = candidates.get(new_pid)
    if candidate is None:
        return None, None
    repairable = ctx.repair_supported and candidate.start_time is not None
    return (
        {
            "kind": "abandoned_passive",
            "summary": "stale cutover breadcrumb names a never-promoted passive daemon",
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
            "targets": [candidate.to_dict()],
            "age_seconds": age_s,
        },
        new_pid,
    )


def _inspect_superseded_generations(
    ctx: DiagnosticContext,
    table: dict | None,
    candidates: dict[int, DaemonCandidate],
) -> tuple[dict[str, object] | None, set[int]]:
    if not isinstance(table, dict):
        return None, set()
    targets: list[dict[str, object]] = []
    matched: set[int] = set()
    for slot in ("previous", "active"):
        raw = table.get(slot)
        if not isinstance(raw, dict):
            continue
        pid = raw.get("pid")
        candidate = candidates.get(pid) if isinstance(pid, int) else None
        if candidate is None:
            continue
        try:
            generation = int(raw.get("generation", 0))
        except (TypeError, ValueError):
            continue
        if generation <= 0 or not ctx.is_superseded(candidate.pid, generation):
            continue
        matched.add(candidate.pid)
        item = candidate.to_dict()
        item["slot"] = slot
        item["generation"] = generation
        targets.append(item)
    if not targets:
        return None, set()
    repairable = ctx.repair_supported and all(item.get("start_time") for item in targets)
    return (
        {
            "kind": "superseded_generation",
            "summary": "a routed, superseded generation is still running past self-retire",
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
        },
        matched,
    )


def _inspect_duplicates(
    ctx: DiagnosticContext,
    candidates: dict[int, DaemonCandidate],
    owner: dict[str, object] | None,
    excluded_pids: set[int],
) -> dict[str, object] | None:
    if len(candidates) <= 1:
        return None
    owner_pid = owner.get("pid") if isinstance(owner, dict) else None
    targets = [
        candidate.to_dict()
        for pid, candidate in sorted(candidates.items())
        if pid not in excluded_pids and pid != owner_pid
    ]
    if owner_pid is None:
        targets = [candidate.to_dict() for _, candidate in sorted(candidates.items())]
    if not targets:
        return None
    repairable = (
        ctx.repair_supported
        and owner_pid is not None
        and all(item.get("start_time") for item in targets)
    )
    return {
        "kind": "duplicate_resident",
        "summary": "more than one live daemon matches the resident active slot",
        "repairable": repairable,
        "blocked_reason": (
            None
            if repairable
            else (
                "identity-bound repair unsupported on this platform"
                if not ctx.repair_supported
                else (
                "no validated live owner"
                if owner_pid is None
                else "candidate start_time unavailable"
                )
            )
        ),
        "owner": owner,
        "targets": targets,
    }


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

    candidates = _candidate_map(ctx.list_candidates())
    table = routing.read_table(ctx.config_dir)
    owner, owner_reason = _validated_owner(ctx, candidates, table)
    record = breadcrumb.read_breadcrumb(ctx.config_dir)
    active_raw = table.get("active") if isinstance(table, dict) else None
    active_pid = active_raw.get("pid") if isinstance(active_raw, dict) else None

    findings: list[dict[str, object]] = []
    try:
        stranded = _inspect_stranded_survivor(ctx, record)
        if stranded is not None:
            findings.append(stranded)

        abandoned, abandoned_pid = _inspect_abandoned_passive(
            ctx,
            record,
            int(active_pid) if isinstance(active_pid, int) else None,
            candidates,
            now=now,
        )
        if abandoned is not None:
            findings.append(abandoned)

        superseded, superseded_pids = _inspect_superseded_generations(
            ctx, table, candidates
        )
        if superseded is not None:
            findings.append(superseded)

        excluded = set(superseded_pids)
        if abandoned_pid is not None:
            excluded.add(abandoned_pid)
        duplicate = _inspect_duplicates(ctx, candidates, owner, excluded)
        if duplicate is not None:
            findings.append(duplicate)
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


def apply_daemon_health(
    ctx: DiagnosticContext,
    *,
    now: datetime | None = None,
) -> dict[str, object]:
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
            recovery = breadcrumb.recover_stale_cutover(
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
                if item["pid"] == owner.get("pid"):
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
                termination = ctx.terminate_pid_if_identity(
                    item["pid"], item.get("start_time")
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
            if item["pid"] == owner.get("pid"):
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
            termination = ctx.terminate_pid_if_identity(
                item["pid"], item.get("start_time")
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
