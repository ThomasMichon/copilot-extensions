from __future__ import annotations

import json

from agent_index import __main__ as cli
from agent_index.installer_readiness import (
    CorpusConfigInspection,
    emit,
    evaluate,
    inspect_configuration,
)


def test_unavailable_service_is_failed_not_empty(capsys):
    status = {
        "running": False,
        "error": "connection refused",
        "index": {"chunks": None},
    }

    result = evaluate(status, CorpusConfigInspection())

    assert result["state"] == "failed"
    assert emit(result) == 1
    assert json.loads(capsys.readouterr().out)["state"] == "failed"


def test_unknown_corpus_state_is_not_invented_as_empty():
    status = {"running": True, "index": {"chunks": None}}

    result = evaluate(status, CorpusConfigInspection())

    assert result["state"] == "failed"
    assert "state is unknown" in result["detail"]


def test_no_configured_sources_is_configuration_empty():
    status = {"running": True, "index": {"chunks": 0}}

    result = evaluate(status, CorpusConfigInspection())

    assert result["state"] == "configuration-empty"
    assert "No corpus was created or indexed" in result["detail"]


def test_configured_but_empty_corpus_is_explicit():
    status = {"running": True, "index": {"chunks": 0}}

    result = evaluate(
        status,
        CorpusConfigInspection(sources=({"name": "demo"},)),
    )

    assert result["state"] == "configuration-empty"
    assert "no indexed chunks" in result["detail"]


def test_populated_corpus_is_ready(capsys):
    status = {"running": True, "index": {"chunks": 7}}

    result = evaluate(
        status,
        CorpusConfigInspection(sources=({"name": "demo"},)),
    )

    assert result["state"] == "ready"
    assert emit(result) == 0
    assert json.loads(capsys.readouterr().out) == result


def test_payload_command_does_not_create_or_reindex_corpus(monkeypatch, capsys):
    monkeypatch.setattr(
        "agent_index.transport.plan_route",
        lambda: ("host", {"machine": "host"}),
    )
    monkeypatch.setattr(
        cli,
        "_status_payload",
        lambda: {"running": True, "index": {"chunks": 0}},
    )
    monkeypatch.setattr(
        "agent_index.installer_readiness.inspect_configuration",
        lambda: CorpusConfigInspection(sources=({"name": "demo"},)),
    )

    assert cli.main(["installer-readiness"]) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "configuration-empty"


def test_unconfigured_payload_does_not_probe_service(monkeypatch, capsys):
    monkeypatch.setattr(
        "agent_index.transport.plan_route",
        lambda: ("unconfigured", None),
    )
    monkeypatch.setattr(
        cli,
        "_status_payload",
        lambda: (_ for _ in ()).throw(AssertionError("must not probe service")),
    )

    assert cli.main(["installer-readiness"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["state"] == "configuration-empty"
    assert "not configured for the current repository" in result["detail"]


def test_configured_client_does_not_probe_local_service(monkeypatch, capsys):
    monkeypatch.setattr(
        "agent_index.transport.plan_route",
        lambda: ("client", {"machine": "host", "ssh": "host"}),
    )
    monkeypatch.setattr(
        "agent_index.transport.has_usable_client_transport",
        lambda: True,
    )
    monkeypatch.setattr(
        cli,
        "_status_payload",
        lambda: (_ for _ in ()).throw(AssertionError("must not probe local service")),
    )

    assert cli.main(["installer-readiness"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["state"] == "ready"
    assert "configured as a client" in result["detail"]


def test_incomplete_client_is_configuration_empty(monkeypatch, capsys):
    monkeypatch.setattr(
        "agent_index.transport.plan_route",
        lambda: ("client", {"machine": "host"}),
    )
    monkeypatch.setattr(
        "agent_index.transport.has_usable_client_transport",
        lambda: False,
    )

    assert cli.main(["installer-readiness"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["state"] == "configuration-empty"
    assert "no SSH alias or endpoint" in result["detail"]


def test_strict_inspection_distinguishes_absent_and_valid_empty(tmp_path):
    absent = inspect_configuration([tmp_path / "absent.yaml"])
    empty_path = tmp_path / "empty.yaml"
    empty_path.write_text("corpus:\n  sources: []\n", encoding="utf-8")
    empty = inspect_configuration([empty_path])

    assert absent == CorpusConfigInspection()
    assert empty.errors == ()
    assert empty.sources == ()
    assert empty.present_paths == (empty_path,)


def test_strict_inspection_reports_malformed_and_unreadable(tmp_path, monkeypatch):
    malformed = tmp_path / "malformed.yaml"
    malformed.write_text("corpus: [", encoding="utf-8")
    unreadable = tmp_path / "unreadable.yaml"
    unreadable.write_text("{}\n", encoding="utf-8")
    original = type(unreadable).read_text

    def fail_read(path, *args, **kwargs):
        if path == unreadable:
            raise OSError("fixture denied")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(type(unreadable), "read_text", fail_read)
    inspection = inspect_configuration([malformed, unreadable])

    assert len(inspection.errors) == 2
    assert "malformed YAML" in inspection.errors[0]
    assert "unreadable configuration" in inspection.errors[1]


def test_strict_inspection_reports_invalid_utf8(tmp_path):
    invalid = tmp_path / "invalid.yaml"
    invalid.write_bytes(b"\xff")

    inspection = inspect_configuration([invalid])

    assert len(inspection.errors) == 1
    assert "unreadable configuration" in inspection.errors[0]


def test_malformed_configuration_fails_even_when_corpus_is_empty():
    status = {"running": True, "index": {"chunks": 0}}

    result = evaluate(
        status,
        CorpusConfigInspection(errors=("broken config",)),
    )

    assert result["state"] == "failed"
    assert "did not reindex" in result["detail"]


def test_populated_corpus_without_attributable_sources_fails():
    status = {"running": True, "index": {"chunks": 7}}

    result = evaluate(status, CorpusConfigInspection())

    assert result["state"] == "failed"
    assert "no readable corpus source declaration owns them" in result["detail"]
