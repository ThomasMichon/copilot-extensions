"""Opt-in real CLI/stdout process seam with finite, synthetic launch evidence."""

import json
import subprocess
import sys

from agent_procutil import no_window_kwargs
import pytest

pytestmark = [pytest.mark.real_installer_processes, pytest.mark.timeout(30)]

SCRIPT = """
import sys
from pathlib import Path
from types import SimpleNamespace
from agent_worktrees import __main__ as main, handoff_cli, embody_receipt, output
main.front_door_cli.resolve_invocation_project = lambda *a: True
main._guard_project_scope = lambda *a: None
embody_receipt.cfg.tracking_dir = lambda: Path.cwd()
embody_receipt.tracking.load_record = lambda _: SimpleNamespace(
    resolved_head_session='native-fixture',
    session_entry=lambda _: SimpleNamespace(pane_id='%1'),
)
embody_receipt.session_liveness.session_liveness = lambda _: 'live'
embody_receipt.sessions.mux_binding_for_session = lambda *a, **k: {
    'worktree_id': 'example', 'session_name': 'wt-example', 'pane_id': '%1',
    'pane_pid': 100, 'pane_start_time': '10',
    'copilot_pid': 101, 'copilot_start_time': '11',
}
def launch(args, target):
    print('finite fixture launch diagnostic')
    output._json_output({
        'ok': True, 'created': True, 'resumed': False, 'worktree_id': 'example',
        'session': 'wt-example', 'new_pane': '%1', 'resume_session': None,
    })
    return 0
handoff_cli._cmd_embody = launch
sys.exit(main.main(sys.argv[1:]))
"""


@pytest.mark.parametrize("arguments", [[], ["--wait-session-timeout", "1"]])
def test_process_stdout_is_a_single_observed_receipt(arguments):
    result = subprocess.run(
        [sys.executable, "-c", SCRIPT, "embody", "--worktree-id", "example",
         "--json", *arguments],
        capture_output=True, text=True, timeout=20, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stderr
    document = json.loads(result.stdout)
    assert document["session_id"] == "native-fixture" and document["state"] == "live"
    assert document["created"] is True and document["resumed"] is False
    assert result.stderr == "finite fixture launch diagnostic\n"


def test_process_unknown_option_fails_before_fixture_launch():
    result = subprocess.run(
        [sys.executable, "-c", SCRIPT, "embody", "--worktree-id", "example",
         "--json", "--wait-sesion-timeout", "1"],
        capture_output=True, text=True, timeout=20, **no_window_kwargs(),
    )
    assert result.returncode == 2
    assert "unrecognized arguments" in result.stderr
    assert "finite fixture launch diagnostic" not in result.stderr
    assert result.stdout == ""
