"""Both ordinary and streaming CLI rosters respect remote contribution scopes."""

from types import SimpleNamespace

import pytest

from agent_bridge import inventory_cli
from agent_bridge.client import BridgeClient


def test_cli_keeps_unbound_remote_visible_and_does_not_leak_multi_project_target(monkeypatch, capsys):
    rows = [
        {
            "name": "remote-acp:shared", "target_type": "remote-acp",
            "project": None, "projects": ["demo", "another"], "global_scope": False,
            "state": "requires-binding", "unavailable_reason": "instance-binding-required",
        },
        {
            "name": "remote-acp:foreign", "target_type": "remote-acp",
            "project": None, "projects": ["foreign", "elsewhere"], "global_scope": False,
            "state": "bound",
        },
        {
            "name": "remote-acp:global", "target_type": "remote-acp",
            "project": None, "projects": [], "global_scope": True, "state": "bound",
        },
        {"name": "local", "target_type": "local", "project": "demo"},
    ]
    emitted = []
    requests = []
    client = BridgeClient.__new__(BridgeClient)
    client.daemon_supports = lambda version: True
    client._request = lambda *args, **kwargs: requests.append((args, kwargs)) or {
        "agents": rows, "incomplete_namespaces": [],
    }
    core = SimpleNamespace(
        _get_client=lambda: client, _PROJECT_OVERRIDE="demo", _PROJECT_ROUTED=False,
        _sender_repo=lambda: "demo", _json_out=emitted.append,
    )
    monkeypatch.setattr(inventory_cli, "_core", lambda: core)
    args = SimpleNamespace(all_projects=False, json=True, stream=False)
    inventory_cli._cmd_agents(args)
    expected = ["remote-acp:shared", "remote-acp:global", "local"]
    assert [row["name"] for row in emitted[0]] == expected
    assert emitted[0][0]["state"] == "requires-binding"
    streamed, incomplete, known = inventory_cli._fetch_agent_rows(args)
    assert [row["name"] for row in streamed] == expected and not incomplete and known
    args.json = False
    inventory_cli._cmd_agents(args)
    output = capsys.readouterr().out
    assert "requires-binding" in output and "instance-binding-required" in output
    assert "remote-acp:foreign" not in output
    assert all(kwargs["params"]["project"] == "demo" for _args, kwargs in requests)


def test_remote_cli_detail_transmits_project_to_real_client(monkeypatch):
    requests, emitted = [], []
    client = BridgeClient.__new__(BridgeClient)
    client.daemon_supports = lambda version: True
    client._request = lambda *args, **kwargs: requests.append(args) or {"state": "bound"}
    monkeypatch.setattr(inventory_cli, "_core", lambda: SimpleNamespace(
        _get_client=lambda: client, _sender_repo=lambda: "demo",
        _json_out=emitted.append,
    ))
    inventory_cli._cmd_agent_show(SimpleNamespace(
        name="remote-acp:assistant", json=True, include_unaddressable=True,
    ))
    assert requests == [("GET", "/api/v1/agents/remote-acp:assistant?include_unaddressable=true&project=demo")]
    assert emitted == [{"state": "bound"}]


@pytest.mark.parametrize("version", [38, 40, 41])
def test_roster_project_is_only_sent_to_capable_daemon(version):
    requests = []
    client = BridgeClient.__new__(BridgeClient)
    client.daemon_supports = lambda required: version >= required
    client._request = lambda *args, **kwargs: requests.append(kwargs) or {"agents": []}
    client.list_agents_with_diagnostics(project="demo")
    assert requests == [{"params": {"project": "demo"} if version == 41 else None}]
