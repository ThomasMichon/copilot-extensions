"""Tests for `agent-containers fleet --json` (picker Containers-pivot feed)."""

from __future__ import annotations

import argparse
import json

from agent_containers import __main__ as cli
from agent_containers.config import ContainersConfig, FleetConfig
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
        security_profile=kw.get("security_profile", "trusted"),
        security_policy=kw.get("security_policy"),
    )


def _patch(monkeypatch, containers, lease_effort=None):
    config = ContainersConfig()
    config.fleets["myrepo"] = FleetConfig()
    monkeypatch.setattr(cli, "load_config", lambda: config)
    import agent_containers.lifecycle as lifecycle
    import agent_containers.lease as lease

    monkeypatch.setattr(lifecycle, "list_containers", lambda cfg: containers)
    monkeypatch.setattr(
        lifecycle,
        "inspect_container",
        lambda name: {
            "Config": {
                "Labels": {"agent-containers.security-profile": "trusted"}
            }
        },
    )

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
        "fleet", "local_folder", "lease", "security_profile",
        "configured_security_profile", "security_policy_current",
        "security_policy_errors", "network", "host_credentials",
    }
    assert row["name"] == "aperture-1"
    assert row["state"] == "running"
    assert row["fleet"] == "myrepo"
    assert row["lease"] == "my-effort"
    assert row["security_profile"] == "trusted"
    assert row["configured_security_profile"] == "trusted"
    assert row["security_policy_current"] is True
    assert row["host_credentials"] == {
        "github_token": True,
        "relay": True,
    }


def test_fleet_json_lease_is_null_when_unheld(monkeypatch, capsys):
    _patch(monkeypatch, [_container("free-1")], lease_effort=None)
    cli._cmd_fleet(argparse.Namespace(json=True))
    data = json.loads(capsys.readouterr().out)
    assert data[0]["lease"] is None


def test_fleet_json_empty_is_empty_array(monkeypatch, capsys):
    _patch(monkeypatch, [])
    cli._cmd_fleet(argparse.Namespace(json=True))
    assert json.loads(capsys.readouterr().out) == []


def test_fleet_json_reports_restricted_posture(monkeypatch, capsys):
    config = ContainersConfig()
    config.fleets["myrepo"] = FleetConfig(
        security_profile="restricted",
        network="model-only",
    )
    monkeypatch.setattr(cli, "load_config", lambda: config)
    import agent_containers.lifecycle as lifecycle
    import agent_containers.lease as lease

    monkeypatch.setattr(
        lifecycle,
        "list_containers",
        lambda cfg: [
            _container(
                "restricted-1",
                security_profile="restricted",
                security_policy=config.fleets["myrepo"].security_policy_fingerprint(
                    config.workspace_folder,
                    config.exec_user,
                ),
            )
        ],
    )
    monkeypatch.setattr(lifecycle, "restricted_policy_errors", lambda *a, **k: [])
    monkeypatch.setattr(
        lifecycle,
        "inspect_container",
        lambda name: {
            "Config": {
                "Labels": {"agent-containers.security-profile": "restricted"}
            },
            "HostConfig": {"NetworkMode": "model-only"},
        },
    )
    monkeypatch.setattr(lease, "get_lease", lambda name: None)

    cli._cmd_fleet(argparse.Namespace(json=True))
    row = json.loads(capsys.readouterr().out)[0]
    assert row["security_profile"] == "restricted"
    assert row["configured_security_profile"] == "restricted"
    assert row["security_policy_current"] is True
    assert row["network"] == "model-only"
    assert row["host_credentials"] == {
        "github_token": False,
        "relay": False,
    }


def test_fleet_json_exposes_stale_restricted_policy(monkeypatch, capsys):
    config = ContainersConfig()
    config.fleets["myrepo"] = FleetConfig(security_profile="restricted")
    monkeypatch.setattr(cli, "load_config", lambda: config)
    import agent_containers.lifecycle as lifecycle
    import agent_containers.lease as lease

    monkeypatch.setattr(
        lifecycle,
        "list_containers",
        lambda cfg: [
            _container(
                "stale-1",
                security_profile="restricted",
                security_policy="old-policy",
            )
        ],
    )
    monkeypatch.setattr(
        lifecycle,
        "restricted_policy_errors",
        lambda *a, **k: ["security policy fingerprint is stale"],
    )
    monkeypatch.setattr(
        lifecycle,
        "inspect_container",
        lambda name: {
            "Config": {
                "Labels": {"agent-containers.security-profile": "restricted"}
            }
        },
    )
    monkeypatch.setattr(lease, "get_lease", lambda name: None)

    cli._cmd_fleet(argparse.Namespace(json=True))
    row = json.loads(capsys.readouterr().out)[0]
    assert row["security_profile"] == "restricted"
    assert row["security_policy_current"] is False


def test_fleet_human_table_unaffected(monkeypatch, capsys):
    _patch(monkeypatch, [_container("aperture-1")], lease_effort="my-effort")
    cli._cmd_fleet(argparse.Namespace(json=False))
    out = capsys.readouterr().out
    assert "CONTAINER" in out and "aperture-1" in out       # still the table
    assert not out.lstrip().startswith("[")                  # not JSON
