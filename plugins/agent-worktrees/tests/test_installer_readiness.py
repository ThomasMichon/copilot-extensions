from __future__ import annotations

import json

from agent_worktrees import __main__ as cli
from agent_worktrees.installer_readiness import emit, evaluate


def test_readiness_is_ready_without_project_configuration(capsys):
    result = evaluate()

    assert result["state"] == "ready"
    assert result["module"] == "agent-worktrees/runtime"
    assert emit(result) == 0
    assert json.loads(capsys.readouterr().out) == result


def test_failed_readiness_maps_to_nonzero(capsys):
    result = {
        "schema": "copilot-extensions.module-readiness",
        "version": 1,
        "module": "agent-worktrees/runtime",
        "state": "failed",
        "detail": "fixture failure",
    }

    assert emit(result) == 1
    assert json.loads(capsys.readouterr().out)["state"] == "failed"


def test_payload_command_is_independently_runnable(capsys):
    assert cli.main(["installer-readiness"]) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "ready"
