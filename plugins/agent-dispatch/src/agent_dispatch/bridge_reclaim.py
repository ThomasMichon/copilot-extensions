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
performs the whole sequence: ``agent-worktrees restart <worktree_id>``
(the exact primitive behind the Picker "Stop" action and Neuron Forge's own
"Take over" -- graceful double-Ctrl-C quit, then a hard mux kill-session)
confirms the interactive CLI is actually gone, and only then does
``agent-bridge resume <worktree_id> --force`` take the worktree over.

:func:`resume_worktree_and_send` orchestrates: try a plain (non-forcing)
resume first -- the common "abandoned handoff" case needs no take-over at
all, since agent-bridge's own liveness check (agent-bridge-cold-resume
Phase 2) already settles a dead RUNNING/IDLE session before resuming
through. Only on the specific 409 ``live_cli_holds_worktree`` refusal does
this stop the interactive CLI via agent-worktrees, then retry with
``--force``. The resumed/created session has no prompt yet, so this follows
up with ``send <session_id> --prompt-file - --caller <caller>`` to deliver
the seed attributed to the worker -- mirroring ``bridge.resume_worker``'s
own resume-then-send shape for a known session id, plus the same synthetic
``--caller`` the old ``create`` invocation always supplied (without it,
``send`` resolves no caller from this coordinator's neutral CWD, and
successive workers could consume or advance one another's shared delivery
cursor).

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

from .procutil import agent_worktrees_launch_prefix, no_window_kwargs

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


def _resume_refusal_reason(resumed: subprocess.CompletedProcess) -> str | None:
    """Parse the ``reason`` out of a failed ``--json resume``'s stdout, if any."""
    try:
        detail = json.loads(resumed.stdout or "{}")
    except json.JSONDecodeError:
        return None
    return detail.get("reason") if isinstance(detail, dict) else None


def _stop_worktree_copilot(worktree_id: str, *, timeout: float | None) -> dict:
    """Kill the interactive Copilot CLI holding ``worktree_id`` via
    ``agent-worktrees restart`` (graceful double-Ctrl-C, then a hard mux
    kill-session) -- the exact primitive behind the Picker's "Stop" action
    and Neuron Forge's "Take over". Returns its JSON payload
    (``{"ok": bool, "had_session": bool, "method": ...}``), or ``{"ok":
    False, "error": ...}`` if agent-worktrees isn't available or the call
    itself failed.
    """
    exe = agent_worktrees_launch_prefix()
    if exe is None:
        return {"ok": False, "error": "agent-worktrees CLI not found on PATH"}
    cmd = [*exe, "restart", worktree_id, "--json"]
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
            or f"agent-worktrees restart exited {proc.returncode}",
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
    ``live_cli_holds_worktree`` refusal, stops that interactive CLI via
    ``agent-worktrees restart`` and retries with ``--force`` -- the complete
    "reclaim" sequence (see the module docstring). Any other failure --
    including a stop that didn't succeed, a connect/spawn error, or a
    resume whose JSON can't be parsed even on success (a not-yet-upgraded
    daemon; reported rather than risking a duplicate controller) -- is
    returned as-is. Otherwise returns the ``send`` call's result -- reshaped
    to carry ``{"session_id": ...}`` on stdout when ``json_output`` is
    requested, since ``send`` itself has no reason to echo an id the caller
    already knows.
    """
    _ = agent
    resumed, session_id = _resume(worktree_id, exe=exe, force=False, timeout=timeout)
    if session_id is None:
        if resumed.returncode == 0:
            return _no_session_id_failure(resumed)
        if _resume_refusal_reason(resumed) != _LIVE_CLI_HOLDS_WORKTREE:
            return resumed
        stopped = _stop_worktree_copilot(worktree_id, timeout=timeout)
        if not stopped.get("ok"):
            return subprocess.CompletedProcess(
                args=resumed.args, returncode=1, stdout=resumed.stdout,
                stderr=(resumed.stderr or "")
                + f"\ncould not stop the interactive CLI holding {worktree_id}: "
                f"{stopped.get('error', stopped)}",
            )
        resumed, session_id = _resume(worktree_id, exe=exe, force=True, timeout=timeout)
        if session_id is None:
            if resumed.returncode == 0:
                return _no_session_id_failure(
                    resumed, extra=" after stopping the interactive CLI holder"
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
