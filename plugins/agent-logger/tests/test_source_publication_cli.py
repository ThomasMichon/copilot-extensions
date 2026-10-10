"""Explicit identity-file publication through the real session-sync CLI."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_logger.config import Config, load_config
from agent_logger.source_publication import load_source_identity_file
from agent_logger.source_roots import SourceIdentity
from agent_logger.sync import engine


def _setup(tmp_path: Path, monkeypatch, target: str = "local"):
    source = tmp_path / "source"
    session = source / "session-state" / "abc-123"
    session.mkdir(parents=True)
    (session / "events.jsonl").write_text("{}\n", encoding="utf-8")
    destination = tmp_path / "destination"
    home = tmp_path / "home"
    data = load_config(home=home, include_repo=False).as_dict()
    data["sync"]["target"] = target
    if target == "onedrive":
        data["sync"]["targets"][target]["root"] = str(destination)
        data["sync"]["targets"][target]["subfolder"] = ""
    else:
        data["sync"]["targets"][target]["path"] = str(destination)
    cfg = Config(data, home)
    monkeypatch.setattr(engine, "load_config", lambda: cfg)
    monkeypatch.delenv("AGENT_LOGGER_SYNC_DISABLED", raising=False)
    return source, destination


def _write_identity(tmp_path: Path, identity: SourceIdentity) -> Path:
    marker = tmp_path / ".archive-source.json"
    marker.write_text(json.dumps({"schema_version": 1, **identity.to_dict()}), encoding="utf-8")
    return marker


@pytest.mark.parametrize("identity", [
    SourceIdentity("machine", "copilot", host="source-host"),
    SourceIdentity("container", "containers", host="source-host", venue_name="worker"),
    SourceIdentity("codespace", "github", repository="owner/repo", venue_name="box"),
])
def test_identity_file_cli_publishes_canonical_metadata(tmp_path, monkeypatch, identity):
    source, destination = _setup(tmp_path, monkeypatch)
    marker = _write_identity(tmp_path, identity)
    args = [
        "push", "--source", str(source), "--machine", identity.namespace,
        "--source-identity-file", str(marker),
    ]
    assert engine.main(args) == 0
    leaf = destination / identity.namespace
    assert load_source_identity_file(leaf / ".archive-source.json") == identity
    assert (leaf / "session-state" / "abc-123" / "events.jsonl").read_text() == "{}\n"
    original_marker = (leaf / ".archive-source.json").read_bytes()
    assert engine.main(args) == 0
    assert (leaf / ".archive-source.json").read_bytes() == original_marker


@pytest.mark.parametrize("invalid", ["missing", "malformed", "schema", "key"])
def test_identity_file_cli_rejects_before_target_construction(
    tmp_path, monkeypatch, capsys, invalid,
):
    source, destination = _setup(tmp_path, monkeypatch)
    identity = SourceIdentity("machine", "copilot", host="source-host")
    marker = tmp_path / "missing.json"
    key = identity.namespace
    if invalid != "missing":
        marker = _write_identity(tmp_path, identity)
        if invalid == "malformed":
            marker.write_text("{", encoding="utf-8")
        elif invalid == "schema":
            marker.write_text('{"schema_version": 2}', encoding="utf-8")
        else:
            key = "unrelated-host"

    def unexpected_target(*args):
        pytest.fail("invalid identity reached target construction")

    monkeypatch.setattr(engine, "build_target", unexpected_target)
    assert engine.main([
        "push", "--source", str(source), "--machine", key,
        "--source-identity-file", str(marker),
    ]) == 1
    assert "source identity rejected" in capsys.readouterr().err
    assert not destination.exists()


def test_identity_file_cli_rejects_unsupported_onedrive(tmp_path, monkeypatch, capsys):
    source, destination = _setup(tmp_path, monkeypatch, target="onedrive")
    identity = SourceIdentity("machine", "copilot", host="source-host")
    marker = _write_identity(tmp_path, identity)
    assert engine.main([
        "push", "--source", str(source), "--machine", identity.namespace,
        "--source-identity-file", str(marker),
    ]) == 1
    assert "cannot enforce cross-writer identity admission" in capsys.readouterr().err
    assert not (destination / identity.namespace / "session-state").exists()
    assert not (destination / identity.namespace / ".archive-source.json").exists()


def test_identity_file_cli_absent_retains_legacy_push(tmp_path, monkeypatch):
    source, destination = _setup(tmp_path, monkeypatch)
    assert engine.main([
        "push", "--source", str(source), "--machine", ".codespaces/legacy",
    ]) == 0
    leaf = destination / ".codespaces" / "legacy"
    assert (leaf / "session-state" / "abc-123" / "events.jsonl").is_file()
    assert not (leaf / ".archive-source.json").exists()
