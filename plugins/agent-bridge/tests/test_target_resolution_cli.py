"""Ordinary CLI consumers use the union without creating a replacement."""

from __future__ import annotations

from argparse import Namespace

import json
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from agent_bridge import __main__ as main_module
from agent_bridge.client import BridgeClientError
from agent_bridge.send_outcome import SendRefused
from agent_bridge.session_start_cli import _resolve_read_target, _resolve_read_worktree_session
from agent_bridge.target_resolution_cli import HydrationOutcomeUnknown, resolve_workspace

cli: ModuleType = main_module


def _client(*targets: Any, sources: Any=None, status: Any="stopped") -> Any:
    calls = []
    observations = {
        "managed": {"availability": "available"},
        "session_host": {"availability": "available"},
        "extension": {"availability": "available"},
        "worktrees": {"completeness": "complete"},
        **(sources or {}),
    }

    def missing(target: Any) -> Any:
        raise BridgeClientError(404, "missing")

    def hydrate(target: Any) -> Any:
        calls.append(("hydrate", target))
        return {
            "session_id": "control", "worktree_id": target, "status": status,
            "acp_session_id": "native", "durable_session_id": "native",
        }

    def session(target: Any) -> Any:
        if target == "control":
            return hydrate_result(target)
        return missing(target)

    def hydrate_result(_target: Any) -> Any:
        return {
            "session_id": "control", "status": status, "worktree_id": "workspace",
            "agent_name": "owner", "project": "demo", "target_dir": "/workspace/demo",
        }

    client = SimpleNamespace(
        list_targets=lambda **kwargs: {
            "version": 1, "targets": list(targets),
            "sources": observations,
        },
        hydrate_worktree=hydrate, get_session=session,
        list_sessions=lambda: [], calls=calls,
    )
    return client


def _workspace(owner: Any="owner", path: Any="/workspace/demo") -> Any:
    return {
        "kind": "worktree", "handle": "workspace", "owner": owner, "project": "demo",
        "worktree": {"id": "workspace", "path": path},
    }

@pytest.mark.parametrize("json_output", [False, True])
def test_sessions_complete_catalog_retains_every_inventory_kind_without_hydration(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    json_output: bool,
) -> None:
    from agent_bridge.session_maintenance_cli import _cmd_sessions

    client = _client(
        _workspace(),
        {"kind": "live", "handle": "anchor-native", "registration": {"worktree_id": None}},
        {"kind": "session_host", "handle": "standalone-host"},
        {"kind": "managed", "handle": "managed-native"},
    )
    client_calls = []

    def get_client(**kwargs: Any) -> Any:
        client_calls.append(kwargs)
        return client

    monkeypatch.setattr(cli, "_get_client", get_client)
    _cmd_sessions(Namespace(json=json_output, status=None, owned_only=False, all_sources=json_output))
    output = capsys.readouterr().out
    expected = {
        ("worktree", "workspace"), ("live", "anchor-native"),
        ("session_host", "standalone-host"), ("managed", "managed-native"),
    }
    if json_output:
        result = json.loads(output)
        assert {(item["kind"], item["handle"]) for item in result["targets"]} == expected
        assert result["sources"]["worktrees"]["completeness"] == "complete"
    else:
        assert all(f"{handle}  [{kind}]" in output for kind, handle in expected)
    assert client.calls == []
    assert client_calls == [{"ensure": True}]


def test_sessions_status_selection_retains_source_evidence(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    from agent_bridge.session_maintenance_cli import _cmd_sessions

    client = _client(
        {"kind": "managed", "handle": "owned", "session": {"status": "idle"}},
        {"kind": "live", "handle": "cli", "registration": {"status": "live", "turn_state": "idle"}},
        _workspace(),
    )
    monkeypatch.setattr(cli, "_get_client", lambda **kwargs: client)
    _cmd_sessions(Namespace(json=True, status="idle", all_sources=True))
    result = json.loads(capsys.readouterr().out)
    assert [item["handle"] for item in result["targets"]] == ["owned", "cli"]
    assert result["sources"]["extension"]["availability"] == "available"
    assert client.calls == []


@pytest.mark.parametrize("status", ["stopped", "ended", "failed"])
@pytest.mark.parametrize("json_output", [False, True])
def test_explicit_terminal_status_includes_historical_managed_sessions_once(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    status: str, json_output: bool,
) -> None:
    from agent_bridge.session_maintenance_cli import _cmd_sessions

    retained = {"session_id": "history", "status": status}
    client = _client(
        {"kind": "managed", "handle": "existing", "session": {"session_id": "existing", "status": status}},
        {"kind": "managed", "handle": "live", "session": {"session_id": "live", "status": "idle"}},
    )
    calls = []

    def sessions(**kwargs: Any) -> list[dict[str, str]]:
        calls.append(kwargs)
        return [retained, {"session_id": "existing", "status": status}]

    client.list_sessions = sessions
    monkeypatch.setattr(cli, "_get_client", lambda **kwargs: client)
    _cmd_sessions(Namespace(json=json_output, status=status, all_sources=json_output))
    output = capsys.readouterr().out
    if json_output:
        result = json.loads(output)
        assert [row["handle"] for row in result["targets"]] == ["existing", "history"]
        assert result["sources"]["managed"]["availability"] == "available"
    else:
        assert output.splitlines() == ["existing  [managed]", "history  [managed]"]
    assert calls == [{"status": status}]
    assert client.calls == []


@pytest.mark.parametrize("owned_only", [False, True])
def test_sessions_json_default_and_owned_only_preserve_the_legacy_contract(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    owned_only: bool,
) -> None:
    from agent_bridge.session_maintenance_cli import _cmd_sessions

    calls = []
    def sessions(**kwargs: Any) -> list[dict[str, str]]:
        calls.append(kwargs)
        return [{"session_id": "original"}]

    client = SimpleNamespace(
        list_sessions=sessions,
        list_targets=lambda **kwargs: pytest.fail("explicit legacy selection must not query inventory"),
    )
    monkeypatch.setattr(cli, "_get_client", lambda **kwargs: client)
    _cmd_sessions(Namespace(json=True, status="stopped", owned_only=owned_only))
    assert json.loads(capsys.readouterr().out) == [{"session_id": "original"}]
    assert calls == [{"status": "stopped"}]


def test_sessions_cannot_silently_report_legacy_rows_as_complete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_bridge.session_maintenance_cli import _cmd_sessions

    client = _client()
    client.list_targets = lambda **kwargs: (_ for _ in ()).throw(BridgeClientError(426, "unsupported"))
    monkeypatch.setattr(cli, "_get_client", lambda **kwargs: client)
    with pytest.raises(BridgeClientError) as refused:
        _cmd_sessions(Namespace(json=True, status=None, all_sources=True))
    assert refused.value.status == 426


def test_read_workspace_without_row_hydrates_without_resume() -> None:
    client = _client(_workspace())
    assert _resolve_read_target(client, "workspace") == ("control", "workspace")
    assert client.calls == [("hydrate", "workspace")]


@pytest.mark.parametrize("target", ["workspace", "control"])
def test_send_dormant_target_requires_explicit_resume(target: Any) -> None:
    client = _client(_workspace())
    with pytest.raises(SendRefused) as refused:
        cli._resolve_target(client, target)
    assert refused.value.reason == "resume_required"
    assert client.calls == ([("hydrate", "workspace")] if target == "workspace" else [])


def test_send_ready_workspace_uses_hydrated_control() -> None:
    client = _client(_workspace(), status="idle")
    assert cli._resolve_target(client, "workspace") == "control"
    assert client.calls == [("hydrate", "workspace")]


@pytest.mark.parametrize("kind", ["managed", "live", "session_host"])
def test_unresolved_registration_never_falls_through_to_creation(kind: Any) -> None:
    client = _client({"kind": kind, "handle": "registered"})
    with pytest.raises(BridgeClientError) as refused:
        cli._resolve_target(client, "registered")
    assert refused.value.detail["reason"] == "registered_transport_unavailable"
    assert client.calls == []


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("target", [
    "codespace:example", "container:example", "custom:example",
    "product@machine", "https://example.com/acp",
])
def test_explicit_provider_routing_does_not_depend_on_workspace_discovery(
    monkeypatch: pytest.MonkeyPatch, target: str, legacy: bool,
) -> None:
    client = _client(sources={"worktrees": {"completeness": "incomplete"}})
    if legacy:
        client.list_targets = lambda: (_ for _ in ()).throw(BridgeClientError(426, "unsupported"))

    def forbidden() -> Any:
        raise AssertionError("an explicit provider is not a legacy workspace lookup")

    client.list_worktrees_for_render = forbidden
    delegated = []

    def start(_client: Any, requested: str, **_kwargs: Any) -> str:
        delegated.append(requested)
        return "provider-control"

    monkeypatch.setattr(cli, "_start_agent_session", start)
    assert cli._resolve_target(client, target) == "provider-control"
    assert delegated == [target]
    assert client.calls == []


@pytest.mark.parametrize("kind", ["managed", "live", "session_host"])
def test_known_registration_still_precedes_explicit_provider_routing(kind: str) -> None:
    client = _client(
        {"kind": kind, "handle": "codespace:example"},
        sources={"worktrees": {"completeness": "incomplete"}},
    )
    with pytest.raises(BridgeClientError) as refused:
        cli._resolve_target(client, "codespace:example")
    assert refused.value.detail["reason"] == "registered_transport_unavailable"
    assert client.calls == []


def test_colliding_workspace_owners_refuse_before_hydration() -> None:
    client = _client(_workspace(), _workspace("other", "/other/demo"))
    with pytest.raises(BridgeClientError) as refused:
        resolve_workspace(client, "workspace")
    assert refused.value.detail["reason"] == "ambiguous_workspace"
    assert client.calls == []


@pytest.mark.parametrize("source", ["worktrees", "session_host", "extension"])
def test_incomplete_discovery_cannot_authorize_fresh_profile(source: Any) -> None:
    client = _client(sources={source: {"availability": "unavailable"}})
    with pytest.raises(BridgeClientError) as refused:
        cli._resolve_target(client, "unknown")
    assert refused.value.detail["reason"] == "target_inventory_incomplete"
    assert client.calls == []


def test_unsupported_workspace_reports_contract_gap_without_old_effect() -> None:
    client = _client()
    client.list_targets = lambda: (_ for _ in ()).throw(BridgeClientError(426, "unsupported"))
    client.list_worktrees_for_render = lambda: {
        "groups": {"owner": [_workspace()["worktree"]]},
        "discovery": {"completeness": "complete"},
    }
    with pytest.raises(BridgeClientError) as refused:
        cli._resolve_target(client, "workspace")
    assert refused.value.status == 426
    assert client.calls == []


def test_older_receiver_keeps_existing_managed_workspace_reads() -> None:
    client = _client()
    client.list_targets = lambda: (_ for _ in ()).throw(BridgeClientError(426, "unsupported"))
    def forbidden() -> Any:
        raise AssertionError("positive managed reads need no discovery")

    client.list_worktrees_for_render = forbidden
    client.list_sessions = lambda: [{
        "session_id": "control", "worktree_id": "workspace", "status": "idle",
    }]
    assert _resolve_read_target(client, "workspace") == ("control", "workspace")
    assert client.calls == []


def test_hydration_response_loss_is_not_replayed_or_fallen_back() -> None:
    from agent_bridge.client import BridgeConnectionError

    client = _client(_workspace())

    def lost(target: Any) -> Any:
        client.calls.append(("hydrate", target))
        raise BridgeConnectionError("response lost")

    client.hydrate_worktree = lost
    with pytest.raises(HydrationOutcomeUnknown):
        cli._resolve_target(client, "workspace")
    assert client.calls == [("hydrate", "workspace")]


@pytest.mark.parametrize("completeness", ["unknown", "incomplete", None])
def test_legacy_warming_or_incomplete_inventory_never_falls_through(completeness: str | None) -> None:
    client = _client()
    client.list_targets = lambda: (_ for _ in ()).throw(BridgeClientError(426, "unsupported"))
    client.list_worktrees_for_render = lambda: {
        "groups": {}, "discovery": {"completeness": completeness},
    }
    with pytest.raises(BridgeClientError) as refused:
        cli._resolve_target(client, "unknown")
    assert refused.value.detail["reason"] == "target_inventory_incomplete"
    assert client.calls == []


def test_visible_unqualified_workspace_needs_complete_discovery() -> None:
    client = _client(_workspace(), sources={"worktrees": {"completeness": "incomplete"}})
    with pytest.raises(BridgeClientError) as refused:
        resolve_workspace(client, "workspace")
    assert refused.value.detail["reason"] == "target_inventory_incomplete"
    assert client.calls == []


def test_qualified_managed_projection_reads_without_head_hydration() -> None:
    item = _workspace()
    item["worktree"]["session_id"] = "control"
    client = _client(item)

    def forbidden(_target: str) -> Any:
        raise AssertionError("existing history must not depend on authoritative-head availability")

    client.hydrate_worktree = forbidden
    assert _resolve_read_target(client, "workspace") == ("control", "workspace")
    assert client.calls == []


def test_missing_projected_record_uses_metadata_owner() -> None:
    item = _workspace()
    item["worktree"]["session_id"] = "evicted"
    client = _client(item)
    assert _resolve_read_target(client, "workspace") == ("control", "workspace")
    assert client.calls == [("hydrate", "workspace")]


def test_changed_projected_owner_neither_reuses_nor_hydrates() -> None:
    item = _workspace()
    item["worktree"]["session_id"] = "control"
    client = _client(item)
    client.get_session = lambda _target: {
        "session_id": "control", "worktree_id": "workspace", "agent_name": "foreign",
        "project": "demo", "target_dir": "/workspace/demo",
    }
    with pytest.raises(BridgeClientError) as refused:
        resolve_workspace(client, "workspace")
    assert refused.value.detail["reason"] == "workspace_projection_changed"
    assert client.calls == []


def test_excluded_predecessor_does_not_fall_back_to_older_inactive_history() -> None:
    item = _workspace()
    item["worktree"]["session_id"] = "control"
    client = _client(item)
    record = client.get_session("control")
    older = {**record, "session_id": "older", "status": "stopped"}
    client.get_session = lambda sid: {"control": record, "older": older}[sid]
    client.list_sessions = lambda: [record, older]
    assert _resolve_read_worktree_session(
        client, "workspace", exclude_session_id="control",
    ) is None
    assert client.calls == []


@pytest.mark.parametrize("detail", ["controlled server failure", {"reason": "owner_unavailable"}])
def test_hydration_server_failure_is_terminal_with_its_actual_reason(detail: Any) -> None:
    client = _client(_workspace())

    def failed(target: str) -> Any:
        client.calls.append(("hydrate", target))
        raise BridgeClientError(503, detail)

    client.hydrate_worktree = failed
    with pytest.raises(SystemExit) as held:
        resolve_workspace(client, "workspace")
    if isinstance(detail, dict):
        assert not isinstance(held.value, HydrationOutcomeUnknown)
        assert "owner_unavailable" in str(held.value)
    else:
        assert isinstance(held.value, HydrationOutcomeUnknown)
    assert client.calls == [("hydrate", "workspace")]


def test_uncertain_handoff_hydration_escapes_outer_reconnect_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_bridge.client import BridgeConnectionError
    from agent_bridge.session_streaming_cli import _stream_feed

    client = _client(_workspace())
    reads = []
    client.get_cursor = lambda *_a, **_kw: 0
    client.read_range = lambda *_a, **_kw: []
    client.ack_cursor = lambda *_a, **_kw: None
    client.refresh_endpoint = lambda: pytest.fail("uncertain hydration cannot reconnect and replay")

    def events(*_args: Any, **_kwargs: Any) -> Any:
        reads.append("stream")
        assert len(reads) == 1, "handoff following replayed its outer stream"
        return iter([
            {"id": "1", "event": "session_handoff", "data": {"successor_id": "successor"}},
            {"id": "2", "event": "turn_complete", "data": {}},
        ])

    def lost(target: str) -> Any:
        client.calls.append(("hydrate", target))
        raise BridgeConnectionError("controlled reply loss")

    client.stream_events = events
    client.hydrate_worktree = lost
    monkeypatch.setattr(cli, "_STREAM_404_GRACE_S", 0)
    with pytest.raises(HydrationOutcomeUnknown):
        _stream_feed(
            client, "control", caller_id=None, follow_handle="workspace",
            renderer=SimpleNamespace(render_event=lambda *_args: ""),
        )
    assert client.calls == [("hydrate", "workspace")]
    assert reads == ["stream"]


def test_targets_parser_preserves_union_and_source_observations(monkeypatch: Any, capsys: Any) -> None:
    client = _client(
        _workspace(), {"kind": "live", "handle": "registered"},
        sources={"session_host": {"availability": "unavailable"}},
    )
    calls = []

    def get_client(**kwargs: Any) -> Any:
        calls.append(kwargs)
        return client

    monkeypatch.setattr(cli, "_get_client", get_client)
    args = cli.build_parser().parse_args(["targets", "--json"])
    args.func(args)
    result = json.loads(capsys.readouterr().out)
    assert [item["kind"] for item in result["targets"]] == ["worktree", "live"]
    assert result["sources"]["session_host"]["availability"] == "unavailable"
    assert calls == [{"ensure": False}]
    assert client.calls == []
