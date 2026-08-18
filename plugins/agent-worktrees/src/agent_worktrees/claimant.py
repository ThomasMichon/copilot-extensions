"""Claimant-liveness resolution for resource-claims reap-safety.

A worktree created as another worktree's outbound resource carries an
``owner_ref`` (``machine/project/worktree_id[#session]``). Before a prune/GC
sweep reclaims such a resource it must answer: **is the owning worktree still
alive?** -- honoring the agent-fabric behavior ``claimed-resource-not-reclaimed``
(a resource with a live, or not-confirmed-gone, claimant is never reclaimed;
absence of a *local* owner is never proof of no owner).

Two layers:

* :func:`local_claimant_alive` -- the **same-machine** resolver: does the
  owner's tracking record + worktree directory still exist *on this machine*?
  Returns ``None`` for a cross-machine owner (it cannot see that machine).
* :func:`resolve_claimant_alive` -- the **fabric** resolver used by the reaper:
  same-machine goes local; a cross-machine owner is probed over the SSH mesh by
  running this same check *on the owner's machine* (which resolves it locally
  there). Any failure/timeout degrades to ``None`` (spare), so the network is
  never allowed to turn "unknown" into "reclaim".

The tri-state contract everywhere: ``True`` = alive (spare), ``None`` =
unconfirmed (spare), ``False`` = confirmed gone (reclaimable).
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
from pathlib import Path

from . import config as cfg
from . import tracking

#: Escape hatch: set truthy to disable cross-machine SSH probing entirely, so a
#: cross-machine owner always degrades to ``None`` (spare) without a network
#: call. For environments where the SSH mesh is unavailable or undesirable.
_NO_REMOTE_ENV = "AGENT_WORKTREES_NO_REMOTE_CLAIMANT"

#: Default per-probe SSH timeout (seconds). Short on purpose: the reaper calls
#: this per claimed resource, and a slow/unreachable owner machine must not hang
#: a sweep -- it degrades to ``None`` (spare) instead.
_REMOTE_TIMEOUT = 8.0

#: How stale a same-machine owner's last-known activity must be before, in the
#: ABSENCE of any live process/hint, the owner is judged dead so its in-flight
#: claims are released. Deliberately generous (24h default)
#: so a merely-idle-but-alive session -- which would anyway present a live
#: process or a fresh cached-live hint -- is never mistaken for dead; only a
#: session with no process, no fresh liveness hint, AND days-stale activity is
#: reclaimed. Override via ``AGENT_WORKTREES_CLAIMANT_DEAD_AFTER_SECS``.
_DEAD_AFTER_SECS = 86_400.0
_DEAD_AFTER_ENV = "AGENT_WORKTREES_CLAIMANT_DEAD_AFTER_SECS"


def _dead_after_secs() -> float:
    """Resolve the dead-after staleness threshold (env override, else default)."""
    raw = os.environ.get(_DEAD_AFTER_ENV)
    if raw:
        try:
            val = float(raw)
            if val > 0:
                return val
        except (TypeError, ValueError):
            pass
    return _DEAD_AFTER_SECS


def _parseable_iso(stamp: str | None) -> bool:
    """Whether ``stamp`` is a parseable ISO timestamp (empty/None -> False)."""
    if not stamp:
        return False
    from datetime import datetime
    try:
        datetime.fromisoformat(str(stamp))
        return True
    except (TypeError, ValueError):
        return False


def _owner_looks_dead(owner_rec, *, dead_after_secs: float | None = None) -> bool:
    """Positively conclude a same-machine owner worktree's session is DEAD.

    Used only for the "owner record present, non-terminal status, dir still on
    disk" branch of :func:`local_claimant_alive`: the dir existing is not proof
    the owning *session* is alive (a crashed/rebooted/abandoned session leaves
    its worktree dir behind). This confirms death conservatively -- returning
    ``True`` only when EVERY liveness signal is absent -- so a live-but-idle
    owner is never mistaken for gone (the safe direction: a false "dead" merely
    drops the claimed-resource shield, and the in-flight resource still keeps its
    OWN git/PR/session prune-safety):

      1. A live bound/mux Copilot process for the owner's registered sessions
         (:func:`sessions.worktree_has_live_session`) -> NOT dead.
      2. A fresh cached-live hint (``mux_live``/``bound_live`` True with a
         within-threshold ``*_at`` stamp -- covers a bare-resumed session that
         is invisible to the registered-session scan) -> NOT dead.
      3. Otherwise dead ONLY when every parseable activity timestamp
         (``started_at`` / ``last_resumed_at`` / a live ``*_live_at``) is older
         than ``dead_after_secs``. No parseable timeline at all -> spare.

    Best-effort: any error resolving liveness biases to NOT dead (spare).
    """
    if dead_after_secs is None:
        dead_after_secs = _dead_after_secs()
    try:
        from . import sessions
        if sessions.worktree_has_live_session(owner_rec):
            return False  # a live bound Copilot -> definitely alive
    except Exception:
        return False  # cannot resolve -> do not declare dead (spare)
    # Activity timeline: creation + last resume are always present; a cached
    # liveness *_at counts only when its paired *_live is True (a fresh
    # ``bound_live=False`` stamp means "confirmed NOT live", not a heartbeat).
    candidates = [getattr(owner_rec, "started_at", ""),
                  getattr(owner_rec, "last_resumed_at", "")]
    if getattr(owner_rec, "mux_live", None) is True:
        candidates.append(getattr(owner_rec, "mux_live_at", "") or "")
    if getattr(owner_rec, "bound_live", None) is True:
        candidates.append(getattr(owner_rec, "bound_live_at", "") or "")
    parseable = [c for c in candidates if _parseable_iso(c)]
    if not parseable:
        return False  # no timeline to judge staleness by -> spare
    return all(tracking._stamp_older_than(c, dead_after_secs) for c in parseable)


def local_claimant_alive(owner_ref: str) -> bool | None:
    """Same-machine claimant-liveness probe (resource-claims).

    Resolve the owning worktree named by ``owner_ref`` on THIS machine:

      * ``False`` -- the owner is on this machine and its worktree is gone (its
        tracking record is missing, or its recorded dir was removed) -> the
        claim is stale and the resource may be reclaimed.
      * ``True``  -- the owner is on this machine and still present -> spare.
      * ``None``  -- the owner is on a DIFFERENT machine (this resolver can't see
        it) or the ref can't be resolved -> UNCONFIRMED; spare. Cross-machine
        resolution is done by :func:`resolve_claimant_alive`.

    Only a *positively resolved, gone* same-machine owner returns ``False``.
    "Gone" here means either the owner's tracking record is missing, its
    recorded worktree dir was removed, its status is terminal, **or** its dir is
    still on disk but the owning session is confirmed dead -- no live process, no
    fresh liveness hint, and days-stale activity (see :func:`_owner_looks_dead`).

    **Anchor owners** (``<machine>/<project>/@anchor``) are permanent, so they
    invert the worktree rule: a *missing* anchor ledger is ``None`` (spare, never
    ``False``) and only a *removed anchor checkout* is ``False`` -- an anchor's
    claims settle by the resource's own proof, not by owner-death.
    """
    parsed = tracking.parse_claim_ref(owner_ref)
    if parsed is None:
        return None
    try:
        this_machine = cfg.load_config().machine
    except Exception:
        this_machine = None
    # Cross-machine (or an unknown local machine) -> not resolvable here.
    if parsed.machine and this_machine and parsed.machine != this_machine:
        return None
    project = parsed.project
    if not project:
        # A bare (legacy same-repo) ref -> assume the active project.
        try:
            project = cfg.project_name()
        except Exception:
            return None
    try:
        rec_path = cfg.project_dir(project) / "worktrees" / f"{parsed.worktree_id}.yaml"
    except Exception:
        return None
    # ANCHOR owner (a permanent, whole-repo enlistment) inverts the worktree
    # "missing record => gone" rule: an anchor is never pruned, so a *missing*
    # ledger is NOT proof the enlistment is gone -- it is UNCONFIRMED (spare).
    # Only a removed anchor checkout is a confirmed gone. An anchor's outbound
    # claims are therefore settled by the resource's own proof (a merged PR, a
    # merged child branch), never by owner-death.
    if parsed.is_anchor:
        if not rec_path.exists():
            return None  # no ledger yet -> unconfirmed, spare (never False)
        try:
            anchor_rec = tracking.load_record(rec_path)
        except Exception:
            return True  # present but unreadable -> bias to sparing
        if anchor_rec.worktree_path and not Path(anchor_rec.worktree_path).exists():
            return False  # the anchor checkout truly went away -> gone
        return True  # anchor present -> alive (permanent)
    if not rec_path.exists():
        return False  # owner record gone -> claim is stale
    try:
        owner_rec = tracking.load_record(rec_path)
    except Exception:
        return True  # record present but unreadable -> bias to sparing
    # Citadel E1b cascade (#877): a parent in a TERMINAL status (finalized /
    # orphaned) is done -- it no longer actively holds its outbound worktree
    # resources, so its children are orphans. Treat it as gone here so a
    # finalized-but-not-yet-pruned parent (whose dir still exists) stops pinning
    # its children as "claimed". The children remain protected by their OWN
    # git/PR/session prune safety, so nothing with real work is lost.
    if owner_rec.status in tracking._TERMINAL_OWNER_STATUSES:
        return False
    if owner_rec.worktree_path and not Path(owner_rec.worktree_path).exists():
        return False  # owner's worktree dir removed -> gone
    # Dead-session detection: a still-on-disk worktree dir is
    # NOT proof the owning *session* is alive -- a crashed/rebooted/abandoned
    # session leaves its dir behind and would otherwise pin its in-flight claims
    # forever. Release the claim only when the session is CONFIRMED gone (no live
    # process, no fresh liveness hint, and days-stale activity); the freed
    # resources still keep their own git/PR/session prune-safety.
    if _owner_looks_dead(owner_rec):
        return False
    return True  # owner still present -> spare


def resolve_claimant_alive(
    owner_ref: str, *, allow_remote: bool = True,
    timeout: float = _REMOTE_TIMEOUT,
) -> bool | None:
    """Fabric-wide claimant-liveness: local for a same-machine owner, an SSH
    probe to the owner's machine for a cross-machine owner.

    This is the resolver the reaper wires into ``prune.assess``. A cross-machine
    owner is probed by running :func:`local_claimant_alive` *on the owner's
    machine* over the SSH mesh; any failure/timeout/unreachability degrades to
    ``None`` (spare). Remote probing is skipped (``None``) when disabled via the
    ``AGENT_WORKTREES_NO_REMOTE_CLAIMANT`` env or ``allow_remote=False``.
    """
    parsed = tracking.parse_claim_ref(owner_ref)
    if parsed is None:
        return None
    try:
        this_machine = cfg.load_config().machine
    except Exception:
        this_machine = None
    same_machine = not (
        parsed.machine and this_machine and parsed.machine != this_machine)
    if same_machine:
        return local_claimant_alive(owner_ref)
    if not allow_remote or os.environ.get(_NO_REMOTE_ENV):
        return None
    return _remote_claimant_alive(parsed.machine, parsed.project, owner_ref,
                                  timeout=timeout)


def _resolve_machine_ssh(machine_key: str) -> tuple[str, str] | None:
    """Resolve ``(alias, shell)`` for a machine key from the registry.

    Returns None when the registry is unavailable, the key is unknown/not
    Copilot-enabled/not ssh-ready, or it has no ssh environment with an alias.
    Picks the first ready ssh environment that carries an alias; the machine's
    top-level ``alias`` is a fallback.
    """
    try:
        config = cfg.load_config()
        entries = cfg.load_machines_yaml(config.default_repo.anchor)
    except Exception:
        return None
    m = entries.get(machine_key)
    # Case-insensitive fallback + alias match.
    if m is None:
        low = machine_key.lower()
        for k, e in entries.items():
            if k.lower() == low or (e.alias and e.alias.lower() == low):
                m = e
                break
    if m is None or not getattr(m, "copilot", True) or not m.ssh_ready:
        return None
    for env in m.ssh_environments:
        if env.alias:
            shell = env.shell or ("pwsh" if (env.name or "").lower() == "windows"
                                  else "bash")
            return (env.alias, shell)
    if m.alias:
        return (m.alias, "bash")
    return None


def _remote_probe_cmd(shell: str, project: str, owner_ref: str) -> str:
    """Build the remote command string that runs the liveness check.

    Invokes the project's binstub with the ``claimant-liveness`` verb, wrapped
    for the remote shell (pwsh EncodedCommand on Windows -- robust against a
    cmd.exe default sshd shell; ``bash -lc`` elsewhere).
    """
    inner = f"{project} claimant-liveness {owner_ref} --json"
    if shell == "pwsh":
        enc = base64.b64encode(inner.encode("utf-16-le")).decode("ascii")
        return f"pwsh -NoProfile -WindowStyle Hidden -EncodedCommand {enc}"
    return f"bash -lc '{inner}'"


def _parse_alive(stdout: str) -> bool | None:
    """Extract the ``alive`` tri-state from a remote claimant-liveness envelope.

    Tolerates surrounding shell/banner noise by scanning for the JSON object.
    A missing/unparseable value yields ``None`` (unconfirmed).
    """
    if not stdout:
        return None
    start = stdout.find("{")
    end = stdout.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(stdout[start:end + 1])
    except (ValueError, TypeError):
        return None
    alive = data.get("alive")
    return alive if alive in (True, False) else None


def _remote_claimant_alive(
    machine_key: str | None, project: str | None, owner_ref: str,
    *, timeout: float = _REMOTE_TIMEOUT,
) -> bool | None:
    """Probe a cross-machine owner's liveness over SSH; ``None`` on any doubt.

    Runs ``local_claimant_alive`` on the owner's machine (where the owner is
    same-machine and resolves definitively) and returns its verdict. Every
    failure mode -- no registry entry, unresolved binstub, ssh error, timeout,
    unparseable output -- degrades to ``None`` (spare).
    """
    if not machine_key or not project:
        return None
    resolved = _resolve_machine_ssh(machine_key)
    if resolved is None:
        return None
    alias, shell = resolved
    remote_cmd = _remote_probe_cmd(shell, project, owner_ref)
    try:
        proc = subprocess.run(
            ["ssh", "-o", "BatchMode=yes",
             "-o", f"ConnectTimeout={max(1, int(timeout))}",
             alias, remote_cmd],
            capture_output=True, text=True, timeout=timeout + 4,
        )
    except (subprocess.SubprocessError, OSError):
        return None
    if proc.returncode != 0:
        return None
    return _parse_alive(proc.stdout)
