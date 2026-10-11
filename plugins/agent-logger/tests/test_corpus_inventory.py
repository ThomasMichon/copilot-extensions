"""On-demand corpus visibility, independent of logging and accounting state."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_logger import sessions
from agent_logger.__main__ import main
from agent_logger.corpus_inventory import inventory_corpus

pytestmark = pytest.mark.contract("agent_logger.corpus_inventory")


def _session(root: Path, session_id: str) -> Path:
    path = root / "session-state" / session_id
    path.mkdir(parents=True)
    (path / "events.jsonl").write_text('{"type":"session.start"}\n', encoding="utf-8")
    return path


def test_inventory_retains_source_qualified_ids_without_metadata_or_filters(tmp_path):
    keys = ("host", ".codespaces/box", "host.containers/worker", "repo.codespaces/box")
    for key in keys:
        _session(tmp_path / key, "same-session")
    before = sorted(str(path.relative_to(tmp_path)) for path in tmp_path.rglob("*"))
    result = inventory_corpus(tmp_path, include_sessions=True)
    assert result["complete"]
    assert result["source_count"] == result["session_count"] == 4
    assert {row["source_key"] for row in result["sources"]} == set(keys)
    assert all(row["live"] == 1 and row["archived"] == 0 for row in result["sources"])
    assert all(
        row["sessions"] == [{"session_id": "same-session", "kind": "live"}]
        for row in result["sources"]
    )
    assert sorted(str(path.relative_to(tmp_path)) for path in tmp_path.rglob("*")) == before


def test_inventory_uses_archive_reader_and_live_precedence(tmp_path, monkeypatch):
    corpus = tmp_path / "corpus"
    source = corpus / "host"
    state = _session(source, "archived-session")
    store = source / "archived"
    sessions.archive_session(state, store, codec="zip")
    archive_only = _session(tmp_path / "staging", "archive-only")
    sessions.archive_session(archive_only, store, codec="targz")
    noise = source / "session-state" / "no-events"
    noise.mkdir()
    monkeypatch.setattr(
        sessions, "read_member",
        lambda *_args: pytest.fail("inventory must not read transcript members"),
    )
    result = inventory_corpus(corpus)
    assert result["complete"]
    assert result["session_count"] == 2
    assert result["sources"][0]["live"] == 1
    assert result["sources"][0]["archived"] == 1
    assert result["sources"][0]["sessions"] is None


def test_inventory_reports_failed_source_and_continues(tmp_path):
    _session(tmp_path / "a-broken", "bad-session")
    (tmp_path / "a-broken" / "archived").write_text("not a directory", encoding="utf-8")
    _session(tmp_path / "b-valid", "good-session")
    result = inventory_corpus(tmp_path, include_sessions=True)
    assert not result["complete"]
    assert result["source_count"] == 2
    assert result["session_count"] == 1
    assert result["sources"][0]["error"]
    assert result["sources"][1]["sessions"] == [
        {"session_id": "good-session", "kind": "live"},
    ]
    assert result["errors"]


def test_inventory_archive_only_and_divergent_formats(tmp_path):
    source = tmp_path / "corpus" / "host"
    store = source / "archived"
    staging = _session(tmp_path / "staging", "session-1")
    sessions.archive_session(staging, store, codec="zip")
    result = inventory_corpus(source.parent, include_sessions=True)
    assert result["complete"]
    assert result["sources"][0]["sessions"] == [
        {"session_id": "session-1", "kind": "archive"},
    ]
    (staging / "events.jsonl").write_text('{"type":"session.end"}\n', encoding="utf-8")
    sessions.get_codec("targz").archive_dir(staging, store / "session-1.tar.gz")
    result = inventory_corpus(source.parent)
    assert not result["complete"]
    assert result["session_count"] == 0
    assert "divergent" in result["errors"][0]


def test_inventory_cli_corrupt_overlap_is_incomplete_json(tmp_path, capsys):
    store = tmp_path / "a-broken" / "archived"
    store.mkdir(parents=True)
    (store / "session-1.tar.gz").write_bytes(b"invalid gzip archive")
    (store / "session-1.zip").write_bytes(b"invalid zip archive")
    _session(tmp_path / "b-valid", "good-session")
    assert main(["corpus", "inventory", "--root", str(tmp_path)]) == 1
    result = json.loads(capsys.readouterr().out)
    assert not result["complete"]
    assert result["sources"][0]["error"]
    assert result["sources"][1]["live"] == 1
    assert result["session_count"] == 1


def test_inventory_permission_error_is_not_an_empty_success(tmp_path, monkeypatch):
    from agent_logger import corpus_inventory

    def denied(_root, **_kwargs):
        raise PermissionError("inventory access denied")

    monkeypatch.setattr(corpus_inventory, "iter_archive_sources", denied)
    result = inventory_corpus(tmp_path)
    assert not result["complete"]
    assert result["errors"] == ["inventory access denied"]


@pytest.mark.parametrize("key", ["a-broken", "a.codespaces/broken"])
def test_inventory_continues_after_malformed_source_metadata(tmp_path, key):
    from agent_logger.source_roots import SOURCE_METADATA_MEMBER, iter_archive_sources

    _session(tmp_path / key, "bad-session")
    (tmp_path / key / SOURCE_METADATA_MEMBER).write_text("{broken", encoding="utf-8")
    _session(tmp_path / "b-valid", "good-session")
    result = inventory_corpus(tmp_path, include_sessions=True)
    assert not result["complete"]
    assert result["source_count"] == 2
    assert result["session_count"] == 1
    assert result["sources"][0]["source_key"] == key
    assert result["sources"][0]["error"]
    assert result["sources"][1]["sessions"] == [
        {"session_id": "good-session", "kind": "live"},
    ]
    with pytest.raises(ValueError):
        list(iter_archive_sources(tmp_path))


def test_inventory_empty_root_succeeds(tmp_path):
    result = inventory_corpus(tmp_path)
    assert result["complete"]
    assert result["sources"] == result["errors"] == []
    assert result["source_count"] == result["session_count"] == 0


def test_inventory_invalid_provider_group_is_not_counted_as_a_source(tmp_path):
    (tmp_path / "bad!.codespaces").mkdir()
    result = inventory_corpus(tmp_path)
    assert not result["complete"]
    assert result["source_count"] == 0
    assert result["sources"] == []
    assert result["errors"]


@pytest.mark.parametrize("layout", ["missing", "file"])
def test_inventory_cli_explicit_failure_is_nonzero(tmp_path, capsys, layout):
    root = tmp_path / "corpus"
    if layout == "file":
        root.write_text("not a directory", encoding="utf-8")
    assert main(["corpus", "inventory", "--root", str(root)]) == 1
    result = json.loads(capsys.readouterr().out)
    assert not result["complete"]
    assert result["errors"]
    assert result["session_count"] == 0


def test_inventory_cli_requires_root_and_emits_json(tmp_path, capsys):
    with pytest.raises(SystemExit) as exc:
        main(["corpus", "inventory"])
    assert exc.value.code == 2
    capsys.readouterr()
    _session(tmp_path / "host", "session-1")
    assert main(["corpus", "inventory", "--root", str(tmp_path), "--include-sessions"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["scope"] == "primary-session-stores"
    assert result["session_count"] == 1
    assert result["sources"][0]["sessions"][0]["session_id"] == "session-1"
