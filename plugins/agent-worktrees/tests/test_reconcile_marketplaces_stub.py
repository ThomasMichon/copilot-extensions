"""``reconcile-marketplaces`` is a retired command (#2722): local-checkout
marketplace source overrides no longer exist. It is kept registered as a
no-op compatibility shim purely to bridge the upgrade window -- a caller
still running pre-#2722 script content (an in-flight launch, or a stale
deployed ``marketplace-overrides.ps1``/``.sh``) must not hit the
"Unknown subcommand" hard failure. These tests pin the exact flag
combinations those legacy callers use.
"""

from __future__ import annotations

import json

from agent_worktrees import __main__ as m


def _run(argv: list[str], stdin_text: str = "") -> tuple[int, str]:
    parser = m.build_parser()
    args = parser.parse_args(argv)
    import io
    import sys

    old_stdin = sys.stdin
    sys.stdin = io.StringIO(stdin_text)
    try:
        buf = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = buf
        try:
            code = args.func(args) if hasattr(args, "func") else m.cmd_reconcile_marketplaces(args)
        finally:
            sys.stdout = old_stdout
    finally:
        sys.stdin = old_stdin
    return code, buf.getvalue()


def test_reconcile_marketplaces_registered_as_noop_command():
    assert m.COMMAND_MAP["reconcile-marketplaces"] is m.cmd_reconcile_marketplaces
    assert "reconcile-marketplaces" in m._NO_PROJECT_COMMANDS


def test_reconcile_marketplaces_launch_session_style_succeeds():
    # matches the removed launch-session.ps1/.sh invocation
    code, out = _run(["reconcile-marketplaces", "--cwd", ".", "--ensure-ignored", "--json"])
    assert code == 0
    payload = json.loads(out)
    assert payload["action"] == "no-op"
    assert payload["changed"] is False


def test_reconcile_marketplaces_session_start_style_succeeds():
    # matches the stale deployed marketplace-overrides.ps1/.sh sessionStart call
    code, out = _run(["reconcile-marketplaces", "--stdin", "--session-start"], stdin_text="{}")
    assert code == 0
    assert out.strip() == "{}"


def test_reconcile_marketplaces_bare_invocation_succeeds():
    code, _out = _run(["reconcile-marketplaces"])
    assert code == 0
