"""Bundled Podman driver controls; no real engine or registry is contacted."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from agent_containers import fleet, lifecycle, podman_driver, runtime_drivers
from agent_containers.config import FLEET_LABEL, ContainersConfig, FleetConfig
from agent_containers.runtime_drivers import DRIVER_LABEL, INSTANCE_LABEL, DriverError
from agent_containers.external_driver import ExternalDriver, DriverSpec


IMAGE_ID = "sha256:" + "a" * 64
MEMBER_ID = "b" * 64


def request(**changes):
    return {
        "protocolVersion": 1, "driver": "podman", "fleet": "example",
        "driverOptions": {"image": "configured-alias"},
        **changes,
    }


def document(*, fleet="example", driver="podman", member_id=MEMBER_ID):
    return {
        "Id": member_id, "Name": "example-1", "Image": IMAGE_ID,
        "State": {"Status": "running"},
        "Config": {"Labels": {
            FLEET_LABEL: fleet, DRIVER_LABEL: driver, INSTANCE_LABEL: "example-1",
        }},
    }


@pytest.fixture
def engine(monkeypatch):
    calls = []

    def run(argv, *, timeout, input_text=None, **kwargs):
        calls.append((argv, input_text, timeout))
        if "exists" in argv:
            return subprocess.CompletedProcess(argv, 1, "", "")
        if "pull" in argv:
            return subprocess.CompletedProcess(argv, 0, IMAGE_ID + "\n", "")
        if argv[1:3] == ["image", "inspect"]:
            return subprocess.CompletedProcess(argv, 0, json.dumps([{"Id": IMAGE_ID}]), "")
        if "inspect" in argv:
            return subprocess.CompletedProcess(argv, 0, json.dumps([document()]), "")
        return subprocess.CompletedProcess(argv, 0, MEMBER_ID + "\n", "")

    monkeypatch.setattr(podman_driver, "run_caller_owned", run)
    monkeypatch.setattr(podman_driver.shutil, "which", lambda name: name)
    monkeypatch.setattr(podman_driver, "_resolve_executable", lambda name: name)
    return calls


def provision(driver):
    return driver.handle("provision", {
        "member": {"name": "example-1", "fleet": "example", "instance": "example-1"},
        "workspaceFolder": "/workspaces/example", "execUser": "vscode",
        "environment": {"EXAMPLE": "literal; value"},
    })


def test_configured_image_is_resolved_once_and_run_by_immutable_id(engine):
    result = provision(podman_driver.PodmanDriver(request()))
    assert result["member"]["id"] == MEMBER_ID
    pull = next(argv for argv, _, _ in engine if "pull" in argv)
    assert pull == ["podman", "pull", "--policy=missing", "--quiet", "configured-alias"]
    create = next(argv for argv, _, _ in engine if "run" in argv)
    assert "--pull=never" in create
    assert IMAGE_ID in create and "configured-alias" not in create
    assert "--replace" not in create
    assert all(stdin is None for _, stdin, _ in engine)


@pytest.mark.parametrize("code, output", [
    (125, ""), (0, ""), (0, IMAGE_ID + "\n" + IMAGE_ID),
])
def test_pull_failure_never_creates_or_publishes_member(engine, monkeypatch, code, output):
    original = podman_driver.run_caller_owned

    def fail(argv, **kwargs):
        if "pull" not in argv:
            return original(argv, **kwargs)
        engine.append((argv, None, kwargs["timeout"]))
        return subprocess.CompletedProcess(argv, code, output, "unresolved source/authentication")

    monkeypatch.setattr(podman_driver, "run_caller_owned", fail)
    with pytest.raises(podman_driver.PodmanError):
        provision(podman_driver.PodmanDriver(request()))
    assert any("pull" in argv for argv, _, _ in engine)
    assert not any("run" in argv for argv, _, _ in engine)


@pytest.mark.parametrize("options", [
    {}, {"image": ""}, {"image": "--bad"}, {"image": "x", "pull_policy": "newer"},
    {"image": "x", "credentials": "not-allowed"}, {"image": "x", "connection": 1},
])
def test_invalid_options_fail_before_engine(options, engine):
    with pytest.raises(podman_driver.PodmanError, match="driverOptions"):
        podman_driver.PodmanDriver(request(driverOptions=options))
    assert engine == []


def test_selected_executable_connection_and_incarnation(engine):
    driver = podman_driver.PodmanDriver(request(driverOptions={
        "image": "configured-alias", "executable": "/tools/podman",
        "connection": "configured-machine",
    }))
    result = driver.handle("inspect", {"name": "example-1", "id": MEMBER_ID})
    assert result["member"]["fleet"] == "example"
    assert engine[0][0][:3] == ["/tools/podman", "--connection", "configured-machine"]


@pytest.mark.parametrize("changed", [
    {"fleet": "foreign"}, {"driver": "other"}, {"member_id": "c" * 64},
])
def test_foreign_or_recreated_member_is_never_mutated(engine, monkeypatch, changed):
    monkeypatch.setattr(podman_driver, "run_caller_owned", lambda argv, **_: (
        engine.append((argv, None, 0))
        or subprocess.CompletedProcess(argv, 0, json.dumps([document(**changed)]), "")
    ))
    driver = podman_driver.PodmanDriver(request())
    with pytest.raises(podman_driver.PodmanError):
        driver.handle("stop", {"name": "example-1", "id": MEMBER_ID})
    assert len(engine) == 1 and "inspect" in engine[0][0]


@pytest.mark.parametrize("state, command", [
    ("paused", "unpause"), ("exited", "start"), ("created", "start"),
])
def test_start_uses_state_specific_command_on_pinned_member(engine, monkeypatch, state, command):
    original = podman_driver.run_caller_owned

    def inspect_state(argv, **kwargs):
        if "inspect" in argv:
            row = document()
            row["State"]["Status"] = state
            engine.append((argv, None, kwargs["timeout"]))
            return subprocess.CompletedProcess(argv, 0, json.dumps([row]), "")
        return original(argv, **kwargs)

    monkeypatch.setattr(podman_driver, "run_caller_owned", inspect_state)
    podman_driver.PodmanDriver(request()).handle("start", {
        "name": "example-1", "id": MEMBER_ID,
    })
    assert [argv for argv, _, _ in engine] == [
        ["podman", "container", "inspect", MEMBER_ID], ["podman", command, MEMBER_ID],
    ]


def test_failed_unpause_is_not_replayed_as_start(engine, monkeypatch):
    def fail(argv, **kwargs):
        engine.append((argv, None, kwargs["timeout"]))
        if "inspect" in argv:
            row = document()
            row["State"]["Status"] = "paused"
            return subprocess.CompletedProcess(argv, 0, json.dumps([row]), "")
        return subprocess.CompletedProcess(argv, 125, "", "synthetic unpause failure")

    monkeypatch.setattr(podman_driver, "run_caller_owned", fail)
    with pytest.raises(podman_driver.PodmanError, match="did not replay"):
        podman_driver.PodmanDriver(request()).handle("start", {
            "name": "example-1", "id": MEMBER_ID,
        })
    assert [argv for argv, _, _ in engine] == [
        ["podman", "container", "inspect", MEMBER_ID], ["podman", "unpause", MEMBER_ID],
    ]


def test_creation_failure_is_not_replayed_or_removed(engine, monkeypatch):
    original = podman_driver.run_caller_owned

    def fail_creation(argv, **kwargs):
        if "run" in argv:
            engine.append((argv, None, kwargs["timeout"]))
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
        return original(argv, **kwargs)

    monkeypatch.setattr(podman_driver, "run_caller_owned", fail_creation)
    with pytest.raises(podman_driver.PodmanError, match="retained state"):
        provision(podman_driver.PodmanDriver(request()))
    assert len([argv for argv, _, _ in engine if "run" in argv]) == 1
    assert not any("rm" in argv for argv, _, _ in engine)


def test_existing_name_blocks_pull_and_creation(engine, monkeypatch):
    monkeypatch.setattr(podman_driver, "run_caller_owned", lambda argv, **_: (
        engine.append((argv, None, 0)) or subprocess.CompletedProcess(argv, 0, "", "")
    ))
    with pytest.raises(podman_driver.PodmanError, match="replacement is forbidden"):
        provision(podman_driver.PodmanDriver(request()))
    assert len(engine) == 1


def test_inventory_rejects_missing_or_foreign_rows(engine, monkeypatch):
    def missing(argv, **_):
        return subprocess.CompletedProcess(
            argv, 0, MEMBER_ID if "ps" in argv else "[]", "",
        )

    monkeypatch.setattr(podman_driver, "run_caller_owned", missing)
    with pytest.raises(podman_driver.PodmanError, match="inventory changed"):
        podman_driver.PodmanDriver(request()).handle("list", {})


def test_exec_preserves_literal_argv_and_stdin_and_targets_id(engine):
    result = podman_driver.PodmanDriver(request()).handle("exec", {
        "name": "example-1", "id": MEMBER_ID,
        "argv": ["/bin/echo", "literal; $value", "", "--argument"],
        "env": {"EXAMPLE": "value; with spaces"}, "user": "vscode",
        "workdir": "/workspaces/example", "stdin": "input\n", "timeoutSeconds": 12,
    })
    argv, stdin, timeout = engine[-1]
    assert argv == [
        "podman", "exec", "--interactive", "--user", "vscode",
        "--workdir", "/workspaces/example", "--env", "EXAMPLE=value; with spaces",
        MEMBER_ID, "/bin/echo", "literal; $value", "", "--argument",
    ]
    assert (stdin, timeout, result["exitCode"]) == ("input\n", 12, 0)


@pytest.mark.parametrize("timeout", [0, -1, True, float("inf"), float("nan")])
def test_exec_refuses_invalid_deadlines(engine, timeout):
    with pytest.raises(podman_driver.PodmanError, match="timeoutSeconds"):
        podman_driver.PodmanDriver(request()).handle("exec", {
            "name": "example-1", "argv": ["true"], "timeoutSeconds": timeout,
        })
    assert not any("exec" in argv for argv, _, _ in engine)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX exec replacement boundary")
def test_stream_keeps_inherited_values_out_of_argv(engine, monkeypatch):
    captured = []
    class Replaced(Exception):
        pass

    def replace(exe, argv):
        captured.append(argv)
        raise Replaced()

    monkeypatch.setenv("FIXTURE_SECRET", "synthetic-only")
    monkeypatch.setattr(podman_driver.os, "execvp", replace)
    with pytest.raises(Replaced):
        podman_driver.PodmanDriver(request()).handle("exec-stream", {
            "name": "example-1", "id": MEMBER_ID, "argv": ["cat"],
            "inheritEnv": ["FIXTURE_SECRET"],
        })
    assert captured == [[
        "podman", "exec", "--interactive", "--env", "FIXTURE_SECRET", MEMBER_ID, "cat",
    ]]
    assert "synthetic-only" not in repr(captured)


def test_invalid_protocol_real_cli_refuses_before_native_lookup():
    selected = FleetConfig(driver="podman", driver_options={"image": "example"})
    # This crosses the actual CLI/parser boundary without needing a native engine.
    driver = ExternalDriver(DriverSpec(
        "podman", (sys.executable, "-m", "agent_containers.podman_driver"), "bundled",
    ), "example", selected)
    original = driver._request
    driver._request = lambda payload: {**original(payload), "protocolVersion": 2}
    with pytest.raises(DriverError) as raised:
        driver.call("capabilities")
    assert raised.value.code == "unsupported_protocol"


def test_real_provider_and_driver_process_roundtrip(tmp_path, monkeypatch):
    """Cross provider -> bundled CLI -> fake native executable, without a real engine."""
    root = tmp_path / "engine"
    root.mkdir()
    script = Path(__file__).with_name("fake_podman.py").read_text(encoding="utf-8")
    for verb in (
        "container", "image", "pull", "run", "ps", "start", "unpause", "stop", "rm", "exec", "cp",
    ):
        (root / verb).write_text(script, encoding="utf-8")
    monkeypatch.chdir(root)
    monkeypatch.setenv("PODMAN_TEST_ROOT", str(root))
    monkeypatch.setenv("AGENT_CONTAINERS_DRIVERS_DIR", str(root / "drivers"))
    registered = root / "drivers"
    registered.mkdir()
    (registered / "podman.json").write_text(json.dumps({
        "schema_version": 1, "name": "podman",
        "command": [sys.executable, "-m", "agent_containers.podman_driver"],
    }), encoding="utf-8")
    original_which = podman_driver.shutil.which
    monkeypatch.setattr(
        podman_driver.shutil, "which",
        lambda executable: str(tmp_path / "unapproved-driver")
        if executable == "agent-containers-driver-podman" else original_which(executable),
    )
    monkeypatch.setattr(lifecycle, "_docker", lambda *_args, **_kw: (_ for _ in ()).throw(
        RuntimeError("Docker is not part of this fixture"),
    ))
    selected = FleetConfig(
        # The fake native engine is stdlib-only, not another plugin consumer.
        driver="podman", driver_options={
            "image": "configured-alias", "executable": sys._base_executable,
        },
        size=1, workspace_folder="/workspaces/example", exec_user="vscode",
    )
    config = ContainersConfig(fleets={"example": selected})
    runtime_drivers.clear_bindings()
    try:
        assert fleet.reconcile_up(config, "example").created == ["example-1"]
        [member] = lifecycle.list_containers(config)
        assert (member.driver, member.container_id, member.state) == ("podman", MEMBER_ID, "running")
        driver = runtime_drivers.driver_for_fleet(config, "example", selected)
        assert driver.capabilities.ssh_transport and not driver.capabilities.owns_relay
        plan = runtime_drivers.launch_plan(selected, "example-1", True, require_ssh=True)
        assert plan.ssh and plan.agent_relay
        outcome = driver.exec(
            "example-1", ["fixture-echo"], stdin="literal input", instance_id=MEMBER_ID,
        )
        assert (outcome.exit_code, outcome.stdout) == (7, "literal input\n")
        source = root / "literal file.txt"
        source.write_text("fixture", encoding="utf-8")
        driver.copy_in("example-1", source, "/workspaces/example/file.txt", instance_id=MEMBER_ID)
        assert fleet.down_fleet(config, "example").stopped == ["example-1"]
        [stopped] = lifecycle.list_containers(config)
        assert stopped.state == "exited"
        assert fleet.start(config, "example") == ["example-1"]
        state = json.loads((root / "state.json").read_text(encoding="utf-8"))
        state["members"][MEMBER_ID]["State"]["Status"] = "paused"
        (root / "state.json").write_text(json.dumps(state), encoding="utf-8")
        assert fleet.start(config, "example") == ["example-1"]
        assert lifecycle.list_containers(config)[0].state == "running"
        with pytest.raises(DriverError) as changed:
            driver.stop("example-1", instance_id="c" * 64)
        assert changed.value.code == "conflict"
        assert fleet.remove_fleet(config, "example", force=True).removed == ["example-1"]
        state = json.loads((root / "state.json").read_text())
        assert state["members"] == {}
        assert len([call for call in state["calls"] if call[0] == "run"]) == 1
        assert next(call for call in state["calls"] if call[0] == "stop")[-1] == MEMBER_ID
        assert [call for call in state["calls"] if call[0] == "unpause"] == [
            ["unpause", MEMBER_ID],
        ]
    finally:
        runtime_drivers.clear_bindings()
