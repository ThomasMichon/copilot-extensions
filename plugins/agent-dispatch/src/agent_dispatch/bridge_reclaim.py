"""The reclaim path's replacement for the removed ``create --reclaim``
(agent-bridge-cold-resume Phase 3).

Extracted out of ``bridge.py`` (at its 1000-line module-size cap) rather than
inlined there. ``agent-bridge create --reclaim`` no longer exists: a create
into an occupied ``worktree_id`` has no break-glass of its own now.

**What "reclaim" actually means.** It is not merely "bypass the ownership
guard" -- the guard exists specifically to stop a second ACP controller
spawning alongside a still-live interactive Copilot CLI attached to the same
checkout. "Reclaim" is the verb for *deliberately taking that worktree away
from that other process*: kill the live interactive CLI holding it, THEN
create/resume the owned session in its place. agent-bridge's own CLI already
says as much (the 409 refusal's own text: "Stop that CLI first, then re-run
with --force to take it over") -- ``--force``/``reclaim=true`` was always
meant to be the *second* half of that two-step sequence, never a
substitute for the first. An earlier revision of this module only ever
performed the second half (bypassing the guard without actually stopping
anything, or later, never bypassing it at all) -- both wrong. This module
performs the whole sequence: ``agent-bridge restart-worktree <worktree_id>``
(the reclaim sequence's stop half -- shells to ``agent-worktrees restart``,
the exact primitive behind the Picker "Stop" action and Neuron Forge's own
"Take over": graceful double-Ctrl-C quit, then a hard mux kill-session --
and *additionally* expires the worktree's live-session registration
server-side on success, #2906; calling ``agent-worktrees restart`` directly
would skip that invalidation) confirms the interactive CLI is actually
gone, and only then does ``agent-bridge resume <worktree_id> --force`` take
the worktree over.

**Race between the stop and the forced resume.** ``restart`` and the
following ``resume --force`` are two separate calls; a *different* live
interactive CLI could attach to the worktree in the gap between them, and
``--force`` bypasses the ownership guard unconditionally. Forcing straight
through in that window would recreate the exact duplicate-controller race
the guard exists to prevent -- forcing past a holder we never actually
confirmed dead. Two layers close this:

1. ``restart-worktree`` is called with ``--expected-holder`` set to the
   *original* refusal's holder session id, so the server-side
   invalidate-on-take-over (#2906) is fenced to only that registration -- a
   genuinely different claimant that registers for this worktree while the
   stop is in flight is never collaterally demoted.
2. After ``restart`` succeeds, this repeats the *plain* (non-forcing)
   resume check once more before ever forcing: if that succeeds outright,
   no force was even needed (best case). If it is refused again for the
   same reason, this compares the refusal's holder session id against the
   original one -- identical means the guard is still keyed on the (now
   confirmed-dead) process we just stopped, so forcing through it is safe;
   a *different* id means a fresh claimant won the worktree in the race
   window (and, thanks to the fencing above, that claimant's own
   registration was never touched), and this refuses rather than force
   through a live process it never confirmed dead.

:func:`resume_worktree_and_send` orchestrates the whole sequence, then
delivers ``prompt`` via ``send <session_id> --prompt-file - --caller
<caller>`` -- mirroring ``bridge.resume_worker``'s own resume-then-send
shape for a known session id, plus the same synthetic ``--caller`` the old
``create`` invocation always supplied (without it, ``send`` resolves no
caller from this coordinator's neutral CWD, and successive workers could
consume or advance one another's shared delivery cursor).

**No legacy-daemon fallback.** A not-yet-upgraded agent-bridge daemon's
``resume`` predates ``--json`` support entirely, so a *successful*
(returncode 0) resume there prints the old human ``[OK] ...`` line instead
of a parseable session-id envelope. There is no safe way to recover the id
of a session an old daemon already resumed/created without parseable
output, so this reports the failure (the caller degrades by leaving the
task queued) rather than risk a duplicate controller by guessing. This is a
real gap during a rolling upgrade -- resolved automatically once
agent-bridge is upgraded too.

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
replacement, since the busy one is now gone) and send to that one, once.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Sequence

from .procutil import no_window_kwargs

_SEND_BUSY_EXIT = 75
_LIVE_CLI_HOLDS_WORKTREE = "live_cli_holds_worktree"


def _resume(
    worktree_id: str, *, exe: Sequence[str], force: bool, timeout: float | None,
) -> tuple[subprocess.CompletedProcess, str | None]:
    """Run ``agent-bridge --json resume <worktree_id> [--force]`` once.

    Returns ``(completed_process, session_id_or_none)``. ``session_id`` is
    ``None`` on any failure to parse it out, whether from a nonzero
    returncode or a 0-returncode, unparseable (legacy-daemon) response --
    the caller distinguishes those via ``completed_process.returncode``.
    """
    resume_cmd = [*exe, "--json", "resume", worktree_id]
    if force:
        resume_cmd.append("--force")
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


def _resume_refusal(resumed: subprocess.CompletedProcess) -> tuple[str | None, str | None]:
    """Parse ``(reason, holder_session_id)`` out of a failed ``--json
    resume``'s stdout, or ``(None, None)`` if it isn't a structured refusal.
    """
    try:
        detail = json.loads(resumed.stdout or "{}")
    except json.JSONDecodeError:
        return None, None
    if not isinstance(detail, dict):
        return None, None
    return detail.get("reason"), detail.get("session_id")


def _stop_worktree_copilot(
    worktree_id: str, *, exe: Sequence[str], expected_holder: str | None,
    timeout: float | None,
) -> dict:
    """Kill the interactive Copilot CLI holding ``worktree_id`` via
    ``agent-bridge restart-worktree`` -- the reclaim sequence's stop half
    (agent-bridge-cold-resume Phase 3, #6744). That verb shells to
    ``agent-worktrees restart`` (graceful double-Ctrl-C, then a hard mux
    kill-session -- the same primitive behind the Picker's "Stop" action
    and Neuron Forge's "Take over") *and* expires the worktree's
    live-session registration server-side on success (#2906) -- calling
    ``agent-worktrees restart`` directly skips that invalidation entirely,
    which is exactly what the caller's revalidation-before-forcing step
    below exists to detect and refuse rather than silently miss.

    ``expected_holder`` (the original refusal's holder session id) is
    passed through as ``--expected-holder`` so the server-side invalidation
    is fenced to only that registration -- a genuinely different claimant
    that registers for this worktree while this call is in flight is left
    untouched rather than collaterally demoted (#2906 race hardening).

    Returns the JSON payload (``{"ok": bool, "had_session": bool, "method":
    ...}``), or ``{"ok": False, "error": ...}`` on any failure to
    parse/run it.
    """
    cmd = [*exe, "restart-worktree", worktree_id, "--json"]
    if expected_holder:
        cmd += ["--expected-holder", expected_holder]
    proc = subprocess.run(  # noqa: S603
        cmd, check=False, capture_output=True, text=True, timeout=timeout,
        **no_window_kwargs(),
    )
    try:
        payload = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        payload = {}
    if not isinstance(payload, dict) or "ok" not in payload:
        return {
            "ok": False,
            "error": (proc.stderr or proc.stdout or "").strip()
            or f"agent-bridge restart-worktree exited {proc.returncode}",
        }
    return payload


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


def _no_session_id_failure(
    resumed: subprocess.CompletedProcess, *, extra: str = "",
) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=resumed.args, returncode=1, stdout=resumed.stdout,
        stderr=(resumed.stderr or "")
        + "\nagent-bridge resume --json returned no session_id" + extra,
    )


def _take_over_live_holder(
    worktree_id: str, *, exe: Sequence[str], original_holder: str | None,
    timeout: float | None,
) -> tuple[subprocess.CompletedProcess | None, str | None]:
    """Stop the interactive CLI holding ``worktree_id``, revalidate it's
    actually gone, and only then force-resume. Returns ``(failure_or_None,
    session_id_or_None)`` -- exactly one is non-``None``.
    """
    stopped = _stop_worktree_copilot(
        worktree_id, exe=exe, expected_holder=original_holder, timeout=timeout
    )
    if not stopped.get("ok"):
        return subprocess.CompletedProcess(
            args=[], returncode=1, stdout="",
            stderr=f"could not stop the interactive CLI holding {worktree_id}: "
            f"{stopped.get('error', stopped)}",
        ), None
    if not stopped.get("had_session"):
        # ok=true/had_session=false is a no-op (no MUX session existed to
        # stop) -- never proof that whatever registered live_cli_holds_worktree
        # was actually terminated: the holder could be a bare, un-muxed CLI
        # (invisible to 'agent-worktrees restart', which only ever sees mux
        # sessions). Forcing past it here would be forcing past a process
        # never confirmed dead. Refuse; 'agent-worktrees reclaim
        # --worktree-id ... --yes' is the primitive for a bare orphan.
        return subprocess.CompletedProcess(
            args=[], returncode=1, stdout="",
            stderr=f"{worktree_id}: restart reported no mux session to stop, "
            f"but a live interactive CLI ({original_holder}) still holds it "
            "-- likely a bare/un-muxed Copilot; refusing to force through a "
            "process never confirmed dead (see 'agent-worktrees reclaim')",
        ), None

    # Revalidate before forcing (see the module docstring): a different live
    # CLI could have attached in the gap between 'restart' returning and
    # here. Repeat the plain check once more.
    revalidated, session_id = _resume(worktree_id, exe=exe, force=False, timeout=timeout)
    if session_id is not None:
        return None, session_id  # no force needed at all -- best case
    if revalidated.returncode == 0:
        return _no_session_id_failure(
            revalidated, extra=" after stopping the interactive CLI holder"
        ), None
    reason, holder = _resume_refusal(revalidated)
    if reason != _LIVE_CLI_HOLDS_WORKTREE:
        return revalidated, None
    if holder != original_holder:
        return subprocess.CompletedProcess(
            args=revalidated.args, returncode=1, stdout=revalidated.stdout,
            stderr=(revalidated.stderr or "")
            + f"\na different interactive CLI ({holder}) claimed {worktree_id} "
            f"while stopping the original holder ({original_holder}) -- "
            "refusing to force through a live process never confirmed dead",
        ), None

    forced, session_id = _resume(worktree_id, exe=exe, force=True, timeout=timeout)
    if session_id is None:
        if forced.returncode == 0:
            return _no_session_id_failure(
                forced, extra=" after stopping and revalidating the interactive CLI holder"
            ), None
        return forced, None
    return None, session_id


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
    """Take over ``worktree_id`` (killing a live interactive CLI holder first
    if one exists) and deliver ``prompt`` attributed to ``caller``.

    ``agent`` is accepted for API symmetry with ``bridge.spawn_worker`` (the
    worktree's own bound agent resolves the resumed/created session, not this
    parameter) but is otherwise unused here.

    Tries a plain (non-forcing) resume first. On a genuine 409
    ``live_cli_holds_worktree`` refusal, delegates the whole
    stop-then-revalidate-then-force sequence to
    :func:`_take_over_live_holder` (see the module docstring). Any other
    failure -- a connect/spawn error, a failed stop, a revalidation naming a
    different holder, or a resume whose JSON can't be parsed even on success
    (a not-yet-upgraded daemon) -- is returned as-is. Otherwise returns the
    ``send`` call's result -- reshaped to carry ``{"session_id": ...}`` on
    stdout when ``json_output`` is requested, since ``send`` itself has no
    reason to echo an id the caller already knows.
    """
    _ = agent
    resumed, session_id = _resume(worktree_id, exe=exe, force=False, timeout=timeout)
    if session_id is None:
        if resumed.returncode == 0:
            return _no_session_id_failure(resumed)
        reason, holder = _resume_refusal(resumed)
        if reason != _LIVE_CLI_HOLDS_WORKTREE:
            return resumed
        failure, session_id = _take_over_live_holder(
            worktree_id, exe=exe, original_holder=holder, timeout=timeout,
        )
        if failure is not None:
            return failure

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
        ended = subprocess.run(  # noqa: S603
            [*exe, "end", session_id, "--force"], check=False,
            capture_output=True, text=True, timeout=timeout, **no_window_kwargs(),
        )
        if ended.returncode != 0:
            # Deletion failed -- the busy session is still live. Resuming
            # again here would just reuse and re-send to the exact session
            # this take-over was supposed to replace, so report the
            # failure instead of silently proceeding as if it were gone.
            return subprocess.CompletedProcess(
                args=ended.args, returncode=1, stdout=ended.stdout,
                stderr=(ended.stderr or "")
                + f"\ncould not end the busy session {session_id} before "
                "re-resuming -- refusing to reuse it",
            )
        resumed, session_id = _resume(worktree_id, exe=exe, force=False, timeout=timeout)
        if session_id is None:
            if resumed.returncode == 0:
                return _no_session_id_failure(
                    resumed, extra=" after ending the busy reused session"
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
