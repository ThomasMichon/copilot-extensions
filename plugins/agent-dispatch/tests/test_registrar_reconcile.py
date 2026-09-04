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
    # headless is the default backend -> no per-label headless_labels, no
    # embody_backend pin; just the agent name.
    assert "headless_labels" not in spec
    assert "embody_backend" not in spec
    assert spec["headless_agent"] == "general-loop-worker"
    assert spec["heartbeat"] is True
    assert spec["reactive"] is False


def test_spec_and_command_carry_disposable_cli_labels():
    decl = load_declaration(
        {
            "name": "reviewers",
            "labels": ["review"],
            "body": {
                "type": "embody",
                "disposable_cli_labels": ["review"],
            },
        }
    )
    spec = declaration_to_spec(decl)
    assert spec["disposable_cli_labels"] == ["review"]
    command = build_command(
        declaration_to_registration(decl, machine="host-a"),
        python="PY",
    )
    assert command[command.index("--disposable-cli-label") + 1] == "review"


def test_spec_fleet_pool_origin_headless():
    # A fleet declaration (pool/origin/headless) must be carried into the lane spec;
    # otherwise the serve daemon drops it and the supervisor runs LOCAL (regression:
    # every fleet supervisor spawned a local body that 404'd on the origin task).
    decl = load_declaration(
        {
            "name": "lane-x",
            "labels": ["lane-x"],
            "concurrency": 1,
            "body": {"type": "headless", "agent": "worker"},
            "fleet": {
                "pool": ["host-b"],
                "origin": "host-a",
                "headless": True,
            },
        }
    )
    spec = declaration_to_spec(decl)
    assert spec["fleet"] == {
        "pool": ["host-b"],
        "origin": "host-a",
        "headless": True,
    }
    assert spec["headless_agent"] == "worker"


def test_build_command_emits_fleet_flags():
    # The daemon-built argv must carry --pool/--origin/--headless for a fleet lane
    # (mirrors ProfileDeclaration.to_supervise_args).
    decl = load_declaration(
        {
            "name": "lane-y",
            "labels": ["lane-y"],
            "concurrency": 1,
            "body": {"type": "headless", "agent": "worker"},
            "fleet": {
                "pool": ["host-b", "host-c"],
                "origin": "host-a",
                "headless": True,
            },
        }
    )
    reg = declaration_to_registration(decl, machine="host-a")
    cmd = build_command(reg)
    assert cmd[cmd.index("--pool") + 1] == "host-b,host-c"
    assert cmd[cmd.index("--origin") + 1] == "host-a"
    assert "--headless" in cmd
    assert cmd[cmd.index("--headless-agent") + 1] == "worker"


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


def test_periodic_emitter_declaration_maps_to_emitter_registration():
    decl = load_declaration(
        {
            "name": "review-inbox",
            "kind": "emitter",
            "spec": {
                "id": "review-inbox",
                "command": ["review-emitter", "tick"],
                "interval_seconds": 3600,
            },
        }
    )
    reg = declaration_to_registration(decl, machine="host-a")
    assert reg["kind"] == "emitter"
    assert reg["spec"] == dict(decl.spec)
    cmd = build_command(
        reg, python="PY", materialize=lambda name, _payload: f"/run/{name}.json"
    )
    assert cmd[:5] == ["PY", "-m", "agent_dispatch", "emitter", "serve"]
    assert cmd[cmd.index("--holder") + 1] == "host-a"


def test_plugin_companion_registration_preserves_runtime_revision():
    decl = load_declaration(
        {
            "name": "index-service",
            "kind": "plugin-companion",
            "spec": {
                "command": ["bin/serve"],
                "stop_command": ["bin/stop"],
                "health_probe": ["bin/health"],
                "managed_runtime": {
                    "schema_version": 1,
                    "runtimes": [
                        {
                            "name": "service",
                            "version": "2.0.0",
                            "profile": "host",
                            "python_env": "INDEX_MANAGED_PYTHON",
                            "projects": [{"path": ".", "extras": ["service"]}],
                            "imports": ["index_service"],
                        }
                    ],
                },
            },
        },
        allow_plugin_companion=True,
    ).with_owner("plugin@example").with_plugin_provenance(
        plugin_root="/plugins/index",
        source_path="/plugins/index/registrar/index.json",
        plugin_version="2.0.0",
        activation_scopes=("global", "project:demo"),
    )

    reg = declaration_to_registration(decl, machine="host-a")

    assert reg["plugin"] == {
        "root": "/plugins/index",
        "source_path": "/plugins/index/registrar/index.json",
        "version": "2.0.0",
        "activation_scopes": ["global", "project:demo"],
    }
    assert reg["runtime_revision"] == {
        "plugin_root": "/plugins/index",
        "plugin_owner": "plugin@example",
        "plugin_source_path": "/plugins/index/registrar/index.json",
        "plugin_version": "2.0.0",
        "activation_scopes": ["global", "project:demo"],
        "managed_runtime": {
            "schema_version": 1,
            "runtimes": [
                {
                    "name": "service",
                    "version": "2.0.0",
                    "profile": "host",
                    "python_env": "INDEX_MANAGED_PYTHON",
                    "projects": [{"path": ".", "extras": ["service"]}],
                    "imports": ["index_service"],
                }
            ],
        },
    }


def test_runs_on_machine_respects_permit_filter():
    pinned = load_declaration(
        {"name": "g", "filters": {"permit": {"machine": ["host-a"]}}}
    )
    assert runs_on_machine(pinned, "host-a") is True
    assert runs_on_machine(pinned, "host-b") is False
    # Fail closed: an unidentified host (machine=None) must NOT run a machine-pinned
    # declaration it cannot confirm membership of (aperture-labs #5001).
    assert runs_on_machine(pinned, None) is False
    unpinned = load_declaration({"name": "g"})
    assert runs_on_machine(unpinned, "host-b") is True
    # A machine-agnostic declaration still runs on an unidentified host.
    assert runs_on_machine(unpinned, None) is True


def test_runs_on_machine_fail_closed_on_reject_too():
    # A reject.machine constraint also makes the declaration machine-scoped, so an
    # unidentified host cannot evaluate it -> excluded.
    rejecting = load_declaration(
        {"name": "g", "filters": {"reject": {"machine": ["host-x"]}}}
    )
    assert runs_on_machine(rejecting, None) is False
    assert runs_on_machine(rejecting, "host-a") is True
    assert runs_on_machine(rejecting, "host-x") is False


def test_declared_registrations_excludes_machine_pinned_when_unidentified():
    decls = [
        load_declaration({"name": "pinned", "filters": {"permit": {"machine": ["host-a"]}}}),
        load_declaration({"name": "anywhere"}),
    ]
    regs = declared_registrations(decls, machine=None)
    # Only the machine-agnostic declaration survives an unidentified host.
    assert [r["id"] for r in regs] == ["declared:local:anywhere"]


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
    # headless-by-default lane: no --headless-label / --embody-backend, just the agent
    assert "--headless-label" not in cmd
    assert "--embody-backend" not in cmd
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


def test_equivalent_declaration_supersedes_direct_registration():
    launcher = FakeLauncher()
    decl = _decl("general", labels=["general"])
    declared = declaration_to_registration(decl, machine="host-a")
    direct_spec = {
        key: value
        for key, value in declared["spec"].items()
        if key not in {
            "heartbeat",
            "reactive",
            "reactive_interval",
            "headless_agent",
        }
    }
    direct = {
        **declared,
        "id": "general",
        "spec": direct_spec,
        "source": "direct",
    }
    daemon = SupervisorDaemon(
        FakeClient([direct]), "host-a", launcher=launcher, clock=Clock(),
        declared_source=lambda: [decl],
    )

    summary = daemon.reconcile_once()

    assert summary.started == ["declared:local:general"]
    assert summary.deduplicated == ["general"]
    assert summary.conflicts == []
    assert summary.running == ["declared:local:general"]


def test_equivalent_direct_registration_migrates_reversibly():
    launcher = FakeLauncher()
    decl = _decl("general", labels=["general"])
    declared = declaration_to_registration(decl, machine="host-a")
    direct = {**declared, "id": "general", "source": "direct"}
    declarations: list = []
    daemon = SupervisorDaemon(
        FakeClient([direct]), "host-a", launcher=launcher, clock=Clock(),
        declared_source=lambda: declarations,
    )
    assert daemon.reconcile_once().started == ["general"]

    declarations.append(decl)
    migrated = daemon.reconcile_once()
    assert migrated.stopped == ["general"]
    assert migrated.started == ["declared:local:general"]
    assert migrated.running == ["declared:local:general"]

    declarations.clear()
    restored = daemon.reconcile_once()
    assert restored.stopped == ["declared:local:general"]
    assert restored.started == ["general"]
    assert restored.running == ["general"]


def test_conflicting_direct_and_declared_specs_are_preserved_with_diagnostic(caplog):
    launcher = FakeLauncher()
    decl = _decl("general", concurrency=2)
    declared = declaration_to_registration(decl, machine="host-a")
    direct = {
        **declared,
        "id": "general",
        "logical_id": "general",
        "spec": {**declared["spec"], "max_concurrent": 1},
        "source": "direct",
    }
    daemon = SupervisorDaemon(
        FakeClient([direct]), "host-a", launcher=launcher, clock=Clock(),
        declared_source=lambda: [decl],
    )

    with caplog.at_level("WARNING", logger="agent-dispatch.supervisor-daemon"):
        summary = daemon.reconcile_once()

    assert set(summary.started) == {"general", "declared:local:general"}
    assert summary.deduplicated == []
    assert summary.conflicts == [
        "general <> declared:local:general (logical unit 'general'; specs differ)"
    ]
    assert "preserving both" in caplog.text


def test_direct_override_suppresses_equivalent_declared_replacement():
    decl = _decl("general", labels=["general"])
    declared = declaration_to_registration(decl, machine="host-a")
    direct = {
        **declared,
        "id": "general",
        "source": "direct",
    }
    daemon = SupervisorDaemon(
        FakeClient([direct]),
        "host-a",
        launcher=FakeLauncher(),
        clock=Clock(),
        declared_source=lambda: [decl],
        overrides_source=lambda: {
            "general": {"disabled": True, "reason": "operator stop"}
        },
    )

    summary = daemon.reconcile_once()

    assert summary.running == []
    assert summary.started == []
    assert summary.deduplicated == ["general"]


def test_malformed_direct_defaults_do_not_crash_reconciliation():
    decl = _decl("general", labels=["general"])
    declared = declaration_to_registration(decl, machine="host-a")
    direct = {
        **declared,
        "id": "general",
        "spec": {
            **declared["spec"],
            "interval": "not-a-number",
            "label_max_attempts": {"general": "not-a-number"},
        },
        "source": "direct",
    }
    daemon = SupervisorDaemon(
        FakeClient([direct]),
        "host-a",
        launcher=FakeLauncher(),
        clock=Clock(),
        declared_source=lambda: [decl],
    )

    summary = daemon.reconcile_once()

    assert set(summary.running) == {"general", "declared:local:general"}
    assert len(summary.conflicts) == 1


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


def test_daemon_preserves_declared_units_on_discovery_error():
    launcher = FakeLauncher()
    holder = {"decls": [_decl("general")], "fail": False}

    def _source():
        if holder["fail"]:
            raise RuntimeError("transient discovery blip")
        return holder["decls"]

    daemon = SupervisorDaemon(
        FakeClient([]), "host-a", launcher=launcher, clock=Clock(),
        declared_source=_source,
    )
    daemon.reconcile_once()  # started
    holder["fail"] = True
    summary = daemon.reconcile_once()  # discovery errors this tick
    assert summary.stopped == []  # last-known set kept -> not torn down
    assert "declared:local:general" in summary.running


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
