"""``copilot``: deliver a TTY Copilot session in this terminal, or a
brand-new visible window (``--headed``) -- the human/TTY-facing counterpart
of :func:`handoff_cli.cmd_embody`.

A standalone module (rather than living in ``handoff_cli.py``, where the
sibling ``cmd_embody`` it wraps is defined) purely to keep both modules
under this repo's flat 1000-line module-size cap -- ``handoff_cli.py`` was
already close to it. No functional reason to split otherwise.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from . import handoff_cli, headed_launch, output, sessions


def add_copilot_parser(sub) -> None:
    # copilot (the human/TTY-facing counterpart of embody -- deliver a TTY
    # Copilot session in THIS terminal, the canonical "___ copilot" verb also
    # implemented by agent-codespaces/agent-containers for a remote venue)
    p = sub.add_parser(
        "copilot",
        help="Deliver a TTY Copilot session to the user in this terminal "
        "(create-or-resume like embody, then attach this terminal to it; "
        "refuses without a controlling terminal)",
    )
    g = p.add_mutually_exclusive_group()
    g.add_argument(
        "--worktree-id", dest="worktree_id", default=None,
        help="Deliver a Copilot session for this existing worktree",
    )
    g.add_argument(
        "--new", action="store_true", help="Create a fresh worktree first, then deliver Copilot in it"
    )
    g.add_argument(
        "--codename", default=None,
        help="Same codename resolution as `embody --codename` (local first, "
        "then a cross-machine SSH scan; fails closed on a different machine).",
    )
    g.add_argument(
        "--anchor", action="store_true",
        help="Deliver a Copilot session directly in the active project's "
        "anchor checkout instead of any worktree -- same as "
        "`embody --anchor`, see its help for the full rationale.",
    )
    p.add_argument(
        "--seed", default=None,
        help="Seed prompt injected as the session's first interactive turn once Copilot is ready",
    )
    p.add_argument(
        "--seed-ready-timeout", dest="seed_ready_timeout", type=float, default=180.0,
        metavar="SECONDS",
        help="How long to wait for Copilot's input prompt before typing --seed (default 180)",
    )
    p.add_argument(
        "--driver", default=None,
        help="Label of the agent steering this session; stamps the "
        "'driven by <agent>' banner (AGENT_BRIDGE_DRIVEN_BY)",
    )
    p.add_argument(
        "--recovery", action="store_true", help="Use the repo's recovery launch command"
    )
    p.add_argument(
        "--ensure-mux", dest="ensure_mux", action="store_true",
        help="Best-effort self-heal a missing tmux/psmux before creating the "
        "session (same explicit opt-in as `embody --ensure-mux`).",
    )
    p.add_argument(
        "--mux", default=None,
        help="Override the mux binary used to attach (default: auto-detect "
        "tmux/psmux, same resolution as the rest of agent-worktrees)",
    )
    p.add_argument(
        "--headed", action="store_true",
        help="Attach in a brand-new, VISIBLE terminal window instead of "
        "THIS one -- for a caller (e.g. the Worktree Manager Picker) that "
        "wants to keep running while a separate window shows the session. "
        "Does not require a controlling terminal (the --headed window is "
        "its own controlling terminal, not this process's).",
    )
    p.add_argument(
        "--json", action="store_true",
        help="With --headed only: emit a JSON result envelope instead of a "
        "human status line (stdout is JSON only). Meaningless without "
        "--headed -- the default exec path never returns to print one.",
    )
    handoff_cli.add_launch_passthrough_args(p)


def cmd_copilot(args: argparse.Namespace) -> int:
    """Deliver a TTY Copilot session in THIS terminal, or with ``--headed``
    a brand-new visible window instead.

    Ensures a durable, mux-wrapped Copilot session exists (identical
    create-or-resume semantics to `embody`), then hands a terminal to it.
    By default that's THIS process's own controlling terminal, replaced via
    exec so no wrapper is left holding the TTY; refuses without one.
    ``--headed`` opens a separate window instead and returns without
    touching this process's stdio, for a caller that must keep running
    (e.g. the Picker's "Launch in new window"); needs no controlling
    terminal of its own. ``--json`` (only meaningful with ``--headed``,
    since the exec path never returns) emits a result envelope instead of a
    human status line. `agent-codespaces`/`agent-containers` implement the
    same verb remotely via SSH `-t`; `--headed` is local-only.
    """
    headed = getattr(args, "headed", False)
    json_mode = getattr(args, "json", False)
    if json_mode and not headed:
        output.err("copilot --json is only meaningful with --headed")
        return 2
    if not headed and not sys.stdin.isatty():
        output.err(
            "`copilot` needs a controlling terminal to attach to -- for a "
            "programmatic/detached launch use `embody` instead, or pass "
            "--headed to open a new window."
        )
        return 2

    # Reuse embody's create-or-resume logic in-process; only its JSON result
    # matters, not its stdout. `_json_output` writes to `sys.__stdout__`,
    # which a plain `contextlib.redirect_stdout` never captures (confirmed
    # live, agent-bridge-cli-mode-sessions Phase 4); `capture_json_output()`
    # swaps `sys.__stdout__` itself.
    with output.capture_json_output() as buf:
        rc = handoff_cli.cmd_embody(args)
    if rc != 0:
        # embody already wrote its JSON error to buf; surface it and exit.
        if json_mode:
            output._json_output({"ok": False, "error": _error_from_buf(buf)})
        else:
            sys.stderr.write(buf.getvalue())
        return rc
    try:
        result = json.loads(buf.getvalue())
    except (ValueError, TypeError):
        return _copilot_fail(json_mode, "could not parse the embodiment result")

    session_name = result.get("session")
    if not session_name:
        return _copilot_fail(json_mode, "embodiment result had no session name")

    mux_bin = sessions._mux_bin(getattr(args, "mux", None))
    if headed:
        try:
            spawned = headed_launch.spawn_headed_attach(
                mux_bin, session_name, title=result.get("worktree_id"))
        except headed_launch.HeadedLaunchError as exc:
            return _copilot_fail(json_mode, str(exc), prefix="copilot --headed: ")
        if json_mode:
            output._json_output({"ok": True, "session": session_name, **spawned})
        else:
            output.info(f"Opened {session_name!r} in a new window "
                        f"(spawner: {spawned['spawner']}, pid: {spawned['pid']})")
        return 0
    argv = [mux_bin, "attach-session", "-t", session_name]
    try:
        os.execvp(mux_bin, argv)  # never returns on success
    except OSError as exc:
        output.err(f"copilot: could not attach to {session_name!r}: {exc}")
        return 1
    return 0  # pragma: no cover -- unreachable after a successful execvp


def _error_from_buf(buf) -> str:
    try:
        return json.loads(buf.getvalue()).get("error") or "embody failed"
    except (ValueError, TypeError):
        return "embody failed"


def _copilot_fail(json_mode: bool, message: str, *, prefix: str = "copilot: ") -> int:
    if json_mode:
        output._json_output({"ok": False, "error": message})
    else:
        output.err(f"{prefix}{message}")
    return 1
