from __future__ import annotations

import json
from datetime import datetime

import pytest
from agent_machines import __main__ as cli
from agent_machines import pretool_killswitch as switch


@pytest.fixture
def state_path(tmp_path, monkeypatch):
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    path = tmp_path / "control" / "pretool-killswitch.json"
    monkeypatch.setenv("PRETOOL_KILLSWITCH_STATE_FILE", str(path))
    return path


def test_hot_on_off_and_auditable_status(state_path, capsys):
    assert cli.main(["pretool-killswitch"]) == 0
    assert not switch.is_active()
    assert cli.main(["pretool-killswitch", "on", "temporary diagnosis", "--json"]) == 0
    capsys.readouterr()
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["active"] is True
    assert state["reason"] == "temporary diagnosis"
    assert state["set_by"]
    assert datetime.fromisoformat(state["set_at"]).tzinfo is not None
    assert switch.is_active()
    assert cli.main(["pretool-killswitch", "status", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["active"] is True
    assert cli.main(["pretool-killswitch", "off", "--json"]) == 0
    assert not state_path.exists()
    assert not switch.is_active()
    assert cli.main(["pretool-killswitch", "off"]) == 0


@pytest.mark.parametrize("raw", ["broken", "[]", '{"active":1}', '{"active":"true"}',
                                  '{}', "x" * (switch.MAX_STATE_BYTES + 1)])
def test_invalid_state_never_disables_guards(state_path, capsys, raw):
    state_path.parent.mkdir()
    state_path.write_text(raw, encoding="utf-8")
    assert not switch.is_active()
    assert "guards remain enabled" in capsys.readouterr().err
    assert cli.main(["pretool-killswitch", "status", "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["ok"] is False


def test_cell_never_reads_or_mutates_default_switch(state_path, monkeypatch, capsys):
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", "owned-cell")
    monkeypatch.setattr(switch, "read_state", lambda path: pytest.fail("foreign state read"))
    assert not switch.is_active()
    for verb in ("on", "off", "status"):
        assert cli.main(["pretool-killswitch", verb, "--json"]) == 2
        assert "default installation only" in json.loads(capsys.readouterr().out)["error"]
    assert not state_path.exists()


def test_write_failure_preserves_previous_state_and_cleans_temporary(
    state_path, monkeypatch, capsys,
):
    assert cli.main(["pretool-killswitch", "on", "previous"]) == 0
    previous = state_path.read_bytes()
    def refuse(*args):
        raise OSError("replacement refused")
    monkeypatch.setattr(switch.os, "replace", refuse)
    assert cli.main(["pretool-killswitch", "on", "new", "--json"]) == 2
    assert state_path.read_bytes() == previous
    assert list(state_path.parent.iterdir()) == [state_path]
    assert "replacement refused" in capsys.readouterr().out


def test_contract_discovery_uses_selected_cli_and_old_boolean_state_remains_valid(state_path, capsys):
    assert cli.main(["pretool-killswitch", "status", "--json"]) == 0
    declaration = json.loads(capsys.readouterr().out)["contract"]
    assert declaration["id"] == "copilot-extensions.pretool-killswitch"
    assert declaration["version"] == 1
    assert declaration["capabilities"] == ["pretool-stop-v1"]
    state_path.parent.mkdir()
    state_path.write_text('{"active": true, "future_advisory": "ignored"}', encoding="utf-8")
    assert switch.is_active()
    assert cli.main(["pretool-killswitch", "on", "versioned"]) == 0
    assert json.loads(state_path.read_text())["contract"]["version"] == 1


@pytest.mark.parametrize("contract", [None, [], {"id": "other", "version": 1},
                                       {"id": switch.CONTRACT["id"], "version": 2},
                                       {"id": switch.CONTRACT["id"], "version": True}])
def test_unsupported_contract_never_pauses_guards(state_path, capsys, contract):
    state_path.parent.mkdir()
    state_path.write_text(json.dumps({"active": True, "contract": contract}), encoding="utf-8")
    assert not switch.is_active()
    assert "unsupported" in capsys.readouterr().err


def test_unknown_cli_syntax_is_rejected_without_state_effect(state_path):
    with pytest.raises(SystemExit) as failure:
        cli.main(["pretool-killswitch", "on", "--unknown-option"])
    assert failure.value.code == 2
    assert not state_path.exists()


def test_relative_override_is_refused_without_effect_in_different_directories(
    tmp_path, monkeypatch, capsys,
):
    project = tmp_path / "project"
    home = tmp_path / "home"
    project.mkdir()
    home.mkdir()
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    monkeypatch.setenv("PRETOOL_KILLSWITCH_STATE_FILE", "stop.json")
    monkeypatch.chdir(project)
    assert cli.main(["pretool-killswitch", "on", "--json"]) == 2
    assert "absolute path" in json.loads(capsys.readouterr().out)["error"]
    monkeypatch.chdir(home)
    assert not switch.is_active()
    assert "guards remain enabled" in capsys.readouterr().err
    assert not (project / "stop.json").exists()
    assert not (home / "stop.json").exists()


def test_absolute_override_is_shared_by_cli_and_hook_despite_different_cwd(
    state_path, tmp_path, monkeypatch,
):
    project = tmp_path / "project"
    home = tmp_path / "home"
    project.mkdir()
    home.mkdir()
    monkeypatch.chdir(project)
    assert cli.main(["pretool-killswitch", "on", "absolute override"]) == 0
    monkeypatch.chdir(home)
    assert switch.is_active()
    assert cli.main(["pretool-killswitch", "off"]) == 0
    monkeypatch.chdir(project)
    assert not switch.is_active()


def test_default_pretool_state_is_central_not_project_relative(tmp_path, monkeypatch):
    monkeypatch.delenv("PRETOOL_KILLSWITCH_STATE_FILE", raising=False)
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    monkeypatch.setattr(switch.Path, "home", classmethod(lambda cls: tmp_path))
    expected = tmp_path / ".copilot-extensions" / "killSwitches" / "pretool-killswitch.json"
    assert switch.state_file() == expected
    assert cli.main(["pretool-killswitch", "on", "central"]) == 0
    assert expected.is_file()
    assert not (tmp_path / ".copilot-extensions/pretool-killswitch.json").exists()
    assert cli.main(["pretool-killswitch", "off"]) == 0
