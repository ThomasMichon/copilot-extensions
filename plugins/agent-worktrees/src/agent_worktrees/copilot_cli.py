"""``copilot``: deliver a TTY Copilot session in this terminal -- the
human/TTY-facing counterpart of :func:`handoff_cli.cmd_embody`.

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

from . import handoff_cli, output, sessions


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
    handoff_cli.add_launch_passthrough_args(p)


def cmd_copilot(args: argparse.Namespace) -> int:
    """Deliver a TTY Copilot session in THIS terminal.

    Ensures a durable, mux-wrapped Copilot session exists (identical
    create-or-resume semantics to `embody`), then hands THIS process's own
    controlling terminal to it, replaced via exec so no wrapper is left
    holding the TTY; refuses without one. For a caller that must keep
    running while a session becomes visible elsewhere (e.g. the Worktree
    Manager Picker's "Launch in new window"), use the Manager's own
    launch-plan ``new_window`` modifier instead (Phase 9, #5210) -- opening
    a visible terminal window without blocking/replacing the caller's own
    process is a presentation concern Worktree Manager owns, not this CLI
    (see the `session-hosting` vision). `agent-codespaces`/`agent-containers`
    implement the same verb remotely via SSH `-t`.
    """
    if not sys.stdin.isatty():
        output.err(
            "`copilot` needs a controlling terminal to attach to -- for a "
            "programmatic/detached launch use `embody` instead."
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
        sys.stderr.write(buf.getvalue())
        return rc
    try:
        result = json.loads(buf.getvalue())
    except (ValueError, TypeError):
        output.err("copilot: could not parse the embodiment result")
        return 1

    session_name = result.get("session")
    if not session_name:
        output.err("copilot: embodiment result had no session name")
        return 1

    mux_bin = sessions._mux_bin(getattr(args, "mux", None))
    argv = [mux_bin, "attach-session", "-t", session_name]
    try:
        os.execvp(mux_bin, argv)  # never returns on success
    except OSError as exc:
        output.err(f"copilot: could not attach to {session_name!r}: {exc}")
        return 1
    return 0  # pragma: no cover -- unreachable after a successful execvp
