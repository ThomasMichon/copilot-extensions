"""Boundary I / ThomasMichon/copilot-extensions#2584: a ``task``-kind
agent-worktrees claim that blocks a hibernating worktree from being
finalized out from under a still-open task.

``agent-dispatch run --detach --task <id>`` (see :mod:`agent_dispatch.hibernation`
and its ``__main__`` CLI wiring) tears the worker's session down while a
detached waiter blocks on some external event, expecting to resume the *same*
worktree later with context intact. Without an explicit claim, a worktree
lifecycle sweep that only checks for a *live session* (correctly absent here --
that is the whole point of hibernation) can still reclaim the worktree as
"unused", permanently breaking the resume. Journaling a ``task``-kind claim on
the current worktree closes that gap: agent-worktrees' finalize-preservation
gate is kind-agnostic (any unsettled entry in the claim ledger blocks), and
``task`` is deliberately left unresolved by the general reclaim sweep (like the
pre-existing ``ssh``/``workdir`` placeholder kinds) since only agent-dispatch
itself can know when the task is genuinely done.

A standalone module (not part of :mod:`agent_dispatch.embody`, which already
sits at this repo's per-module line cap) so the CLI can import just this
narrow surface.
"""

from __future__ import annotations

import json
import shlex
import shutil
import subprocess

from . import bridge_remote, remote_dispatch
from .procutil import (
    agent_worktrees_launch_prefix,
    no_window_kwargs,
    run_ssh_capture,
)


def waiter_claim_key(task_id: str, generation: int) -> str:
    """Generation-scoped worktree-claim id for one detached waiter attempt."""
    return f"{task_id}:{int(generation)}"


def _mirror_task_claim_status(
    task_id: str, status: str, *, timeout: float = 15.0
) -> None:
    """Best-effort mirror of this task claim's disposition onto agent-worktrees'
    cross-machine ``task_claim_registry`` (ThomasMichon/copilot-extensions#2584).

    Same-machine journaling (:func:`add_hibernation_claim` /
    :func:`release_hibernation_claim`) already blocks *this* machine's own
    finalize sweep. This additionally mirrors the disposition onto a
    4-tier-resolved external store (a bound knowledge repo's remote, this
    project's own remote, or a local/machine-local fallback) so a reclaim
    sweep running anywhere else that resolves the same store can see this
    task's status too. Failure is silent and non-fatal -- the caller's own
    same-machine claim add/release already carries the real obligation.
    """
    prefix = agent_worktrees_launch_prefix()
    if prefix is None:
        return
    try:
        subprocess.run(  # noqa: S603 -- fixed argv, launcher resolved locally
            [*prefix, "claims", "mirror-status", "task", task_id,
             "--status", status, "--holder", "agent-dispatch", "--json"],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            **no_window_kwargs(),
        )
    except (OSError, subprocess.SubprocessError):
        pass


def add_hibernation_claim(
    task_id: str, *, note: str | None = None, timeout: float = 15.0
) -> dict | None:
    """Journal a ``task``-kind claim on the *current* worktree.

    Best-effort and non-fatal: returns ``None`` (never raises) when the
    ``agent-worktrees`` CLI is unavailable or the call otherwise fails, since
    a worker that cannot journal the claim should still proceed with
    suspending/hibernating rather than block on it -- the claim is defense in
    depth, not a precondition. Release the matching claim with
    :func:`release_hibernation_claim` once the task resumes.

    Also best-effort mirrors the claim as ``active`` onto the cross-machine
    discovery store (see :func:`_mirror_task_claim_status`).
    """
    prefix = agent_worktrees_launch_prefix()
    if prefix is None:
        return None
    args = ["claims", "add", "task", task_id, "--json"]
    if note:
        args += ["--note", note[:300]]
    try:
        result = subprocess.run(  # noqa: S603 -- fixed argv, launcher resolved locally
            [*prefix, *args],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            **no_window_kwargs(),
        )
        payload = json.loads(result.stdout or "{}")
    except (OSError, subprocess.SubprocessError, TypeError, ValueError):
        return None
    if result.returncode != 0 or not isinstance(payload, dict):
        return None
    _mirror_task_claim_status(task_id, "active", timeout=timeout)
    return payload


def release_hibernation_claim(task_id: str, *, timeout: float = 15.0) -> dict | None:
    """Retire the ``task``-kind claim :func:`add_hibernation_claim` journaled.

    Best-effort and non-fatal (mirrors ``add_hibernation_claim``): a resume
    must never fail because releasing the claim did. A claim left behind
    (e.g. the CLI was unavailable at release time) is inert -- it only ever
    blocked *this* worktree's own finalize, and a worktree that never gets
    finalized is a cheap, safe failure mode compared to one reclaimed too
    early.

    Also best-effort mirrors the claim as ``released`` onto the cross-machine
    discovery store (see :func:`_mirror_task_claim_status`).
    """
    prefix = agent_worktrees_launch_prefix()
    if prefix is None:
        return None
    try:
        result = subprocess.run(  # noqa: S603 -- fixed argv, launcher resolved locally
            [*prefix, "claims", "release", task_id, "--json"],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            **no_window_kwargs(),
        )
        payload = json.loads(result.stdout or "{}")
    except (OSError, subprocess.SubprocessError, TypeError, ValueError):
        return None
    if result.returncode != 0 or not isinstance(payload, dict):
        return None
    _mirror_task_claim_status(task_id, "released", timeout=timeout)
    return payload


def release_hibernation_claim_for_worktree(
    task_id: str,
    worktree_id: str,
    *,
    timeout: float = 15.0,
) -> dict | None:
    """Retire a hibernation claim targeting an explicit worktree id."""
    prefix = agent_worktrees_launch_prefix()
    if prefix is None:
        return None
    try:
        result = subprocess.run(  # noqa: S603 -- fixed argv, launcher resolved locally
            [*prefix, "claims", "release", task_id, "--worktree", worktree_id, "--json"],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            **no_window_kwargs(),
        )
        payload = json.loads(result.stdout or "{}")
    except (OSError, subprocess.SubprocessError, TypeError, ValueError):
        return None
    if result.returncode != 0 or not isinstance(payload, dict):
        return None
    _mirror_task_claim_status(task_id, "released", timeout=timeout)
    return payload


def release_hibernation_claim_for_host_worktree(
    task_id: str,
    host: str | None,
    worktree_id: str,
    repo: str | None,
    *,
    timeout: float = 15.0,
) -> dict | None:
    """Retire a hibernation claim on the machine that originally journaled it."""
    if not worktree_id:
        return None
    from . import identity

    project = identity.name_for_repo(repo) if repo else None
    current = remote_dispatch.local_machine()
    if host is None or (current is not None and host == current):
        prefix = agent_worktrees_launch_prefix()
        if prefix is None:
            return None
        argv = [*prefix]
        if project:
            argv += ["--project", project]
        argv += ["claims", "release", task_id, "--worktree", worktree_id, "--json"]
        try:
            result = subprocess.run(  # noqa: S603 -- fixed argv, launcher resolved locally
                argv,
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout,
                **no_window_kwargs(),
            )
            payload = json.loads(result.stdout or "{}")
        except (OSError, subprocess.SubprocessError, TypeError, ValueError):
            return None
        if result.returncode != 0 or not isinstance(payload, dict):
            return None
        _mirror_task_claim_status(task_id, "released", timeout=timeout)
        return payload
    ssh = shutil.which("ssh")
    if ssh is None:
        return None
    remote_cmd = " ".join(
        shlex.quote(part)
        for part in [
            "agent-worktrees",
            *(["--project", project] if project else []),
            "claims",
            "release",
            task_id,
            "--worktree",
            worktree_id,
            "--json",
        ]
    )
    result = run_ssh_capture(
        [
            ssh,
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=3",
            bridge_remote.normalize_host(host),
            remote_cmd,
        ],
        timeout=timeout,
    )
    if result is None:
        return None
    try:
        payload = json.loads(result.stdout or "{}")
    except (TypeError, ValueError):
        return None
    if result.returncode != 0 or not isinstance(payload, dict):
        return None
    _mirror_task_claim_status(task_id, "released", timeout=timeout)
    return payload
