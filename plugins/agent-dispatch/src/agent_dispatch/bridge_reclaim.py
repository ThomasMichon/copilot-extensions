"""The reclaim path's two-call equivalent of the removed ``create --reclaim``
(agent-bridge-cold-resume Phase 3).

Extracted out of ``bridge.py`` (at its 1000-line module-size cap) rather than
inlined there. ``agent-bridge create --reclaim`` no longer exists: a create
into an occupied ``worktree_id`` has no break-glass of its own now -- the
closest primitive left is ``agent-bridge resume <worktree_id>``. That resumes
(or freshly starts) the worktree's owned session and returns its session id
machine-readably, but the resumed/created session has no prompt yet, so
:func:`resume_worktree_and_send` follows up with
``send <session_id> --prompt-file - --caller <caller>`` to deliver the seed
attributed to the worker -- mirroring ``bridge.resume_worker``'s own
resume-then-send shape for a known session id, plus the same synthetic
``--caller`` the old ``create`` invocation always supplied (without it,
``send`` resolves no caller from this coordinator's neutral CWD, and
successive workers could consume or advance one another's shared delivery
cursor).

**Deliberately never passes ``--force``.** ``resume --force`` is the
single-controller invariant's break-glass: it bypasses the
``live_cli_holds_worktree`` guard outright, so calling it unconditionally
from an unattended coordinator could spawn a second ACP controller
alongside a still-live interactive CLI attached to the same checkout --
exactly the race that guard exists to prevent. Plain ``resume`` (no
``--force``) already reclaims a genuinely *stale* worktree correctly: its
own liveness check (agent-bridge-cold-resume Phase 2) verifies a
RUNNING/IDLE-looking record's actual process health and settles a dead one
before resuming through, so the common "abandoned handoff" case this path
exists for needs no override at all. A real live-CLI holder still refuses
409 ``live_cli_holds_worktree`` -- correctly, since agent-dispatch judged
only the *task* stale, never that a human's own attached session should be
torn out from under them.

**No legacy-daemon fallback.** A not-yet-upgraded agent-bridge daemon's
``resume`` predates ``--json`` support entirely, so a *successful*
(returncode 0) resume there prints the old human ``[OK] ...`` line instead
of a parseable session-id envelope -- but that resume has *already*
reused-or-created the worktree's live session either way. Falling back to
``create --reclaim`` in that case (an earlier revision of this module did)
would force-new a *second* session onto the same worktree, leaving two
controllers -- exactly the invariant this whole path exists to protect.
There is no safe way to recover the id of a session an old daemon already
created without parseable output, so this simply reports the failure
(``BridgeUnavailable``-style: the caller degrades by leaving the task
queued) rather than risk a duplicate controller. This is a real gap during
a rolling upgrade -- resolved automatically once agent-bridge is upgraded
too, since a current daemon always emits parseable ``--json`` output.

``resume`` *reuses* an existing session when one is already live for the
worktree -- it only starts a fresh one when none exists. The old
``create --reclaim`` path instead always requested a brand-new session
(``force_new=True``), ignoring any existing one outright. If the reused
session happens to be mid-turn, plain ``send`` refuses it busy (exit code
``_SEND_BUSY_EXIT`` = 75, ``agent_bridge.__main__``) rather than force
through -- ``send`` deliberately has no ``--force`` of its own (that
belongs to ``create``). Since this whole path only runs when the caller has
already judged the *task* safe to take over, a busy reuse (an ACP-owned
turn, not a rival interactive CLI) is handled the same way: ``end --force``
the busy session -- which *deletes* it, so the prompt can no longer reach
that exact id -- then **resume the worktree again** (getting its
replacement session, since the busy one is now gone) and send to that one,
once.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Sequence

from .procutil import no_window_kwargs

_SEND_BUSY_EXIT = 75


def _resume(
    worktree_id: str, *, exe: Sequence[str], timeout: float | None,
) -> tuple[subprocess.CompletedProcess, str | None]:
    """Run ``agent-bridge --json resume <worktree_id>`` once.

    Returns ``(completed_process, session_id_or_none)``. ``session_id`` is
    ``None`` on any failure to parse it out, whether from a nonzero
    returncode or a 0-returncode, unparseable (legacy-daemon) response --
    the caller distinguishes those via ``completed_process.returncode``.
    """
    resume_cmd = [*exe, "--json", "resume", worktree_id]
    resumed = subprocess.run(  # noqa: S603 -- fixed argv, exe resolved via shutil.which
        resume_cmd, check=False, capture_output=True, text=True, timeout=timeout,
        **no_window_kwargs(),
    )
    if resumed.returncode != 0:
        return resumed, None
    try:
        session_id = json.loads(resumed.stdout or "{}").get("session_id")
    except json.JSONDecodeError:
        session_id = None
    return resumed, (session_id or None)


def _send(
    session_id: str, prompt: str, *, exe: Sequence[str], caller: str, wait: bool,
    json_output: bool, timeout: float | None,
) -> subprocess.CompletedProcess:
    send_cmd = [*exe]
    if json_output:
        send_cmd.append("--json")
    send_cmd += ["send", session_id, "--prompt-file", "-", "--caller", caller]
    if not wait:
        send_cmd.append("--no-wait")
    return subprocess.run(  # noqa: S603
        send_cmd, input=prompt, check=False, capture_output=True, text=True,
        timeout=timeout, **no_window_kwargs(),
    )


def resume_worktree_and_send(
    worktree_id: str,
    prompt: str,
    *,
    exe: Sequence[str],
    agent: str,
    caller: str,
    wait: bool,
    json_output: bool,
    timeout: float | None,
) -> subprocess.CompletedProcess:
    """Resume ``worktree_id`` via agent-bridge (no ``--force``), then deliver
    ``prompt`` attributed to ``caller``.

    ``agent`` is accepted for API symmetry with ``bridge.spawn_worker`` (the
    worktree's own bound agent resolves the resumed/created session, not this
    parameter) but is otherwise unused here.

    Returns the ``resume`` call's result directly on a genuine failure --
    including a 409 ``live_cli_holds_worktree`` refusal, which this
    deliberately never overrides (see the module docstring) -- or when its
    ``session_id`` can't be parsed even on success (a not-yet-upgraded
    daemon; see the module docstring -- reported as a failure rather than
    risking a duplicate controller). Otherwise returns the ``send`` call's
    result -- reshaped to carry ``{"session_id": ...}`` on stdout when
    ``json_output`` is requested, since ``send`` itself has no reason to
    echo an id the caller already knows.
    """
    _ = agent
    resumed, session_id = _resume(worktree_id, exe=exe, timeout=timeout)
    if session_id is None:
        if resumed.returncode == 0:
            return subprocess.CompletedProcess(
                args=resumed.args, returncode=1, stdout=resumed.stdout,
                stderr=(resumed.stderr or "")
                + "\nagent-bridge resume --json returned no session_id "
                "(daemon may predate --json support here; refusing rather "
                "than risk a duplicate controller)",
            )
        return resumed

    sent = _send(
        session_id, prompt, exe=exe, caller=caller, wait=wait,
        json_output=json_output, timeout=timeout,
    )
    if sent.returncode == _SEND_BUSY_EXIT:
        # The reused session is mid-turn -- this path only runs when the
        # caller already judged the worktree safe to take over, so end the
        # busy turn and resume again for its replacement (end deletes the
        # busy session outright, so the prompt can no longer reach that
        # exact id), then send to the new one, once.
        subprocess.run(  # noqa: S603
            [*exe, "end", session_id, "--force"], check=False,
            capture_output=True, text=True, timeout=timeout, **no_window_kwargs(),
        )
        resumed, session_id = _resume(worktree_id, exe=exe, timeout=timeout)
        if session_id is None:
            if resumed.returncode == 0:
                return subprocess.CompletedProcess(
                    args=resumed.args, returncode=1, stdout=resumed.stdout,
                    stderr=(resumed.stderr or "")
                    + "\nagent-bridge resume --json returned no session_id "
                    "after ending the busy reused session",
                )
            return resumed
        sent = _send(
            session_id, prompt, exe=exe, caller=caller, wait=wait,
            json_output=json_output, timeout=timeout,
        )
    if sent.returncode != 0 or not json_output:
        return sent
    return subprocess.CompletedProcess(
        args=sent.args, returncode=0,
        stdout=json.dumps({"session_id": session_id}), stderr=sent.stderr,
    )
