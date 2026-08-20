"""Derive a worktree's asserted *head session* from the agent-worktrees ground
layer (agent-fabric `single-current-session-per-worktree`).

agent-worktrees **owns** the durable session-lifecycle state: a per-worktree
head pointer plus a per-session lifecycle (active / handed-off / concluded).
agent-bridge does not keep a rival copy of that state (the vision's
``derive-dont-duplicate``); it *reads* it here, on demand, by shelling to the
``agent-worktrees head-session`` CLI -- the same "run the binstub in its own
interpreter" pattern the agent registry uses to read the repos registry
(a separate venv, so the child env is scrubbed of our virtual-env markers).

The read is deliberately **fail-open**: if the binstub is missing, the worktree
is untracked, the call times out, or the JSON is unparseable, this returns a
``HeadInfo`` with ``active=False`` so the create guard *permits* the session
(never refuse a create because the ground layer could not be consulted).
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from dataclasses import dataclass

from agent_procutil import no_window_flags

from .agent_registry import _agent_worktrees_bin

log = logging.getLogger("agent_bridge.worktree_head")

# The head query is a cheap local YAML read; keep the subprocess budget small so
# a wedged binstub can never stall a session create for long (fail-open on
# timeout).
_HEAD_QUERY_TIMEOUT_S = 10.0


@dataclass(frozen=True)
class HeadInfo:
    """A worktree's derived head, as read from the ground layer.

    - ``active`` -- the worktree has a current, un-concluded head session that a
      fresh create would run in parallel with. This is the guard signal.
    - ``head_session`` -- that session's id (a Copilot session-state GUID), or
      None when there is no active head.
    - ``state`` -- the head's lifecycle state (``active`` normally), or None.
    - ``tracked`` -- whether a ground-layer record was found at all; False means
      "untracked / unknown worktree", which is a fail-open (no guard) signal.
    """

    active: bool
    head_session: str | None = None
    state: str | None = None
    tracked: bool = False


_UNKNOWN = HeadInfo(active=False, head_session=None, state=None, tracked=False)


def resolve_head(worktree_id: str) -> HeadInfo:
    """Return the ground-layer head for ``worktree_id`` (fail-open).

    Shells to ``agent-worktrees head-session --worktree-id <id> --json`` and
    parses its envelope. Any failure (no binstub, non-zero exit, timeout,
    non-JSON, unexpected shape) degrades to :data:`_UNKNOWN` -- an inactive,
    untracked head -- so a create is never *blocked* by an inability to read the
    ground layer.
    """
    if not worktree_id:
        return _UNKNOWN
    exe = _agent_worktrees_bin()
    if not exe:
        log.debug("agent-worktrees binstub not found -- head guard fails open")
        return _UNKNOWN

    creationflags = no_window_flags()
    # Run the binstub in its own interpreter context: scrub our venv markers so
    # a uv-managed child Python does not trip an `_sre` module mismatch (the same
    # guard agent_registry.load_local_repos uses).
    child_env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("VIRTUAL_ENV", "PYTHONHOME", "__PYVENV_LAUNCHER__", "PYTHONPATH")
    }
    try:
        proc = subprocess.run(  # noqa: S603 -- fixed argv, exe via shutil.which
            [exe, "head-session", "--worktree-id", worktree_id, "--json"],
            capture_output=True,
            text=True,
            timeout=_HEAD_QUERY_TIMEOUT_S,
            check=False,
            creationflags=creationflags,
            env=child_env,
        )
    except Exception as exc:
        log.warning("agent-worktrees head-session failed for %s: %s", worktree_id, exc)
        return _UNKNOWN
    if proc.returncode != 0:
        log.debug(
            "agent-worktrees head-session exited %s for %s", proc.returncode, worktree_id
        )
        return _UNKNOWN
    return parse_head_payload(proc.stdout)


def parse_head_payload(stdout: str | None) -> HeadInfo:
    """Parse a ``head-session`` JSON envelope into a :class:`HeadInfo`.

    Split out from the subprocess call so the mapping is unit-testable without
    spawning the binstub. Any malformed / unexpected payload fails open.
    """
    try:
        doc = json.loads(stdout or "{}")
    except (ValueError, TypeError):
        log.debug("agent-worktrees head-session emitted non-JSON")
        return _UNKNOWN
    if not isinstance(doc, dict):
        return _UNKNOWN
    head = doc.get("head_session")
    return HeadInfo(
        active=bool(doc.get("active")),
        head_session=head if isinstance(head, str) else None,
        state=doc.get("state") if isinstance(doc.get("state"), str) else None,
        tracked=bool(doc.get("tracked")),
    )
