"""Which conversation ``embody`` resumes when it brings a worktree back."""

from __future__ import annotations

import argparse

from . import sessions
from .session_liveness import session_liveness


def resume_target(
    args: argparse.Namespace, record, seed: str | None, *, make_new: bool,
) -> str | None:
    """The conversation embody resumes, or None for a fresh start.

    Bringing an existing worktree back (no new task) resumes its head
    (``sessions.resolve_resume_target``) so its history isn't lost; a seed is
    a new task and starts fresh unless ``--resume-head``. ``--fresh``, a new
    worktree, or an explicit ``--copilot-arg=--resume=...`` never adds one.
    """
    if make_new or record is None or getattr(args, "fresh", False):
        return None
    if seed and not getattr(args, "resume_head", False):
        return None
    for arg in getattr(args, "copilot_args", None) or []:
        if str(arg).split("=", 1)[0] in ("--resume", "--continue"):
            return None
    return sessions.resolve_resume_target(record)


def with_resume(launch_cmd, target: str | None) -> list[str]:
    """``launch_cmd`` resuming ``target`` (unchanged when there is none)."""
    return [*launch_cmd, f"--resume={target}"] if target else list(launch_cmd)


def with_seed(launch_cmd, seed: str | None) -> list[str]:
    """``launch_cmd`` with ``seed`` delivered as its next interactive turn
    (unchanged when there is none).

    Appends the **full** ``--interactive`` flag -- never the short ``-i``,
    which PowerShell's own argument parser can intercept before it ever
    reaches the exec'd ``copilot`` process on Windows. This makes the seed a
    durable, explicit argument on the launched Copilot command line itself,
    never an ambient side-channel (a worktree-record field typed in later
    via mux send-keys): it works identically whether the launch is muxed or
    ``--no-mux`` (there is no pane to target either way), and composes with
    a separately-appended ``--resume={target}`` to resume history AND
    execute the new prompt in the SAME process (verified live: `copilot
    --resume=<id> --interactive "<prompt>"` resumes full history and
    auto-executes the prompt as the next turn).
    """
    return [*launch_cmd, "--interactive", seed] if seed else list(launch_cmd)


def live_head_refusal(target: str | None, worktree_id: str) -> str | None:
    """Why ``target`` must not be resumed in a new process: its Copilot is
    still running (outside this worktree's mux), or that can't be ruled out
    here -- a second process on one conversation would fork it."""
    if not target:
        return None
    state = session_liveness(target)
    if state == "dead":
        return None
    if state == "live":
        return (f"head session {target} is already running outside wt-{worktree_id}; "
                "attach to it there, or pass --fresh")
    return (f"can't confirm head session {target} has stopped (no process probe on this platform, "
            f"or its lock directory is unreadable); once it has, pass --copilot-arg=--resume={target}, "
            "or --fresh")
