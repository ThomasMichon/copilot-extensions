import json
import os
import sys
from types import SimpleNamespace

import pytest

from conftest import pytest_runtest_logreport, run_artifact
from qualification import PARAMETER_COUNTS, membership, membership_digest


def original_nodes():
    return [
        family + (f"[case-{index}]" if count > 1 else "")
        for family, count in PARAMETER_COUNTS.items() for index in range(count)
    ]


def test_membership_is_exact_and_full_retains_every_parameter(request, monkeypatch, capsys):
    report = getattr(request.config, "_chat_membership", None)
    if os.environ.get("AGENT_CHAT_TEST_SUITE"):
        assert report is not None
        path = request.config._tmp_path_factory.getbasetemp() / "chat-membership.json"
        assert json.loads(path.read_text("utf-8")) == report
        assert not path.with_suffix(".tmp").exists()
    else:
        assert report is None
    monkeypatch.setenv("AGENT_CHAT_TEST_SUITE", "acceptance")
    pytest_runtest_logreport(SimpleNamespace(
        failed=True, nodeid="test_synthetic.py::test_failure", when="call",
        longreprtext="synthetic assertion evidence",
    ))
    assert capsys.readouterr().err == (
        "\nChat failure: test_synthetic.py::test_failure [call]\nsynthetic assertion evidence\n"
    )
    original = original_nodes()
    mapping = membership(original)
    acceptance = {node for node, lane in mapping.items() if lane == "acceptance"}
    full_only = {node for node, lane in mapping.items() if lane == "full-only"}
    assert acceptance and full_only and acceptance.isdisjoint(full_only)
    assert acceptance | full_only == set(original)
    assert membership_digest(mapping) == membership_digest(membership(list(reversed(original))))
    changed = [node.replace("[case-", "[changed-") for node in original]
    assert membership_digest(mapping) != membership_digest(membership(changed))
    assert sum(PARAMETER_COUNTS.values()) == 236 + 6


@pytest.mark.parametrize("change", ["missing", "new", "duplicate", "parameter"])
def test_membership_refuses_missing_unassigned_or_duplicate_nodes(change):
    nodes = original_nodes()
    if change == "missing":
        nodes.pop()
    elif change == "new":
        nodes.append("test_new.py::test_unassigned")
    elif change == "duplicate":
        nodes.append(nodes[0])
    else:
        nodes.remove(next(node for node in nodes if "[" in node))
    with pytest.raises(ValueError):
        membership(nodes)


def test_artifact_helper_preserves_cwd_stdin_and_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("PYTHONPATH", str(tmp_path / "unselected-source"))
    result = json.loads(run_artifact([
        sys.executable, "-I", "-c",
        "import json,os,sys; from pathlib import Path; "
        "print(json.dumps({'cwd':str(Path.cwd()),'input':sys.stdin.read(),"
        "'pythonpath':'PYTHONPATH' in os.environ}))",
    ], cwd=tmp_path, input_text="fixture input", timeout=20))
    assert result == {"cwd": str(tmp_path), "input": "fixture input", "pythonpath": False}
