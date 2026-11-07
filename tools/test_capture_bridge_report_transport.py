"""Capture reports survive pytest's shell-style environment option parsing."""

import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
from types import ModuleType, SimpleNamespace

import capture_bridge_http48 as capture


def test_capture_report_path_survives_spaces_and_backslashes(tmp_path, monkeypatch):
    root = tmp_path / "source with spaces"
    root.mkdir()
    (root / ".test-venvs").mkdir()
    (root / "input.json").write_text("{}")
    (root / "entry.py").write_text("")
    expected = {"receipt": "synthetic"}
    evidence = SimpleNamespace(
        GENERATION=51, INPUT="input.json", TEST="plugins/agent-bridge/tests/test_probe.py",
        PROBES={"probe": 1},
        safe=lambda root, *parts, **kwargs: root.joinpath(*parts),
        encode=lambda value: json.dumps(value).encode(),
        digest=lambda value: hashlib.sha256(value).hexdigest(),
        installation=SimpleNamespace(ENTRY="entry.py", required_sources=lambda value: set()),
    )

    def run(argv, **kwargs):
        options = shlex.split(os.environ["PYTEST_ADDOPTS"])
        assert len(options) == 1
        report = Path(options[0].removeprefix("--junitxml="))
        report.write_text('<testsuite><testcase name="probe"/></testsuite>')
        request = json.loads(Path(os.environ[capture.binding.REQUEST]).read_bytes())
        Path(request["report"]).write_text(json.dumps({
            "legacy": expected, "namespaced": expected,
        }))
        return subprocess.CompletedProcess(argv, 0, "", "")

    owner = ModuleType("agent_procutil.owned_process")
    owner.run_caller_owned = run
    monkeypatch.setitem(sys.modules, "agent_procutil.owned_process", owner)
    monkeypatch.setattr(capture.binding, "verify_interpreters", lambda *a, **k: {"roles": ["observer"]})
    result = capture.run_probes(
        root, {"input.json": {"sha256": "synthetic-input"}}, {"expected": expected},
        evidence=evidence,
    )
    assert result == {"legacy": expected, "namespaced": expected}
