"""Guards for the launch-session.sh nested-plan unwrap.

Non-interactive resolves (``resolve --json --worktree-id`` / ``--json --new``,
used by agent-bridge ACP launches) emit the bridge's *nested* plan shape::

    {"worktree": {...}, "launch": {"action": "exec", ...}}

launch-session.sh consumes the *flat* plan, so it unwraps the ``launch`` object
when present.  These tests pin that contract: the flat consumer must receive an
``action == "exec"`` plan for both nested and already-flat inputs.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

# The exact transformation launch-session.sh applies to resolve's stdout.
# Kept in lockstep with bin/launch-session.sh; the marker assertion below
# fails loudly if the script's snippet is removed or renamed.
_UNWRAP_SNIPPET = (
    "import sys, json\n"
    "d = json.load(sys.stdin)\n"
    "print(json.dumps(d['launch'] if isinstance(d, dict) and 'launch' in d else d))"
)

_LAUNCH_SCRIPT = (
    Path(__file__).resolve().parents[1] / "bin" / "launch-session.sh"
)


def _unwrap(plan: dict) -> dict:
    out = subprocess.run(
        [sys.executable, "-c", _UNWRAP_SNIPPET],
        input=json.dumps(plan),
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(out.stdout)


def test_nested_plan_unwraps_to_launch():
    nested = {
        "worktree": {"id": "wt-1"},
        "launch": {
            "action": "exec",
            "work_dir": "/w/wt-1",
            "cmd": ["copilot", "--acp", "--stdio"],
            "no_mux": True,
        },
    }
    flat = _unwrap(nested)
    assert flat["action"] == "exec"
    assert flat["work_dir"] == "/w/wt-1"
    assert flat["no_mux"] is True


def test_flat_plan_passes_through_unchanged():
    flat_in = {"action": "exec", "work_dir": "/w/wt", "cmd": ["copilot"]}
    assert _unwrap(flat_in) == flat_in


def test_none_action_plan_passes_through():
    assert _unwrap({"action": "none", "exit_code": 0}) == {
        "action": "none",
        "exit_code": 0,
    }


def test_launch_script_contains_unwrap_snippet():
    """Drift guard: the script must still apply the unwrap we test here."""
    text = _LAUNCH_SCRIPT.read_text()
    assert "d['launch'] if isinstance(d, dict) and 'launch' in d else d" in text
