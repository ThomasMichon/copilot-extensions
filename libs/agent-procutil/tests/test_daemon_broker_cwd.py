import json
from pathlib import Path
import subprocess
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agent_procutil import daemon_broker as broker


def test_activation_helper_does_not_inherit_the_application_slot_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SystemRoot", str(tmp_path / "windows"))
    run = Mock(return_value=SimpleNamespace(returncode=0, stdout="1234"))
    monkeypatch.setattr(subprocess, "run", run)
    target = str(tmp_path / "daemon-workspace")
    assert broker._activate(["python", "daemon.py"], {"FIXTURE": "value"}, target, time.monotonic() + 30) == 1234
    arguments, options = run.call_args
    assert options["cwd"] == Path(arguments[0][0]).parent
    payload = json.loads(options["input"])
    assert payload["cwd"] == target
    assert payload["environment"] == ["FIXTURE=value"]
    assert options["capture_output"] and not options["check"]
