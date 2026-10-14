"""Exact metadata reads are not roster computation or execution authority."""

from __future__ import annotations

import argparse
import json
import subprocess
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
import yaml

from agent_worktrees import __main__ as main
from agent_worktrees import config as cfg
from agent_worktrees import list_cli, output, record_cli, session_tracking_cli, sessions, tracking
from agent_worktrees import worktrees_client as client


@pytest.fixture
def record_fixture(tmp_path, monkeypatch):
    project = "example-project"
    cfg.set_active_project(project)
    tdir = tmp_path / f".{project}" / "worktrees"
    tdir.mkdir(parents=True)
    config = cfg.Config(
        srcroot=str(tmp_path),
        machine="receiving-owner",
        platform="linux",
        repo_name=project,
        repos={
            project: cfg.RepoConfig(
                anchor=str(tmp_path / "repo"),
                worktree_root=str(tmp_path / "workspaces"),
            )
        },
    )
    monkeypatch.setattr(cfg, "load_config", lambda **kw: config)
    monkeypatch.setattr(cfg, "_home", lambda: tmp_path)
    monkeypatch.setattr(cfg.project_state, "project_state_root", lambda project, legacy: legacy)
    monkeypatch.setattr(main, "_resolve_active_project", lambda _: (project, None))
    data = {
        "worktree_id": "example-worktree",
        "repo": project,
        "branch": "worktree/example",
        "worktree_path": str(tmp_path / "workspaces" / "example-worktree"),
        "status": "active",
        "creation_nonce": "example-incarnation",
        "sessions": [],
        "machine": "historical-owner",
        "platform": "windows",
        "owner": "example-producer",
        "owner_ref": "historical-owner/example-project/example-parent",
    }
    path = tdir / "example-worktree.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    yield data, path
    cfg.set_active_project(None)


def invoke(*args):
    with output.capture_json_output() as captured:
        rc = main.main(["record", "--worktree-id", *args, "--json"])
    return rc, json.loads(captured.getvalue())


def test_record_read_exact_unpublished_record_and_unchanged_list(record_fixture, monkeypatch):
    data, path = record_fixture
    requests = []
    publication = {
        "ok": True,
        "contract_version": 1,
        "worktrees": [],
        "projection": {"quality": "current"},
    }
    monkeypatch.setattr(
        client, "read_current", lambda request: requests.append(request) or publication
    )
    monkeypatch.setattr(list_cli, "_status_monitor_enabled", lambda: True)
    monkeypatch.setattr(list_cli.list_cache, "note_demand", lambda *a, **kw: None)
    parser = argparse.ArgumentParser()
    list_cli.add_parsers(parser.add_subparsers(dest="command"))
    args = parser.parse_args(
        ["list", "--json", "--cache-only", "--worktree-id", data["worktree_id"]]
    )
    with output.capture_json_output() as captured:
        assert list_cli.cmd_list(args) == 0
    assert json.loads(captured.getvalue())["worktrees"] == []
    assert len(requests) == 1

    def forbidden(*args, **kwargs):
        pytest.fail("exact record read scanned, computed, launched or mutated")

    for module, name in (
        (tracking, "list_records"),
        (tracking, "load_record"),
        (tracking, "load_record_by_id"),
        (tracking, "save_record"),
        (sessions, "scan_sessions_fast"),
        (sessions, "mux_status_many"),
        (client, "read_current"),
        (session_tracking_cli, "_all_tracking_dirs"),
        (session_tracking_cli, "_find_tracking_file"),
        (subprocess, "Popen"),
        (Path, "glob"),
        (Path, "iterdir"),
        (main, "_ensure_cluster_loaded"),
    ):
        monkeypatch.setattr(module, name, forbidden)
    before = path.read_bytes()
    reads = []
    read_text = tracking._read_text_with_retry

    def read_spy(self, *args, **kwargs):
        reads.append(self)
        return read_text(self, *args, **kwargs)

    monkeypatch.setattr(tracking, "_read_text_with_retry", read_spy)
    monkeypatch.setattr(Path, "write_text", forbidden)
    monkeypatch.setattr(Path, "mkdir", forbidden)
    rc, result = invoke(data["worktree_id"])
    assert rc == 0
    assert result["version"] == 1 and result["ok"] is True
    assert result["worktree"] == {
        "id": data["worktree_id"],
        "repo": data["repo"],
        "path": data["worktree_path"],
        "branch": data["branch"],
        "creation_nonce": data["creation_nonce"],
        "status": "active",
        "session_count": 0,
        "last_session_id": None,
        "execution_leg": None,
        "execution_leg_opaque": False,
        "sessions_indexed": True,
        "session_backend": None,
        "session_backend_opaque": False,
        "machine": "historical-owner",
        "platform": "windows",
        "owner": data["owner"],
        "owner_ref": data["owner_ref"],
        "caller_worktree": None,
        "parent_session": None,
        "checkout_managed": None,
    }
    assert reads == [path]
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    "raw_id", ["", "../example", "/example", "a\\b", "a:b", "a b", "a\nb", "a" * 201]
)
def test_record_read_invalid_id_before_config(record_fixture, monkeypatch, raw_id):
    monkeypatch.setattr(cfg, "load_config", lambda **kw: pytest.fail("invalid ID reached config"))
    rc, result = invoke(raw_id)
    assert rc == 1
    assert result["error"]["code"] == "invalid-id"
    assert result["worktree"] is None


@pytest.mark.parametrize("raw_id", ["missing-record", "worktree"])
def test_record_read_missing_is_not_terminal_or_suffix_lookup(record_fixture, raw_id):
    rc, result = invoke(raw_id)
    assert rc == 1 and result["error"]["code"] == "record-missing"
    assert result["worktree"] is None


@pytest.mark.parametrize(
    ("change", "code"),
    [
        ({"worktree_id": "other-worktree"}, "id-mismatch"),
        ({"repo": "other-project"}, "repository-mismatch"),
        ({"repo": None}, "record-malformed"),
        ({"worktree_path": "relative/path"}, "path-invalid"),
        ({"worktree_path": "/workspace/../other"}, "path-invalid"),
        ({"worktree_path": ""}, "record-malformed"),
        ({"status": "disposed"}, "record-malformed"),
        ({"sessions": {}}, "record-malformed"),
        ({"sessions": [None]}, "record-malformed"),
        ({"sessions": [{"session_id": 1}]}, "record-malformed"),
        ({"creation_nonce": True}, "record-malformed"),
        ({"machine": []}, "record-malformed"),
        ({"owner_ref": {}}, "record-malformed"),
        ({"checkout_managed": "false"}, "record-malformed"),
        ({"resume_count": "not-an-int"}, "record-malformed"),
    ],
)
def test_record_read_rejects_malformed_identity(record_fixture, change, code):
    data, path = record_fixture
    path.write_text(yaml.safe_dump({**data, **change}), encoding="utf-8")
    rc, result = invoke(data["worktree_id"])
    assert rc == 1 and result["error"]["code"] == code
    assert result["worktree"] is None


@pytest.mark.parametrize("raw", ["[", "- a", "", "worktree_id: [", "\x07", b"\xff"])
def test_record_read_bad_yaml_explicit(record_fixture, raw):
    data, path = record_fixture
    path.write_bytes(raw.encode("utf-8") if isinstance(raw, str) else raw)
    rc, result = invoke(data["worktree_id"])
    assert rc == 1 and result["error"]["code"] == "record-malformed"


def test_record_read_unreadable_explicit(record_fixture, monkeypatch):
    data, _ = record_fixture

    def unreadable(*args, **kwargs):
        raise PermissionError("synthetic permission refusal")

    monkeypatch.setattr(tracking, "_read_text_with_retry", unreadable)
    rc, result = invoke(data["worktree_id"])
    assert rc == 1 and result["error"]["code"] == "record-unreadable"


@pytest.mark.parametrize(
    "status", ["active", "complete", "pushed", "finalized", "orphaned", "archived"]
)
def test_record_read_terminal_and_historical_paths_are_metadata(record_fixture, status):
    data, path = record_fixture
    data["status"] = status
    data["worktree_path"] = r"C:\workspaces\example-worktree"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    rc, result = invoke(data["worktree_id"])
    assert rc == 0 and result["worktree"]["status"] == status
    assert result["worktree"]["path"] == data["worktree_path"]
    assert result["worktree"]["platform"] == "windows"


def test_record_read_repairs_control_characters_in_memory_only(record_fixture):
    data, path = record_fixture
    raw = path.read_text(encoding="utf-8") + "title: stray\x07character\n"
    path.write_text(raw, encoding="utf-8")
    before = path.read_bytes()
    rc, result = invoke(data["worktree_id"])
    assert rc == 0
    assert result["worktree"]["id"] == data["worktree_id"]
    assert tracking.load_record(path).title == "straycharacter"
    assert path.read_bytes() == before


def test_record_read_unindexed_sessions_are_unknown(record_fixture):
    data, path = record_fixture
    data.pop("sessions")
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    rc, result = invoke(data["worktree_id"])
    assert rc == 0
    assert result["worktree"]["session_count"] is None
    assert result["worktree"]["sessions_indexed"] is False


def test_record_read_reuses_head_ledger_and_preserves_execution_leg(record_fixture):
    data, path = record_fixture
    data.update(
        sessions=[{"session_id": "older-session"}, {"session_id": "newer-session"}],
        head_session="newer-session",
        head_transitions=[
            {"revision": 1, "session_id": "older-session", "reason": "registered", "at": ""}
        ],
        execution_leg={
            "version": 1,
            "provider": "example-provider",
            "state": "disposed",
            "binding_revision": 2,
            "blob": {"opaque_key": "opaque-value"},
        },
    )
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    rc, result = invoke(data["worktree_id"])
    assert rc == 0
    assert result["worktree"]["session_count"] == 2
    assert result["worktree"]["last_session_id"] == "older-session"
    assert result["worktree"]["execution_leg"] == data["execution_leg"]


@pytest.mark.parametrize(
    "ledger",
    [
        None, {}, "invalid", [None], [1], [{}],
        [{"revision": value} for value in (0, -1)],
        [{"revision": "invalid"}], [{"revision": True}], [{"revision": 1.5}],
        [{"revision": 1, "session_id": {}}],
        [{"revision": 1, "session_id": ""}],
        [{"revision": 1, "handoff_ordinal": "invalid"}],
        [{"revision": 1}, {"revision": 1}],
    ],
)
def test_record_read_rejects_malformed_head_ledger_without_fallback(record_fixture, ledger):
    data, path = record_fixture
    data.update(
        sessions=[{"session_id": "legacy-head"}],
        head_session="legacy-head",
        head_transitions=ledger,
    )
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    before = path.read_bytes()
    rc, result = invoke(data["worktree_id"])
    assert rc == 1 and result["error"]["code"] == "record-malformed"
    assert result["worktree"] is None
    assert path.read_bytes() == before


@pytest.mark.parametrize("ledger", ["absent", "empty", "cleared"])
def test_record_read_preserves_legacy_and_explicit_clear_head_semantics(record_fixture, ledger):
    data, path = record_fixture
    data.update(sessions=[{"session_id": "legacy-head"}], head_session="legacy-head")
    if ledger == "empty":
        data["head_transitions"] = []
    elif ledger == "cleared":
        data["head_transitions"] = [{"revision": 1, "session_id": None}]
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    rc, result = invoke(data["worktree_id"])
    assert rc == 0
    assert result["worktree"]["last_session_id"] == (
        None if ledger == "cleared" else "legacy-head"
    )


def test_record_read_rejects_malformed_session_state_without_fallback(record_fixture):
    data, path = record_fixture
    data["sessions"] = [{"session_id": "legacy-head", "state": "unknown-state"}]
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    rc, result = invoke(data["worktree_id"])
    assert rc == 1 and result["error"]["code"] == "record-malformed"


@pytest.mark.parametrize(
    "failure", [RuntimeError("project unavailable"), ValueError("invalid config")]
)
def test_record_read_project_unavailable_explicit(record_fixture, monkeypatch, failure):
    def unavailable(**kwargs):
        raise failure

    monkeypatch.setattr(cfg, "load_config", unavailable)
    rc, result = invoke("example-worktree")
    assert rc == 1 and result["error"]["code"] == "project-unavailable"


def test_record_read_opaque_execution_leg_is_not_disposed(record_fixture):
    data, path = record_fixture
    data["execution_leg"] = {"version": 99, "provider": "future-provider", "extra": "opaque"}
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    rc, result = invoke(data["worktree_id"])
    assert rc == 0
    assert result["worktree"]["execution_leg"] == data["execution_leg"]
    assert result["worktree"]["execution_leg_opaque"] is True


def test_record_read_opaque_legacy_backend_is_not_sessionless(record_fixture):
    data, path = record_fixture
    data["session_backend"] = {"version": 99, "kind": "future-backend"}
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    rc, result = invoke(data["worktree_id"])
    assert rc == 0
    assert result["worktree"]["execution_leg"] is None
    assert result["worktree"]["session_backend"] == data["session_backend"]
    assert result["worktree"]["session_backend_opaque"] is True


@pytest.mark.parametrize(
    ("field", "binding"),
    [
        ("execution_leg", {"version": 99, "provider": "future-provider"}),
        ("session_backend", {"version": 99, "kind": "future-backend"}),
        (
            "execution_leg",
            {"version": 1, "provider": "example-provider", "state": "unknown",
             "binding_revision": 1, "blob": {}},
        ),
    ],
)
def test_record_read_normalizes_nested_yaml_timestamps_without_writes(
    record_fixture, field, binding,
):
    data, path = record_fixture
    timestamp = datetime(2026, 1, 1, 12, 30, tzinfo=timezone.utc)
    calendar_day = date(2026, 1, 2)
    content = binding.get("blob", binding)
    content["future_metadata"] = {"values": [timestamp, {"day": calendar_day}]}
    data[field] = binding
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    before = path.read_bytes()
    rc, result = invoke(data["worktree_id"])
    assert rc == 0
    normalized = result["worktree"][field]
    metadata = normalized.get("blob", normalized)["future_metadata"]
    assert metadata == {"values": [timestamp.isoformat(), {"day": calendar_day.isoformat()}]}
    assert result["worktree"][f"{field}_opaque"] is (binding["version"] != 1)
    assert path.read_bytes() == before


@pytest.mark.parametrize("field", ["execution_leg", "session_backend"])
@pytest.mark.parametrize("key", [1, True, None, date(2026, 1, 1)])
def test_record_read_rejects_non_string_opaque_mapping_keys(record_fixture, field, key):
    data, path = record_fixture
    data[field] = {"version": 99, "nested": [{"1": "string value", key: "other value"}]}
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    before = path.read_bytes()
    rc, result = invoke(data["worktree_id"])
    assert rc == 1 and result["error"]["code"] == "record-malformed"
    assert "string keys" in result["error"]["message"]
    assert path.read_bytes() == before


def test_record_read_rejects_cyclic_opaque_yaml(record_fixture):
    data, path = record_fixture
    cyclic = {}
    cyclic["self"] = cyclic
    data["execution_leg"] = {"version": 99, "nested": cyclic}
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    rc, result = invoke(data["worktree_id"])
    assert rc == 1 and result["error"]["code"] == "record-malformed"
    assert "cyclic" in result["error"]["message"]


@pytest.mark.parametrize("value", [b"binary", {"set-value"}, float("nan"), float("inf")])
def test_record_read_rejects_other_non_json_opaque_values(record_fixture, value):
    data, path = record_fixture
    data["execution_leg"] = {"version": 99, "nested": value}
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    rc, result = invoke(data["worktree_id"])
    assert rc == 1 and result["error"]["code"] == "record-malformed"


def test_record_read_preserves_shared_noncyclic_opaque_yaml_values(record_fixture):
    data, path = record_fixture
    shared = {"text": "unchanged"}
    data["execution_leg"] = {"version": 99, "values": [shared, shared]}
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    rc, result = invoke(data["worktree_id"])
    assert rc == 0
    assert result["worktree"]["execution_leg"]["values"] == [shared, shared]


def test_record_read_legacy_backend_translation_is_not_written(record_fixture):
    data, path = record_fixture
    data["session_backend"] = {
        "version": 1,
        "kind": "ahp",
        "endpoint_url": "https://example.invalid/session",
        "session_id": "provider-session",
        "protocol_version": "1",
        "auth_account": "example",
        "created_at": datetime(2026, 1, 1),
        "last_seen_at": datetime(2026, 1, 1, 0, 0, 1),
        "state": "disposed",
        "binding_revision": 1,
    }
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    before = path.read_bytes()
    rc, result = invoke(data["worktree_id"])
    assert rc == 0
    assert result["worktree"]["execution_leg"]["provider"] == "ahp"
    assert result["worktree"]["execution_leg"]["state"] == "disposed"
    assert result["worktree"]["execution_leg"]["blob"]["session_id"] == "provider-session"
    assert isinstance(result["worktree"]["session_backend"]["created_at"], str)
    assert path.read_bytes() == before


def test_record_read_help_matches_full_lazy_parser(record_fixture, capsys):
    with pytest.raises(SystemExit) as fast:
        main._dispatch_lazy("record", ["record", "--help"])
    assert fast.value.code == 0
    fast_help = capsys.readouterr().out
    with pytest.raises(SystemExit) as full:
        main.build_parser().parse_args(["record", "--help"])
    assert full.value.code == 0
    assert capsys.readouterr().out == fast_help
    assert main.COMMAND_MAP["record"] is record_cli.cmd_record
