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
the busy session, then retry ``send`` once.

**Independent-plugin version skew.** agent-dispatch and agent-bridge are
separately deployable (``bridge.py``'s own module docstring). A daemon not
yet upgraded past agent-bridge-cold-resume Phase 3 still understands the
legacy ``create --reclaim`` bypass but never emits ``resume``'s new JSON
session-id envelope -- ``--json resume`` there just prints the old human
``[OK] ...`` line, so this can't parse a ``session_id`` out of it even
though the resume itself *succeeded* (``returncode == 0``). Genuine failure
on a current daemon returns a JSON error object instead, still with
``returncode != 0``, and is returned to the caller unchanged well before
this ambiguity -- a *0-returncode, unparseable-JSON* combination is
therefore a reliable signal of exactly this daemon-version gap, not a new
kind of failure, so :func:`resume_worktree_and_send` falls back to the
legacy ``create ... --reclaim`` invocation in that one case.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Sequence

from .procutil import no_window_kwargs

_SEND_BUSY_EXIT = 75


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

    Returns the ``resume`` call's result directly on a genuine failure --
    including a 409 ``live_cli_holds_worktree`` refusal, which this
    deliberately never overrides (see the module docstring) -- or a
    connect/spawn error; a *successful* resume whose JSON can't be parsed
    instead falls back to the legacy ``create ... --reclaim`` invocation
    (an old, not-yet-upgraded daemon -- see the module docstring). Otherwise
    returns the ``send`` call's result -- reshaped to carry
    ``{"session_id": ...}`` on stdout when ``json_output`` is requested, since
    ``send`` itself has no reason to echo an id the caller already knows.
    """
    resume_cmd = [*exe, "--json", "resume", worktree_id]
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
        return _legacy_create_reclaim(
            worktree_id, prompt, exe=exe, agent=agent, caller=caller,
            wait=wait, json_output=json_output, timeout=timeout,
        )
    send_cmd = [*exe]
    if json_output:
        send_cmd.append("--json")
    send_cmd += ["send", session_id, "--prompt-file", "-", "--caller", caller]
    if not wait:
        send_cmd.append("--no-wait")
    sent = subprocess.run(  # noqa: S603
        send_cmd, input=prompt, check=False, capture_output=True, text=True,
        timeout=timeout, **no_window_kwargs(),
    )
    if sent.returncode == _SEND_BUSY_EXIT:
        # The reused session is mid-turn -- this path only runs when the
        # caller already judged the worktree safe to take over, so end the
        # busy turn (mirrors the old create --reclaim's force_new=True, which
        # never deferred to an existing session at all) and retry once.
        subprocess.run(  # noqa: S603
            [*exe, "end", session_id, "--force"], check=False,
            capture_output=True, text=True, timeout=timeout, **no_window_kwargs(),
        )
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


def _legacy_create_reclaim(
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
    """Rolling-upgrade fallback: the pre-Phase-3 ``create --worktree-id
    --reclaim`` invocation, for a daemon that hasn't upgraded past
    agent-bridge-cold-resume Phase 3 yet (see the module docstring)."""
    cmd = [*exe]
    if json_output:
        cmd.append("--json")
    cmd += ["create", "--worktree-id", worktree_id, "--reclaim", agent, prompt]
    cmd += ["--caller", caller]
    if not wait:
        cmd.append("--no-wait")
    return subprocess.run(  # noqa: S603
        cmd, check=False, capture_output=True, text=True, timeout=timeout,
        **no_window_kwargs(),
    )

