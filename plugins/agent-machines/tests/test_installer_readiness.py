from __future__ import annotations

import json

from agent_machines import __main__ as cli
from agent_machines.installer_readiness import emit, evaluate
from agent_machines.layout import LayoutFinding, LayoutReport


def test_no_requirement_packages_is_configuration_empty(capsys):
    result = evaluate([])

    assert result["state"] == "configuration-empty"
    assert emit(result) == 0
    assert json.loads(capsys.readouterr().out) == result


def test_optional_unavailable_repo_does_not_invent_packages():
    report = LayoutReport("demo", "missing", "unavailable")

    result = evaluate([report])

    assert result["state"] == "configuration-empty"
    assert "unavailable" in result["detail"]


def test_invalid_layout_is_failed_and_nonzero(capsys):
    report = LayoutReport(
        "demo",
        "repo",
        "malformed",
        findings=[LayoutFinding("error", "invalid-layout", "bad manifest")],
    )

    result = evaluate([report])

    assert result["state"] == "failed"
    assert "agent-machines doctor" in result["detail"]
    assert emit(result) == 1
    assert json.loads(capsys.readouterr().out)["state"] == "failed"


def test_applicable_package_is_ready():
    report = LayoutReport("demo", "repo", "canonical", package_count=2)

    result = evaluate([report])

    assert result["state"] == "ready"
    assert "2 applicable" in result["detail"]


def test_payload_command_is_independently_runnable(monkeypatch, capsys):
    monkeypatch.setattr(cli._layout, "inspect_layouts", lambda _machine: [])

    assert cli.main(["installer-readiness", "--machine", "fixture"]) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "configuration-empty"
