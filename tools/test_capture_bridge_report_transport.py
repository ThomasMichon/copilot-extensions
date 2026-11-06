"""Capture reports survive pytest's shell-style environment option parsing."""

import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
from types import ModuleType, SimpleNamespace

import pytest

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


@pytest.mark.parametrize("fault", [None, "missing", "duplicate", "foreign", "expired"])
def test_capture_groups_share_one_deadline_and_verify_the_complete_portfolio(tmp_path, monkeypatch, fault):
    root = tmp_path
    (root / "input.json").write_text("{}")
    (root / "entry.py").write_text("")
    evidence = SimpleNamespace(
        GENERATION=52, INPUT="input.json", TEST="plugins/agent-bridge/tests/test_probe.py",
        PROBES={"native": 1, "protocol": 1},
        safe=lambda root, *parts, **kwargs: root.joinpath(*parts),
        encode=lambda value: json.dumps(value).encode(),
        digest=lambda value: hashlib.sha256(value).hexdigest(),
        installation=SimpleNamespace(ENTRY="entry.py", required_sources=lambda value: set()),
    )
    budget = 600 if os.name == "nt" else 240
    clock = iter((0, 1, budget + 1 if fault == "expired" else 3))
    monkeypatch.setattr(capture, "time", SimpleNamespace(monotonic=lambda: next(clock)))
    observed = []

    def run(argv, **kwargs):
        selected = argv[argv.index("-k") + 1]
        observed.append((selected, kwargs["timeout"]))
        options = shlex.split(os.environ["PYTEST_ADDOPTS"])
        junit = Path(options[0].removeprefix("--junitxml="))
        names = [selected]
        if selected == "protocol":
            names = {"missing": [], "duplicate": ["protocol", "protocol"], "foreign": ["native"]}.get(fault, names)
        junit.write_text("<testsuite>" + "".join(f'<testcase name="{name}"/>' for name in names) + "</testsuite>")
        request = json.loads(Path(os.environ[capture.binding.REQUEST]).read_bytes())
        Path(request["report"]).write_text(json.dumps({"legacy": {}, "namespaced": {}}))
        return subprocess.CompletedProcess(argv, 0, "", "")

    owner = ModuleType("agent_procutil.owned_process")
    owner.run_caller_owned = run
    monkeypatch.setitem(sys.modules, "agent_procutil.owned_process", owner)
    monkeypatch.setattr(capture.binding, "verify_interpreters", lambda *a, **k: {"roles": ["observer"]})
    args = (root, {"input.json": {"sha256": "synthetic-input"}}, {"expected": {}})
    kwargs = {"evidence": evidence, "isolated_probes": frozenset({"native"})}
    if fault:
        with pytest.raises(ValueError, match="portfolio|deadline"):
            capture.run_probes(*args, **kwargs)
    else:
        assert capture.run_probes(*args, **kwargs) == {"legacy": {}, "namespaced": {}}
    assert observed == [("native", budget - 1), *([] if fault == "expired" else [("protocol", budget - 3)])]
