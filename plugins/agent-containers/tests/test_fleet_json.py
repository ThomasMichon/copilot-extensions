"""Tests for `agent-containers fleet --json` (picker Containers-pivot feed)."""

from __future__ import annotations

import argparse
import json

from agent_containers import __main__ as cli
from agent_containers.lifecycle import DockerContainerInfo


def _container(name, **kw):
    return DockerContainerInfo(
        name=name,
        container_id=kw.get("container_id", "cid-" + name),
        image=kw.get("image", "ghcr.io/example/dev:latest"),
        state=kw.get("state", "running"),
        status=kw.get("status", "Up 3 minutes"),
        fleet=kw.get("fleet", "myrepo"),
        local_folder=kw.get("local_folder", "/work/myrepo"),
    )


def _patch(monkeypatch, containers, lease_effort=None):
    monkeypatch.setattr(cli, "load_config", lambda: object())
    import agent_containers.lifecycle as lifecycle
    import agent_containers.lease as lease

    monkeypatch.setattr(lifecycle, "list_containers", lambda cfg: containers)

    class _Lease:
        effort = lease_effort

    monkeypatch.setattr(
        lease, "get_lease", lambda name: (_Lease() if lease_effort else None)
    )


def test_fleet_json_emits_bare_array_with_expected_fields(monkeypatch, capsys):
    _patch(monkeypatch, [_container("aperture-1")], lease_effort="my-effort")
    rc = cli._cmd_fleet(argparse.Namespace(json=True))
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert isinstance(data, list) and len(data) == 1          # bare top-level array
    row = data[0]
    assert set(row) == {
        "name", "container_id", "image", "state", "status",
        "fleet", "local_folder", "lease",
    }
    assert row["name"] == "aperture-1"
    assert row["state"] == "running"
    assert row["fleet"] == "myrepo"
    assert row["lease"] == "my-effort"


def test_fleet_json_lease_is_null_when_unheld(monkeypatch, capsys):
    _patch(monkeypatch, [_container("free-1")], lease_effort=None)
    cli._cmd_fleet(argparse.Namespace(json=True))
    data = json.loads(capsys.readouterr().out)
    assert data[0]["lease"] is None


def test_fleet_json_empty_is_empty_array(monkeypatch, capsys):
    _patch(monkeypatch, [])
    cli._cmd_fleet(argparse.Namespace(json=True))
    assert json.loads(capsys.readouterr().out) == []


def test_fleet_human_table_unaffected(monkeypatch, capsys):
    _patch(monkeypatch, [_container("aperture-1")], lease_effort="my-effort")
    cli._cmd_fleet(argparse.Namespace(json=False))
    out = capsys.readouterr().out
    assert "CONTAINER" in out and "aperture-1" in out       # still the table
    assert not out.lstrip().startswith("[")                  # not JSON
