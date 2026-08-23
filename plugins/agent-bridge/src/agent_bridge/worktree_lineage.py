"""Write a worktree's *session lineage* into the agent-worktrees ground layer.

The read-side companion of :mod:`worktree_head`. agent-worktrees **owns** the
durable session-lifecycle state (a per-worktree head derived from per-session
lifecycle + succession slots); agent-bridge keeps **no rival copy** of that
state (the vision's ``derive-dont-duplicate``). Historically the bridge violated
this on the ACP path: a bridge/Neuron-Forge handoff wrote succession only into
the bridge's own DB and never told the ground layer, so ``session-role`` /
``head-session`` diverged for ACP sessions and an ACP successor never learned it
*was* a successor.

This module closes that gap by shelling the **agent-worktrees CLI** to contribute
the lifecycle *facts* the ground layer reduces into the derived head:

- :func:`register_session` -- an ACP session started in a local worktree (so the
  ground layer knows it exists and the derived head is correct). The vision's
  *explicit session binding* names exactly this case ("a spawned successor, a
  headless launch ... would otherwise never register and leave the worktree
  looking unowned").
- :func:`link_succession` -- at a bridge handoff, mark the predecessor
  ``handed-off`` and make the successor the derived head in one atomic write.
- :func:`note_handoff` -- mirror the handoff into the worktree record's history.

Reads used to seed a successor's lineage awareness (the ``sessionStart`` role/
digest hook does **not** fire under ``copilot --acp``, so the successor gets this
via its opening turn instead):

- :func:`session_role` -- the successor's role over the ground layer.
- :func:`history_digest` -- the worktree's recent session-tagged history.

Every write and read is **fail-open**: a missing binstub, an untracked worktree,
a timeout, or a non-zero exit degrades to a no-op (writes) or ``None`` (reads) so
the ground layer is a *supplement*, never a mainline dependency -- a bridge
handoff must never break because the CLI could not be consulted.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess

from agent_procutil import no_window_flags

from .agent_registry import _agent_worktrees_bin

log = logging.getLogger("agent_bridge.worktree_lineage")

# Ground-layer writes are cheap local YAML/JSONL touches; keep the subprocess
# budget small so a wedged binstub can never stall a session start or handoff for
# long (fail-open on timeout, exactly like the head read).
_LINEAGE_TIMEOUT_S = 10.0

# env markers scrubbed so a uv-managed child Python does not trip an `_sre`
# module mismatch -- the same guard worktree_head / agent_registry use.
_SCRUB = ("VIRTUAL_ENV", "PYTHONHOME", "__PYVENV_LAUNCHER__", "PYTHONPATH")


def _child_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if k not in _SCRUB}


def _run(args: list[str]) -> subprocess.CompletedProcess[str] | None:
    """Shell ``agent-worktrees <args>`` fail-open; return the completed process
    or ``None`` when the binstub is missing / the call could not run."""
    exe = _agent_worktrees_bin()
    if not exe:
        log.debug("agent-worktrees binstub not found -- lineage write skipped")
        return None
    try:
        proc = subprocess.run(  # noqa: S603 -- fixed argv, exe via shutil.which
            [exe, *args],
            capture_output=True,
            text=True,
            timeout=_LINEAGE_TIMEOUT_S,
            check=False,
            creationflags=no_window_flags(),
            env=_child_env(),
        )
    except Exception as exc:  # broad by design -- fail-open contract
        log.warning("agent-worktrees %s failed: %s", args[0] if args else "?", exc)
        return None
    if proc.returncode != 0:
        log.debug(
            "agent-worktrees %s exited %s: %s",
            args[0] if args else "?", proc.returncode, (proc.stderr or "").strip()[:200],
        )
    return proc


# -- writes -----------------------------------------------------------------


def register_session(
    worktree_id: str,
    session_id: str,
    *,
    pid: int | None = None,
    pane: str | None = None,
) -> bool:
    """Register an ACP session into the ground layer (idempotent; fail-open).

    ``session_id`` is the **ACP** session id -- the durable Copilot session id
    that matches ``~/.copilot/session-state`` and agent-worktrees tracking.
    Returns True on a zero-exit write, else False.
    """
    if not worktree_id or not session_id:
        return False
    args = ["register-session", "--worktree-id", worktree_id, "--session-id", session_id]
    if pid is not None:
        args += ["--pid", str(pid)]
    if pane:
        args += ["--pane", pane]
    proc = _run(args)
    return bool(proc and proc.returncode == 0)


def link_succession(worktree_id: str, predecessor: str, successor: str) -> bool:
    """Record a handoff in the ground layer: predecessor ``handed-off`` +
    successor becomes the derived head, in one atomic write (fail-open)."""
    if not worktree_id or not predecessor or not successor:
        return False
    proc = _run([
        "link-succession",
        "--worktree", worktree_id,
        "--predecessor", predecessor,
        "--successor", successor,
        "--json",
    ])
    return bool(proc and proc.returncode == 0)


def note_handoff(
    worktree_id: str,
    session_id: str,
    title: str | None = None,
    task: str | None = None,
) -> bool:
    """Mirror a handoff into the worktree record's session-tagged history
    (``kind=handoff``); ``session_id`` is the predecessor. Fail-open."""
    if not worktree_id or not session_id:
        return False
    args = ["note-handoff", "--worktree-id", worktree_id, "--session-id", session_id]
    if title:
        args += ["--title", title]
    if task:
        args += ["--task", task]
    proc = _run(args)
    return bool(proc and proc.returncode == 0)


# -- reads (for seeding a successor's lineage awareness) --------------------


def session_role(worktree_id: str, session_id: str) -> dict | None:
    """Return the ground-layer role envelope for ``session_id`` (fail-open).

    ``{role, head_session, head_state, is_head, registered,
    pending_handoff_predecessor}`` or ``None`` on any failure / non-JSON.
    """
    if not worktree_id or not session_id:
        return None
    proc = _run([
        "session-role",
        "--worktree-id", worktree_id,
        "--session-id", session_id,
    ])
    if not proc or proc.returncode != 0:
        return None
    try:
        doc = json.loads(proc.stdout or "{}")
    except (ValueError, TypeError):
        return None
    return doc if isinstance(doc, dict) else None


def history_digest(worktree_id: str, session_id: str | None = None, limit: int = 8) -> str | None:
    """Return the worktree's recent session-tagged history digest (fail-open)."""
    if not worktree_id:
        return None
    args = ["history-digest", "--worktree-id", worktree_id, "--limit", str(limit)]
    if session_id:
        args += ["--session-id", session_id]
    proc = _run(args)
    if not proc or proc.returncode != 0:
        return None
    text = (proc.stdout or "").strip()
    return text or None


# -- pure seed-header composition (unit-testable without a subprocess) -------


def build_succession_seed_header(
    role: dict | None,
    digest: str | None,
    predecessor: str | None = None,
) -> str:
    """Compose a terse succession/role header to prepend to a successor's seed.

    Because the ``sessionStart`` role/digest hook cannot fire under
    ``copilot --acp``, the successor learns its lineage from its opening turn.
    ``predecessor`` is the session it succeeds (known to the bridge at handoff);
    it is passed explicitly because, once ``link-succession`` has run, the
    successor *is* the head and the role envelope no longer carries a pending
    predecessor. Returns an empty string when there is nothing worth saying
    (fail-open).
    """
    lines: list[str] = []
    role_name = (role or {}).get("role")
    pred = predecessor or (role or {}).get("pending_handoff_predecessor")
    if role_name:
        if role_name == "head":
            if pred:
                lines.append(
                    f"You are the current head of this worktree, succeeding "
                    f"session {pred}."
                )
            else:
                lines.append("You are the current head of this worktree.")
        elif role_name in ("successor-elect", "head-elect"):
            tail = f" succeeding session {pred}." if pred else "."
            lines.append(f"You are the incoming head of this worktree{tail}")
        elif role_name == "superseded":
            lines.append(
                "A different session is the active head of this worktree; "
                "assist the changeover rather than seizing the head."
            )
        else:
            lines.append(f"Your role in this worktree: {role_name}.")
    elif pred:
        lines.append(f"You are the successor to session {pred} in this worktree.")

    if digest:
        lines.append("")
        lines.append("Recent worktree history (most recent last):")
        lines.append(digest)

    if not lines:
        return ""
    return (
        "## Your place in this worktree's lineage\n\n"
        + "\n".join(lines)
        + "\n\n---\n\n"
    )
