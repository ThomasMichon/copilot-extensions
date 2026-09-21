"""The reclaim path's two-call equivalent of the removed ``create --reclaim``
(agent-bridge-cold-resume Phase 3).

Extracted out of ``bridge.py`` (at its 1000-line module-size cap) rather than
inlined there. ``agent-bridge create --reclaim`` no longer exists: a create
into an occupied ``worktree_id`` has no break-glass of its own now -- the
sole take-over primitive is ``agent-bridge resume <worktree_id> --force``.
That force-resumes (or freshly starts) the worktree's owned session and
returns its session id machine-readably, but the resumed/created session has
no prompt yet, so :func:`resume_worktree_and_send` follows up with
``send <session_id> --prompt-file -`` to deliver the seed -- mirroring
``bridge.resume_worker``'s own resume-then-send shape for a known session id.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Sequence

from .procutil import no_window_kwargs


def resume_worktree_and_send(
    worktree_id: str,
    prompt: str,
    *,
    exe: Sequence[str],
    wait: bool,
    json_output: bool,
    timeout: float | None,
) -> subprocess.CompletedProcess:
    """Force-resume ``worktree_id`` via agent-bridge, then deliver ``prompt``.

    Returns the ``resume`` call's result directly on its own failure (a
    connect/spawn error, or a malformed/absent ``session_id`` in its JSON);
    otherwise returns the ``send`` call's result -- reshaped to carry
    ``{"session_id": ...}`` on stdout when ``json_output`` is requested, since
    ``send`` itself has no reason to echo an id the caller already knows.
    """
    resume_cmd = [*exe, "--json", "resume", worktree_id, "--force"]
    resumed = subprocess.run(  # noqa: S603 -- fixed argv, exe resolved via shutil.which
        resume_cmd, check=False, capture_output=True, text=True, timeout=timeout,
        **no_window_kwargs(),
    )
    if resumed.returncode != 0:
        return resumed
    try:
        session_id = json.loads(resumed.stdout or "{}").get("session_id")
    except json.JSONDecodeError:
        session_id = None
    if not session_id:
        return subprocess.CompletedProcess(
            args=resume_cmd, returncode=1, stdout=resumed.stdout,
            stderr=(resumed.stderr or "")
            + "\nagent-bridge resume --json returned no session_id",
        )
    send_cmd = [*exe]
    if json_output:
        send_cmd.append("--json")
    send_cmd += ["send", session_id, "--prompt-file", "-"]
    if not wait:
        send_cmd.append("--no-wait")
    sent = subprocess.run(  # noqa: S603
        send_cmd, input=prompt, check=False, capture_output=True, text=True,
        timeout=timeout, **no_window_kwargs(),
    )
    if sent.returncode != 0 or not json_output:
        return sent
    return subprocess.CompletedProcess(
        args=send_cmd, returncode=0,
        stdout=json.dumps({"session_id": session_id}), stderr=sent.stderr,
    )
