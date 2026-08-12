"""Tests for the declaration -> registration bridge + the daemon's declared source."""

from __future__ import annotations

from agent_dispatch.registrar import load_declaration
from agent_dispatch.registrar_reconcile import (
    declaration_to_registration,
    declaration_to_spec,
    declared_registration_id,
    declared_registrations,
    runs_on_machine,
)
from agent_dispatch.supervisor_daemon import SupervisorDaemon, build_command
from tests.test_supervisor_daemon import Clock, FakeClient, FakeLauncher

# -- converter ---------------------------------------------------------------

def test_spec_general_pool_headless():
    decl = load_declaration(
        {
            "name": "general",
            "labels": ["general"],
            "concurrency": 2,
            "body": {"type": "headless", "agent": "general-loop-worker"},
        }
    )
    spec = declaration_to_spec(decl)
    assert spec["all_repos"] is True
    assert spec["labels"] == ["general"]
    assert spec["max_concurrent"] == 2
    assert spec["headless_labels"] == ["general"]
    assert spec["headless_agent"] == "general-loop-worker"
    assert spec["heartbeat"] is True
    assert spec["reactive"] is True


def test_spec_lane_and_evaluator():
    decl = load_declaration({"name": "rev", "repos": "lane-a", "evaluator": "/e.py"})
    spec = declaration_to_spec(decl)
    assert spec["repo"] == "lane-a"
    assert "all_repos" not in spec
    assert spec["evaluator"] == "/e.py"


def test_declared_registration_id_stable_and_namespaced():
    decl = load_declaration({"name": "general", "owner": "repo:demo"})
    assert declared_registration_id(decl) == "declared:repo:demo:general"
    # no owner -> 'local'
    assert declared_registration_id(load_declaration({"name": "g"})) == "declared:local:g"


def test_declaration_to_registration_kind_and_scope():
    lane = declaration_to_registration(load_declaration({"name": "g"}), machine="host-a")
    assert lane["kind"] == "supervised-lane"
    assert lane["machine"] == "host-a"
    assert lane["status"] == "active"
    ev = declaration_to_registration(
        load_declaration({"name": "e", "evaluator": "/e.py"}), machine="host-a"
    )
    assert ev["kind"] == "evaluator"


def test_runs_on_machine_respects_permit_filter():
    pinned = load_declaration(
        {"name": "g", "filters": {"permit": {"machine": ["host-a"]}}}
    )
    assert runs_on_machine(pinned, "host-a") is True
    assert runs_on_machine(pinned, "host-b") is False
    assert runs_on_machine(pinned, None) is True  # a machineless daemon runs anything
    unpinned = load_declaration({"name": "g"})
    assert runs_on_machine(unpinned, "host-b") is True


def test_declared_registrations_filters_by_machine():
    decls = [
        load_declaration({"name": "here", "filters": {"permit": {"machine": ["host-a"]}}}),
        load_declaration({"name": "there", "filters": {"permit": {"machine": ["host-b"]}}}),
    ]
    regs = declared_registrations(decls, machine="host-a")
    assert [r["id"] for r in regs] == ["declared:local:here"]


def test_build_command_runs_a_converted_declaration():
    decl = load_declaration(
        {
            "name": "general",
            "labels": ["general"],
            "concurrency": 2,
            "heartbeat": False,
            "reactive_interval": 3.0,
            "body": {"type": "headless", "agent": "general-loop-worker"},
        }
    )
    reg = declaration_to_registration(decl, machine="host-a")
    cmd = build_command(reg)
    assert "supervise" in cmd
    assert cmd[cmd.index("--max-concurrent") + 1] == "2"
    assert "--headless-label" in cmd
    assert cmd[cmd.index("--headless-agent") + 1] == "general-loop-worker"
    # the extended lane flags a declaration can now carry:
    assert "--no-heartbeat" in cmd
    assert cmd[cmd.index("--reactive-interval") + 1] == "3.0"


# -- daemon: declared source feeds the reconcile -----------------------------

def _decl(name, **over):
    data = {"name": name}
    data.update(over)
    return load_declaration(data)


def test_daemon_starts_declared_units():
    launcher = FakeLauncher()
    holder = {"decls": [_decl("general", labels=["general"])]}
    daemon = SupervisorDaemon(
        FakeClient([]), "host-a", launcher=launcher, clock=Clock(),
        declared_source=lambda: holder["decls"],
    )
    summary = daemon.reconcile_once()
    assert summary.started == ["declared:local:general"]


def test_daemon_winds_down_vanished_declaration():
    launcher = FakeLauncher()
    holder = {"decls": [_decl("general")]}
    daemon = SupervisorDaemon(
        FakeClient([]), "host-a", launcher=launcher, clock=Clock(),
        declared_source=lambda: holder["decls"],
    )
    daemon.reconcile_once()
    holder["decls"] = []  # the declaration disappeared (repo unsynced / removed)
    summary = daemon.reconcile_once()
    assert summary.stopped == ["declared:local:general"]


def test_daemon_restarts_changed_declaration():
    launcher = FakeLauncher()
    holder = {"decls": [_decl("general", concurrency=1)]}
    daemon = SupervisorDaemon(
        FakeClient([]), "host-a", launcher=launcher, clock=Clock(),
        declared_source=lambda: holder["decls"],
    )
    daemon.reconcile_once()
    holder["decls"] = [_decl("general", concurrency=3)]  # concurrency changed
    summary = daemon.reconcile_once()
    assert summary.restarted == ["declared:local:general"]


def test_daemon_merges_store_and_declared_sources():
    launcher = FakeLauncher()
    store_reg = {
        "id": "store-lane",
        "kind": "supervised-lane",
        "spec": {"all_repos": True, "labels": ["x"]},
        "machine": "host-a",
        "env": "default",
        "status": "active",
    }
    daemon = SupervisorDaemon(
        FakeClient([store_reg]), "host-a", launcher=launcher, clock=Clock(),
        declared_source=lambda: [_decl("general")],
    )
    summary = daemon.reconcile_once()
    assert set(summary.started) == {"store-lane", "declared:local:general"}


def test_daemon_without_declared_source_is_store_only():
    launcher = FakeLauncher()
    daemon = SupervisorDaemon(FakeClient([]), "host-a", launcher=launcher, clock=Clock())
    summary = daemon.reconcile_once()
    assert summary.started == []


def test_daemon_excludes_machine_pinned_declaration():
    launcher = FakeLauncher()
    daemon = SupervisorDaemon(
        FakeClient([]), "host-a", launcher=launcher, clock=Clock(),
        declared_source=lambda: [
            _decl("there", filters={"permit": {"machine": ["host-b"]}})
        ],
    )
    summary = daemon.reconcile_once()
    assert summary.started == []  # pinned to another host


def test_daemon_uses_real_discover_source(tmp_path, monkeypatch):
    # End-to-end through the real discovery layer: register a pointer, and the
    # served daemon supervises what it declares.
    import json

    from agent_dispatch import registrar_discovery as rd

    monkeypatch.setenv(rd.REGISTRAR_DIR_ENV, str(tmp_path))
    launcher = FakeLauncher()
    daemon = SupervisorDaemon(
        FakeClient([]), "host-a", launcher=launcher, clock=Clock(),
        declared_source=rd.discover,
    )
    assert daemon.reconcile_once().started == []  # no pointers -> nothing declared

    decls = tmp_path / "d"
    decls.mkdir()
    (decls / "general.json").write_text(json.dumps({"name": "general"}), encoding="utf-8")
    rd.add_pointer("general", decls, base=tmp_path)
    assert daemon.reconcile_once().started == ["declared:pointer:general:general"]
