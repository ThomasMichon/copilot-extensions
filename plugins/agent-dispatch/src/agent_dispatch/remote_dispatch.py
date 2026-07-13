"""Cross-machine dispatch via SSH-push (Phase 8 Slice 8a).

agent-dispatch is **per-host**: each machine runs its own loopback coordinator
and ``agent-worktrees embody`` spawns a detached session *locally*. So "dispatch
to machine Y" runs the **same-machine** embody-dispatch **on Y** over the facility
SSH mesh -- the task lives on Y's coordinator and the autopilot session runs +
completes explicitly on Y. Reuses Tier-1 SSH + per-host embody; no coordinator
federation, no reverse tunnels, no dependency on the (unbuilt) peer-delegation
transport.

The machine name **is** its facility SSH alias (``ssh borealis``) -- never a raw
IP (the ``facility-ssh`` discipline). The payload is streamed over the SSH pipe's
stdin (``create --payload-file -``) so no shell-escaping of a large body is
needed; the remaining args are shell-quoted argv.
"""

from __future__ import annotations

import argparse
import shlex
import shutil
import subprocess


class RemoteDispatchUnavailable(RuntimeError):
    """Raised when the cross-machine dispatch transport (ssh) is unavailable."""


def local_machine() -> str | None:
    """This machine's name (its facility SSH alias), or None if unresolvable."""
    from .identity import resolve_identity

    return resolve_identity()[0]


def ssh_available() -> bool:
    """True if the ``ssh`` client is on PATH."""
    return shutil.which("ssh") is not None


def is_cross_machine(args: argparse.Namespace) -> bool:
    """True when this create is an embody spawn targeted at *another* machine.

    Cross-machine only kicks in for the embody spawn backend (a CLI-backed
    autopilot session): a bare targeted task, or a bridge-backed spawn, keeps its
    existing semantics. Returns False when the target is this machine, unset, or
    the local machine can't be resolved (then we dispatch locally as before).
    """
    if not getattr(args, "spawn", False) or getattr(args, "proposed", False):
        return False
    if getattr(args, "spawn_backend", "bridge") != "embody":
        return False
    target = getattr(args, "target_machine", None)
    if not target:
        return False
    local = local_machine()
    return bool(local) and target != local


def build_remote_create_argv(
    args: argparse.Namespace, *, repo: str, has_payload: bool
) -> list[str]:
    """Build the ``agent-dispatch create ... --spawn --spawn-backend embody`` argv
    to run **on the target**.

    Drops ``--target-machine`` (the target is *local* over there, so a second
    cross-machine hop must not trigger) and passes ``--repo`` explicitly (the
    remote SSH command runs in the home dir, which is not a repo, so the lane
    can't be auto-resolved). The payload rides stdin via ``--payload-file -``.
    """
    argv = [
        "agent-dispatch", "create", args.title,
        "--repo", repo,
        "--spawn", "--spawn-backend", "embody",
    ]
    if getattr(args, "prompt", ""):
        argv += ["--prompt", args.prompt]
    for label in getattr(args, "label", None) or []:
        argv += ["--label", label]
    for req in getattr(args, "require", None) or []:
        argv += ["--require", req]
    for aff in getattr(args, "affinity", None) or []:
        argv += ["--affinity", aff]
    if getattr(args, "target_repo", None):
        argv += ["--target-repo", args.target_repo]
    if getattr(args, "target_worktree", None):
        argv += ["--target-worktree", args.target_worktree]
    if getattr(args, "source", None):
        argv += ["--source", args.source]
    if getattr(args, "dedup_key", None):
        argv += ["--dedup-key", args.dedup_key]
    verify_timeout = getattr(args, "verify_timeout", 0) or 0
    if verify_timeout:
        argv += ["--verify-timeout", str(verify_timeout)]
    if has_payload:
        argv += ["--payload-file", "-"]
    return argv


def dispatch_to_remote(
    machine: str,
    args: argparse.Namespace,
    *,
    repo: str,
    payload: str | None,
    timeout: float | None = None,
) -> subprocess.CompletedProcess:
    """SSH to ``machine`` (its facility alias) and run the create+embody there.

    Raises :class:`RemoteDispatchUnavailable` if ``ssh`` is not on PATH; the
    caller degrades from there. The payload (if any) is streamed to the remote
    command's stdin.
    """
    exe = shutil.which("ssh")
    if exe is None:
        raise RemoteDispatchUnavailable("ssh not found on PATH")
    remote_argv = build_remote_create_argv(
        args, repo=repo, has_payload=payload is not None
    )
    remote_cmd = " ".join(shlex.quote(a) for a in remote_argv)
    # `machine` is the facility SSH alias (never a raw IP). BatchMode so a missing
    # key fails fast instead of hanging on a password prompt.
    cmd = [exe, "-o", "BatchMode=yes", machine, remote_cmd]
    return subprocess.run(  # noqa: S603 -- fixed argv, exe resolved via shutil.which
        cmd,
        input=payload,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
