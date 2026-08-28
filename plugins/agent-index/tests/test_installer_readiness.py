from __future__ import annotations

import json

from agent_index import __main__ as cli
from agent_index import config
from agent_index.installer_readiness import emit, evaluate


def test_unavailable_service_is_failed_not_empty(capsys):
    status = {
        "running": False,
        "error": "connection refused",
        "index": {"chunks": None},
    }

    result = evaluate(status, [])

    assert result["state"] == "failed"
    assert emit(result) == 1
    assert json.loads(capsys.readouterr().out)["state"] == "failed"


def test_unknown_corpus_state_is_not_invented_as_empty():
    status = {"running": True, "index": {"chunks": None}}

    result = evaluate(status, [])

    assert result["state"] == "failed"
    assert "state is unknown" in result["detail"]


def test_no_configured_sources_is_configuration_empty():
    status = {"running": True, "index": {"chunks": 0}}

    result = evaluate(status, [])

    assert result["state"] == "configuration-empty"
    assert "No corpus was created or indexed" in result["detail"]


def test_configured_but_empty_corpus_is_explicit():
    status = {"running": True, "index": {"chunks": 0}}

    result = evaluate(status, [{"name": "demo"}])

    assert result["state"] == "configuration-empty"
    assert "no indexed chunks" in result["detail"]


def test_populated_corpus_is_ready(capsys):
    status = {"running": True, "index": {"chunks": 7}}

    result = evaluate(status, [{"name": "demo"}])

    assert result["state"] == "ready"
    assert emit(result) == 0
    assert json.loads(capsys.readouterr().out) == result


def test_payload_command_does_not_create_or_reindex_corpus(monkeypatch, capsys):
    monkeypatch.setattr(
        cli,
        "_status_payload",
        lambda: {"running": True, "index": {"chunks": 0}},
    )
    monkeypatch.setattr(config, "read_corpus_sources", lambda: [{"name": "demo"}])

    assert cli.main(["installer-readiness"]) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "configuration-empty"
