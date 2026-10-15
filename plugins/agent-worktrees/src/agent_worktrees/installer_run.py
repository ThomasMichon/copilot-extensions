"""Run a runtime installer under a deadline that owns its whole process tree.

``subprocess.run(timeout=...)`` kills only the immediate child: the installer's
own ``pwsh``/``bash`` descendants (``uv``, a staged installer copy, a build)
keep running and can publish a slot after the updater has already labelled
the run from its timer. :func:`run_installer` instead

* starts the installer inside a kill-on-close Job on Windows (daemons it
  starts break away through ``agent_procutil.spawn_daemon``) or in its own
  session on POSIX, and on deadline expiry kills that whole tree;
* judges the outcome by the runtime's receipt -- the ``deploy-manifest.json``
  version the installer publishes -- rather than by the exit code alone, so
  an installer that exits 0 without activating the target is a failure, and
  a deadline that expires after the slot was published says so.

Statuses keep the shapes :func:`update_events.classify_status` already maps:
``OK``, ``installer exited <rc>`` (75/76/124 keep their outcomes),
``timed out (...)`` and a plain failure line for a missing receipt.
"""

from __future__ import annotations

import dataclasses
import os
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from agent_procutil import contained_test_mode, spawn_sync_in_kill_on_close_job

from . import push_timeout

#: How long to wait for a killed tree's root to be reaped before reporting.
_REAP_GRACE_S = 30.0
#: Exit code of the installers' own self-stage watchdog kill.
_WATCHDOG_EXIT = 124


@dataclasses.dataclass(frozen=True)
class InstallerRun:
    status: str
    rc: int | None
    receipt_version: str | None
    receipt_complete: bool
    duration_s: float


def _spawn(argv: Sequence[str], cwd: Path | None, env: Mapping[str, str], capture: bool):
    # Captured output is merged into one text stream; otherwise stdio stays
    # inherited (the installer reports to the updater's console).
    stdio = {"stdout": subprocess.PIPE, "stderr": subprocess.STDOUT, "text": True,
             "encoding": "utf-8", "errors": "replace"} if capture else {}
    if os.name == "nt":
        return spawn_sync_in_kill_on_close_job(list(argv), cwd=cwd, env=dict(env), **stdio)
    # Contained test mode keeps the child in the harness's own group.
    session = {} if contained_test_mode() else {"start_new_session": True}
    return subprocess.Popen(list(argv), cwd=cwd, env=dict(env), **session, **stdio), None


def _kill_tree(proc: subprocess.Popen, job) -> str:
    """Kill the whole tree; return whatever captured output remains."""
    if job is not None:
        job.close()
    else:
        push_timeout._kill_tree(proc)
    try:
        out, _ = proc.communicate(timeout=_REAP_GRACE_S)
    except subprocess.TimeoutExpired:
        out = None
    return out or ""


def _judge(
    rc: int | None, expired: bool, target: str | None, deployed: str | None,
) -> tuple[str, bool]:
    from .reconcile import _versions_equal

    complete = bool(target and deployed and _versions_equal(deployed, target))
    # 124 is the installer's own watchdog kill: the same deadline, one level down.
    if expired or rc == _WATCHDOG_EXIT:
        where = "slot published; deadline hit during cutover" if complete else (
            "deadline hit during build")
        return f"timed out ({where})", complete
    if rc != 0:
        return f"installer exited {rc}", complete
    if target is None or complete:
        return "OK", complete
    return (
        f"runtime not at target after a successful installer exit "
        f"(deployed {deployed or 'nothing'}, expected {target})"
    ), complete


def run_installer(
    argv: Sequence[str],
    *,
    cwd: Path | None,
    env: Mapping[str, str],
    deadline_s: float,
    target_version: str | None = None,
    receipt: Callable[[], str | None] | None = None,
    log: Callable[[str], None] | None = None,
) -> InstallerRun:
    """Run ``argv`` until it exits or ``deadline_s`` passes; never raises for a
    spawn failure (it becomes the status).

    ``receipt`` returns the runtime's published version (``None`` when there is
    none); with ``target_version`` it decides success. Without a target the
    exit code alone decides, as before. With ``log``, stdout and stderr are
    captured and passed to it line by line once the run ends (also after a
    deadline kill); otherwise they stay inherited.
    """
    started = time.monotonic()
    try:
        proc, job = _spawn(argv, cwd, env, log is not None)
    except OSError as exc:
        return InstallerRun(str(exc)[:120], None, None, False, time.monotonic() - started)
    expired, out = False, ""
    try:
        try:
            out = proc.communicate(timeout=deadline_s)[0] or ""
            rc: int | None = proc.returncode
        except subprocess.TimeoutExpired:
            expired, rc = True, None
            out = _kill_tree(proc, job)
            job = None
    except BaseException:
        _kill_tree(proc, job)
        job = None
        raise
    finally:
        if job is not None:
            # Everything the installer left behind dies with it; daemons it
            # started have broken away.
            job.close()
    if log is not None:
        for line in out.splitlines():
            log(line)
    deployed = receipt() if receipt is not None else None
    status, complete = _judge(rc, expired, target_version, deployed)
    return InstallerRun(status, rc, deployed, complete, time.monotonic() - started)
