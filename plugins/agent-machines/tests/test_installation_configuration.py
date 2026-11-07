"""Configuration reads and explicit repair use the real native module owner."""

import json
import os

import pytest

from agent_machines import __main__ as cli
from agent_machines import installation_configuration as configuration
from .test_installation_repair import repair_fixture as repair_fixture

pytestmark = [
    pytest.mark.guard, pytest.mark.portfolio_tier("T2"), pytest.mark.effect("process"),
    pytest.mark.contract("agent_machines.installation_configuration"),
]


def select(monkeypatch, fixture):
    home, project, *_ = fixture
    for key in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(key, str(home))
    monkeypatch.setattr(
        configuration.discover, "resolve_registered_repo",
        lambda name: ("sample", project) if name == "sample" else None,
    )


def read(capsys):
    return [json.loads(line) for line in capsys.readouterr().out.splitlines()]


def test_native_configuration_observe_explicit_repair_and_post_observation(
    repair_fixture, monkeypatch, capsys,
):
    select(monkeypatch, repair_fixture)
    home, *_ = repair_fixture
    arguments = ["installation-configuration", "--project", "sample"]
    assert cli.main([*arguments, "--stream"]) == 0
    frames = read(capsys)
    assert frames[0]["summary"]["repairable"] == "false"
    assert frames[1]["entry"]["state"] == "checking"
    assert frames[-1] == {"type": "done"}
    summary = frames[-2]["summary"]
    assert summary["status"] == "incomplete" and summary["repairable"] == "true"
    assert any(frame.get("entry", {}).get("state") == "not-ready" for frame in frames)
    assert not list(home.glob("*.ready"))
    assert "fixture-private-detail" not in json.dumps(frames)
    assert cli.main([
        *arguments, "--apply", "--expected-plan-digest", summary["plan_digest"],
    ]) == 0
    applied = read(capsys)[0]
    assert applied["summary"]["required_satisfied"] is True
    assert len(list(home.glob("*.ready"))) == 2
    assert all(row["attempted"] for row in applied["repair"]["outcomes"])
    assert cli.main(arguments) == 0
    observed = read(capsys)[0]
    assert observed["summary"]["status"] == "ready"
    assert observed["summary"]["repairable"] == "false"
    assert all(row["state"] == "ready" for row in observed["entries"])
    assert all(not row["attempted"] for row in observed["repair"]["outcomes"])


def test_native_configuration_changed_plan_refuses_without_installer(
    repair_fixture, monkeypatch, capsys,
):
    select(monkeypatch, repair_fixture)
    home, _, _, configure, _ = repair_fixture
    arguments = ["installation-configuration", "--project", "sample"]
    assert cli.main(arguments) == 0
    digest = read(capsys)[0]["summary"]["plan_digest"]
    configure(dependencies=())
    assert cli.main([*arguments, "--apply", "--expected-plan-digest", digest]) == 1
    assert not list(home.glob("*.ready"))
    assert "changed" in capsys.readouterr().err


def test_native_configuration_pending_restart_never_offers_automatic_replay(
    repair_fixture, monkeypatch, capsys,
):
    select(monkeypatch, repair_fixture)
    _, _, _, configure, _ = repair_fixture
    configure(restart="shell", dependencies=("agent-example/base",))
    arguments = ["installation-configuration", "--project", "sample"]
    assert cli.main(arguments) == 0
    digest = read(capsys)[0]["summary"]["plan_digest"]
    assert cli.main([*arguments, "--apply", "--expected-plan-digest", digest]) == 2
    applied = read(capsys)[0]
    assert applied["summary"]["status"] == "incomplete"
    assert applied["summary"]["repairable"] == "false"
    assert any(row["state"] == "restart-required" for row in applied["entries"])
    assert "explicit confirmation" in applied["summary"]["restart_guidance"]
    assert cli.main(arguments) == 0
    observed = read(capsys)[0]
    assert observed["summary"]["repairable"] == "false"
    assert observed["repair"]["restarts"]["pending"]
    assert all(not row["attempted"] for row in observed["repair"]["outcomes"])


def test_configuration_missing_project_stream_is_error_not_empty_success(monkeypatch, capsys):
    monkeypatch.setattr(configuration.discover, "resolve_registered_repo", lambda name: None)
    monkeypatch.setattr(configuration, "repair", lambda *a, **k: pytest.fail("unselected owner"))
    assert cli.main(["installation-configuration", "--project", "absent", "--stream"]) == 1
    frames = read(capsys)
    assert frames[-1]["type"] == "error" and not any(row["type"] == "done" for row in frames)
    assert frames[0]["summary"]["repairable"] == "false"


def test_configuration_stream_cannot_install(monkeypatch, capsys):
    monkeypatch.setattr(configuration, "repair", lambda *a, **k: pytest.fail("stream installed"))
    assert cli.main([
        "installation-configuration", "--project", "sample", "--stream", "--apply",
    ]) == 1
    assert not capsys.readouterr().out


def test_native_configuration_action_progress_is_not_terminal_until_repair_completes(
    repair_fixture, monkeypatch, capsys,
):
    select(monkeypatch, repair_fixture)
    arguments = ["installation-configuration", "--project", "sample"]
    assert cli.main(arguments) == 0
    digest = read(capsys)[0]["summary"]["plan_digest"]
    assert cli.main([
        *arguments, "--apply", "--progress", "--expected-plan-digest", digest,
    ]) == 0
    frames = read(capsys)
    assert all(frame["type"] == "progress" for frame in frames[:-1])
    assert any(frame["msg"].startswith("Repairing ") for frame in frames[:-1])
    assert frames[-1]["type"] == "done"


def test_native_configuration_action_progress_preserves_failed_owner_and_exit(
    repair_fixture, monkeypatch, capsys,
):
    select(monkeypatch, repair_fixture)
    home, _, payload, *_ = repair_fixture
    script = payload / "scripts" / ("action.ps1" if os.name == "nt" else "action.sh")
    text = script.read_text(encoding="utf-8")
    text = text.replace(
        "if ($Action -eq 'install') {", "if ($Action -eq 'install') { exit 7;"
    ) if os.name == "nt" else text.replace(
        'if [ "$1" = install ]; then', 'if [ "$1" = install ]; then exit 7;'
    )
    script.write_text(text, encoding="utf-8")
    arguments = ["installation-configuration", "--project", "sample"]
    assert cli.main(arguments) == 0
    digest = read(capsys)[0]["summary"]["plan_digest"]
    assert cli.main([
        *arguments, "--apply", "--progress", "--expected-plan-digest", digest,
    ]) == 2
    frames = read(capsys)
    assert frames[-1]["type"] == "error" and not any(row["type"] == "done" for row in frames)
    assert any("repair-failed" in row.get("msg", "") for row in frames)
    assert not list(home.glob("*.ready"))
