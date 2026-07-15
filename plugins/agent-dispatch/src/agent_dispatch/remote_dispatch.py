"""Cross-machine dispatch + peer-queue browse via SSH-push (Phase 8 Slices 8a, 8c).

agent-dispatch is **per-host**: each machine runs its own loopback coordinator
and ``agent-worktrees embody`` spawns a detached session *locally*. So "dispatch
to machine Y" runs the **same-machine** embody-dispatch **on Y** over the facility
SSH mesh -- the task lives on Y's coordinator and the autopilot session runs +
completes explicitly on Y (Slice 8a). Reuses Tier-1 SSH + per-host embody; no
coordinator federation, no reverse tunnels, no dependency on the (unbuilt)
peer-delegation transport.

The same SSH-exec pattern also powers **peer-queue browse** (Slice 8c):
``list``/``inbox --machine Y`` (Y != local) run the read command **on Y** and
stream back its JSON, so an operator can inspect a peer's queue -- and, since 8b
runs there, its live embodiment overlays -- without leaving the local box.

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
import sys


class RemoteDispatchUnavailable(RuntimeError):
    """Raised when the cross-machine dispatch transport (ssh) is unavailable."""


def local_machine() -> str | None:
    """This machine's name (its facility SSH alias), or None if unresolvable."""
    from .identity import resolve_identity

    return resolve_identity()[0]


def ssh_available() -> bool:
    """True if the ``ssh`` client is on PATH."""
    return shutil.which("ssh") is not None


def wsl_coordinator_present() -> bool:
    """True on Windows when a WSL distro has agent-dispatch installed.

    On a physical box that runs both a Windows and a WSL coordinator, the
    coordinator MUST live in WSL: a WSL-bound loopback port is reachable from
    BOTH envs (Windows via WSL2 localhost-forwarding, WSL natively), whereas a
    Windows-bound port is NOT reachable from WSL over localhost. So a Windows
    ``serve`` must **defer** when a WSL peer exists -- starting a second
    coordinator would either collide on ``127.0.0.1:9847`` (bind failure) or,
    if it wins the race, split-brain into two coordinators with two task DBs
    (issue #2777). The Windows CLI still reaches the WSL coordinator via the
    forwarded ``127.0.0.1:9847``.

    Off Windows this is always False (Linux/WSL is the correct coordinator home).
    Best-effort and bounded: a missing/broken ``wsl.exe`` yields False.
    """
    if sys.platform != "win32":
        return False
    exe = shutil.which("wsl.exe") or shutil.which("wsl")
    if exe is None:
        return False
    try:
        proc = subprocess.run(  # noqa: S603 -- fixed argv, exe via shutil.which
            [exe, "-e", "bash", "-lc",
             'test -x "$HOME/.agent-dispatch/.venv/bin/agent-dispatch"'],
            check=False, capture_output=True, timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0


def _norm_machine(name: str | None) -> str:
    """Normalize a machine name for **identity comparison**: trimmed + casefolded.

    Facility machine names (a machine's registry key / SSH alias) are lowercase
    by convention, but a caller may hand us a display-cased variant -- e.g. the
    worktree picker passes the ``machines.yaml`` ``display_name`` (``Lambda-Core``)
    while this machine's resolved identity is the registry key (``lambda-core``).
    A case-sensitive ``==`` would then misread the *local* machine as a remote
    peer and try to SSH to itself. Comparing casefolded values keeps local/peer
    detection case-insensitive. Returns ``""`` for ``None``/empty.
    """
    return (name or "").strip().casefold()


def _ssh_alias(machine: str) -> str:
    """The SSH alias to connect to for ``machine``: lowercased.

    SSH ``Host`` matching is case-sensitive, and facility ``Host`` blocks are
    lowercase by convention (``Host borealis``). A display-cased name
    (``Borealis``) would miss its ``Host`` block and fall back to a literal
    ``Borealis`` hostname on the default port -- which fails. Lowercasing keeps a
    case-insensitive caller reaching the intended peer over the mesh. The
    original (caller-supplied) name is still used for human-facing diagnostics.
    """
    return machine.strip().lower()


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
    return bool(local) and _norm_machine(target) != _norm_machine(local)


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
    # key fails fast instead of hanging on a password prompt. Lowercased so a
    # display-cased name still matches its lowercase `Host` block.
    cmd = [exe, "-o", "BatchMode=yes", _ssh_alias(machine), remote_cmd]
    return subprocess.run(  # noqa: S603 -- fixed argv, exe resolved via shutil.which
        cmd,
        input=payload,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


# -- Peer-queue browse (Phase 8 Slice 8c) ------------------------------------


def is_peer_machine(machine: str | None) -> bool:
    """True when ``machine`` names a *remote* peer (not this machine).

    Used by ``list``/``inbox --machine Y`` to decide whether to read Y's queue
    over the mesh (Y != local) or fall through to local behavior. Returns False
    when ``machine`` is unset, is this machine, or the local machine can't be
    resolved (then we can't prove it's remote, so stay local -- the safe
    degrade, mirroring :func:`is_cross_machine`).
    """
    if not machine:
        return False
    local = local_machine()
    return bool(local) and _norm_machine(machine) != _norm_machine(local)


def build_remote_browse_argv(
    subcommand: str, args: argparse.Namespace, *, repo: str | None = None
) -> list[str]:
    """Build the ``agent-dispatch <subcommand> ...`` argv to run **on the peer**
    for a peer-queue browse.

    Forwards the read filters (``--status``/``--label``/``--limit``, plus
    ``--repo``/``--target-machine``/``--target-repo`` for ``list``). ``list``
    scopes to a repo lane, which can't be resolved from the peer's SSH home dir,
    so the locally-resolved ``repo`` is passed explicitly.

    ``--machine Y`` handling differs by subcommand:

    - ``inbox`` **forwards** it -- the peer needs it as its scoping identity
      (``inbox`` can't resolve a machine from the SSH home dir), it is
      backward-compatible (``inbox --machine`` has always existed), and it is
      hop-safe: the peer we reached over SSH **is** ``Y``, so its
      :func:`local_machine` resolves to ``Y`` (or ``None``) and
      :func:`is_peer_machine` returns False -- the peer's run stays strictly
      local, no second hop.
    - ``list`` **drops** it -- ``list`` needs no machine identity (it scopes by
      ``--repo``), so forwarding a *new* ``list --machine`` flag would only break
      a peer running an older agent-dispatch that doesn't know it. Dropping it
      also guarantees no second hop.
    """
    argv = ["agent-dispatch", subcommand]
    if subcommand == "inbox" and getattr(args, "machine", None):
        argv += ["--machine", args.machine]
    if getattr(args, "status", None):
        argv += ["--status", args.status]
    if getattr(args, "label", None):
        argv += ["--label", args.label]
    limit = getattr(args, "limit", None)
    if limit:
        argv += ["--limit", str(limit)]
    if subcommand == "list":
        if repo:
            argv += ["--repo", repo]
        if getattr(args, "target_machine", None):
            argv += ["--target-machine", args.target_machine]
        if getattr(args, "target_repo", None):
            argv += ["--target-repo", args.target_repo]
    return argv


def browse_remote(
    machine: str, argv: list[str], *, timeout: float | None = None
) -> subprocess.CompletedProcess:
    """SSH to ``machine`` (its facility alias) and run an ``agent-dispatch`` read
    command there, returning its captured result (JSON on stdout).

    Raises :class:`RemoteDispatchUnavailable` if ``ssh`` is not on PATH; the
    caller degrades from there.
    """
    exe = shutil.which("ssh")
    if exe is None:
        raise RemoteDispatchUnavailable("ssh not found on PATH")
    remote_cmd = " ".join(shlex.quote(a) for a in argv)
    cmd = [exe, "-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
           _ssh_alias(machine), remote_cmd]
    return subprocess.run(  # noqa: S603 -- fixed argv, exe resolved via shutil.which
        cmd,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def diagnose_remote_failure(
    machine: str, returncode: int, stderr: str | None
) -> str:
    """Translate a failed remote ``agent-dispatch`` invocation into one concise,
    actionable line -- shared by cross-machine dispatch (8a) and peer-queue
    browse (8c) so a peer that lacks the install or a running coordinator
    reports *why*, not a raw ``command not found`` or an httpx traceback.

    - **exit 127** -- the shell couldn't find ``agent-dispatch`` on the peer
      (not installed, or its binstub dir isn't on the non-interactive SSH PATH).
    - **connection refused / ConnectError** in stderr -- the peer has the CLI but
      its per-host coordinator isn't reachable (not running).
    - otherwise -- a trimmed tail of the remote stderr with the exit code.
    """
    if returncode == 127:
        return (
            f"agent-dispatch is not installed (or not on the non-interactive SSH "
            f"PATH) on {machine!r} -- install it there; a per-host coordinator is "
            f"required for cross-machine dispatch/browse"
        )
    low = (stderr or "").lower()
    if any(
        s in low
        for s in ("connecterror", "refused", "10061", "max retries",
                  "failed to establish", "connection error")
    ):
        return (
            f"could not reach the agent-dispatch coordinator on {machine!r} "
            f"-- is its coordinator running?"
        )
    tail = ""
    if stderr:
        lines = [ln.strip() for ln in stderr.splitlines() if ln.strip()]
        tail = lines[-1] if lines else ""
    base = f"agent-dispatch on {machine!r} failed (exit {returncode})"
    return f"{base}: {tail}" if tail else base
