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
