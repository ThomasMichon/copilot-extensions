"""Task-only provider admission and secret-free retained projection contracts."""

from __future__ import annotations

import io
import json
import shlex
import subprocess
import threading
import time
from dataclasses import replace
from types import SimpleNamespace

import pytest

from agent_containers import dispatch_projection as projection
from agent_containers import lease, lifecycle, resolver
from agent_containers.config import ContainersConfig, DISPATCH_BINDING_LABEL, FleetConfig, RESTRICTED_PROFILE

PHYSICAL_ID = "a" * 64
OTHER_ID = "b" * 64
FACTS = {
    "task_id": "task-1", "worker_id": "worker-1", "generation": 3,
    "coordinator_epoch": 2, "coordinator_url": "http://coordinator:8080",
}


@pytest.fixture
def target(monkeypatch, tmp_path):
    for name, filename in (
        ("_LOCK_FILE", "leases.lock"),
        ("_DEPLOY_HOLDS_FILE", "holds.json"),
        ("_SESSION_ADMISSIONS_FILE", "admissions.json"),
    ):
        monkeypatch.setattr(lease, name, tmp_path / filename)
    monkeypatch.setattr(lease, "ensure_state_dir", lambda: None)
    monkeypatch.setattr(projection, "_STATE", tmp_path / "bindings")
    projection.register_birth(PHYSICAL_ID, "c" * 32)
    projection.register_birth(OTHER_ID, "d" * 32)
    fleet = FleetConfig(
        image="worker-image", security_profile=RESTRICTED_PROFILE, dispatch_only=True,
    )
    target = SimpleNamespace(
        name="pool-1", container_id=PHYSICAL_ID, fleet=fleet,
        actual_profile=RESTRICTED_PROFILE, user="1000",
    )
    monkeypatch.setattr(resolver, "resolve_live_exec_target", lambda *args, **kwargs: target)
    monkeypatch.setattr(lifecycle, "inspect_container", lambda name: {
        "Id": name, "Config": {"Labels": {
            DISPATCH_BINDING_LABEL: ("c" if name == PHYSICAL_ID else "d") * 32,
        }},
    })
    monkeypatch.setattr(
        projection, "resolve_executable",
        lambda *args, kind, **kwargs: (f"/usr/bin/{kind}", "/home/worker"),
    )
    monkeypatch.setattr(projection, "_initialize_worker_lock", lambda *args, **kwargs: None)
    monkeypatch.setattr(projection, "_initialize_lock", lambda *args, **kwargs: None)
    return target


def grant(binding, **updates):
    return {
        "binding_id": binding["binding_id"], "owner_session_id": "native-1",
        "task_token": "task-credential", "expires_at": time.time() + 500, **updates,
    }


def test_prepare_is_idempotent_and_never_reuses_physical_incarnation(target):
    first = projection.prepare("pool-1", PHYSICAL_ID, FACTS)
    assert projection.prepare("pool-1", PHYSICAL_ID, FACTS) == first
    for name, value in (
        ("task_id", "other"), ("worker_id", "other"), ("generation", 4),
        ("coordinator_epoch", 3), ("coordinator_url", "http://other:8080"),
    ):
        with pytest.raises(RuntimeError, match="another task incarnation"):
            projection.prepare("pool-1", PHYSICAL_ID, {**FACTS, name: value})
    assert projection.verify_binding(PHYSICAL_ID, first["binding_id"]) == first
    target.container_id = OTHER_ID
    second = projection.prepare("pool-1", OTHER_ID, {**FACTS, "task_id": "other"})
    assert second["binding_id"] != first["binding_id"]


@pytest.mark.parametrize("updates", [
    {"generation": True}, {"generation": -1}, {"coordinator_epoch": "2"},
    {"task_id": ""}, {"worker_id": "bad\nidentity"}, {"extra": "field"},
    {"coordinator_url": "http://user:password@coordinator"},
    {"coordinator_url": "http://coordinator:invalid"},
    {"coordinator_url": "http://coordinator:0"},
    {"coordinator_url": "http://coordinator?"}, {"coordinator_url": "http://coordinator#"},
])
def test_invalid_preparation_does_not_seal_container(target, updates):
    with pytest.raises(RuntimeError):
        projection.prepare("pool-1", PHYSICAL_ID, {**FACTS, **updates})
    assert projection._read(PHYSICAL_ID)["task_id"] is None


def test_wrong_identity_or_non_dedicated_fleet_cannot_be_prepared(target):
    with pytest.raises(RuntimeError, match="exact dispatch-only"):
        projection.prepare("pool-1", OTHER_ID, FACTS)
    target.fleet.dispatch_only = False
    with pytest.raises(RuntimeError, match="exact dispatch-only"):
        projection.prepare("pool-1", PHYSICAL_ID, FACTS)


def test_existing_session_or_lifecycle_hold_prevents_sealing(target):
    with lease.session_admission("pool-1"):
        with pytest.raises(RuntimeError, match="busy"):
            projection.prepare("pool-1", PHYSICAL_ID, FACTS)
    with lease.deploy_hold("pool-1", "remove"):
        with pytest.raises(RuntimeError, match="busy"):
            projection.prepare("pool-1", PHYSICAL_ID, FACTS)


def test_only_one_provider_worker_is_admitted(target):
    binding = projection.prepare("pool-1", PHYSICAL_ID, FACTS)
    options = {"dispatch_container_id": PHYSICAL_ID, "dispatch_binding_id": binding["binding_id"]}
    with lease.session_admission("pool-1", **options):
        with pytest.raises(lease.ProviderAdmissionError, match="already has"):
            with lease.session_admission("pool-1", **options):
                pytest.fail("second worker admitted")
    with pytest.raises(lease.ProviderAdmissionError, match="does not match"):
        with lease.session_admission("pool-1", **{**options, "dispatch_binding_id": "wrong"}):
            pytest.fail("wrong binding admitted")


def test_rotation_delivers_only_stdin_secret_and_retains_nonsecret_fence(target, monkeypatch):
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(projection.subprocess, "run", run)
    binding = projection.prepare("pool-1", PHYSICAL_ID, FACTS)
    initial = grant(binding)
    published = projection.project("pool-1", PHYSICAL_ID, initial)
    rotated = projection.project(
        "pool-1", PHYSICAL_ID,
        grant(binding, task_token="rotated-credential", expires_at=initial["expires_at"] + 100),
    )
    assert published["owner_session_id"] == rotated["owner_session_id"] == "native-1"
    assert len(calls) == 2
    for (argv, options), token in zip(calls, ("task-credential", "rotated-credential")):
        assert options["input"] == token and options["capture_output"]
        assert token not in json.dumps(argv)
        assert argv[:3] == ["docker", "exec", "-i"]
        assert "-u" in argv and argv[argv.index("-u") + 1] == "0"
        assert "/usr/bin/node" in argv[-1] and PHYSICAL_ID in argv
        assert projection.PUBLICATION_LOCK in argv[-1]
        assert "--conflict-exit-code 75" in argv[-1]
    persisted = projection._path(PHYSICAL_ID).read_text(encoding="utf-8")
    assert "task-credential" not in persisted and "rotated-credential" not in persisted
    assert set(projection.environment(rotated)) == {
        "AGENT_DISPATCH_TASK_ID", "AGENT_DISPATCH_TASK_WORKER_ID",
        "AGENT_DISPATCH_TASK_URL", "AGENT_DISPATCH_TASK_TOKEN_FILE",
    }
    with pytest.raises(RuntimeError, match="cannot be rebound"):
        projection.project("pool-1", PHYSICAL_ID, grant(binding, owner_session_id="native-other"))
    with pytest.raises(RuntimeError, match="backwards"):
        projection.project("pool-1", PHYSICAL_ID, initial)
    assert len(calls) == 2


def test_failed_publication_is_sanitized_and_preserves_native_fence(target, monkeypatch):
    binding = projection.prepare("pool-1", PHYSICAL_ID, FACTS)
    monkeypatch.setattr(
        projection.subprocess, "run",
        lambda argv, **kwargs: subprocess.CompletedProcess(argv, 1, kwargs["input"], kwargs["input"]),
    )
    with pytest.raises(RuntimeError, match="delivery failed") as error:
        projection.project("pool-1", PHYSICAL_ID, grant(binding))
    assert "task-credential" not in str(error.value)
    retained = projection._read(PHYSICAL_ID)
    assert retained["owner_session_id"] == "native-1" and retained["expires_at"] is None
    with pytest.raises(RuntimeError, match="cannot be rebound"):
        projection.project("pool-1", PHYSICAL_ID, grant(binding, owner_session_id="native-other"))


@pytest.mark.parametrize("failure", ["timeout", "final-write"])
def test_ambiguous_publication_fences_expiry_before_delivery(target, monkeypatch, failure):
    binding = projection.prepare("pool-1", PHYSICAL_ID, FACTS)
    initial = grant(binding)
    monkeypatch.setattr(
        projection.subprocess, "run", lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0),
    )
    projection.project("pool-1", PHYSICAL_ID, initial)
    newer = {**initial, "task_token": "newer-credential", "expires_at": initial["expires_at"] + 100}
    delivered = []
    write = projection.atomic_write_json

    def run(argv, **kwargs):
        delivered.append(kwargs["input"])
        retained = projection._read(PHYSICAL_ID)
        assert retained["attempted_expires_at"] == newer["expires_at"]
        assert retained["expires_at"] == initial["expires_at"]
        if failure == "timeout":
            raise subprocess.TimeoutExpired(argv, 30)
        return subprocess.CompletedProcess(argv, 0)

    def fail_final_write(path, record):
        if record["expires_at"] == newer["expires_at"]:
            raise OSError("interrupted final acknowledgement")
        write(path, record)

    monkeypatch.setattr(projection.subprocess, "run", run)
    monkeypatch.setattr(projection, "atomic_write_json", fail_final_write)
    with pytest.raises((RuntimeError, OSError)):
        projection.project("pool-1", PHYSICAL_ID, newer)
    assert delivered == ["newer-credential"]
    with pytest.raises(RuntimeError, match="backwards"):
        projection.project("pool-1", PHYSICAL_ID, initial)
    assert delivered == ["newer-credential"]
    monkeypatch.setattr(projection, "atomic_write_json", write)
    monkeypatch.setattr(
        projection.subprocess, "run", lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0),
    )
    retried = projection.project("pool-1", PHYSICAL_ID, newer)
    assert retried["expires_at"] == retried["attempted_expires_at"] == newer["expires_at"]
    assert "newer-credential" not in projection._path(PHYSICAL_ID).read_text(encoding="utf-8")


def test_legacy_binding_preserves_delivered_expiry_floor(target, monkeypatch):
    binding = projection.prepare("pool-1", PHYSICAL_ID, FACTS)
    monkeypatch.setattr(
        projection.subprocess, "run", lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0),
    )
    request = grant(binding)
    published = projection.project("pool-1", PHYSICAL_ID, request)
    published.pop("attempted_expires_at")
    projection.atomic_write_json(projection._path(PHYSICAL_ID), published)
    assert projection._read(PHYSICAL_ID)["attempted_expires_at"] == request["expires_at"]
    with pytest.raises(RuntimeError, match="backwards"):
        projection.project("pool-1", PHYSICAL_ID, {**request, "expires_at": request["expires_at"] - 1})


@pytest.mark.parametrize("expiry", [True, float("inf"), -1, "123", 123])
def test_invalid_attempt_fence_is_not_available_authority(target, expiry):
    binding = projection.prepare("pool-1", PHYSICAL_ID, FACTS)
    binding["attempted_expires_at"] = expiry
    projection.atomic_write_json(projection._path(PHYSICAL_ID), binding)
    with pytest.raises(RuntimeError, match="binding is invalid"):
        projection._read(PHYSICAL_ID)


@pytest.mark.parametrize("state,expected", [(0, "idle"), (75, "active"), (1, "unknown")])
def test_guest_worker_probe_is_fail_closed(target, monkeypatch, state, expected):
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, state, "", "untrusted diagnostic")

    monkeypatch.setattr(projection.subprocess, "run", run)
    result = projection.probe_worker_liveness(PHYSICAL_ID, {"Id": PHYSICAL_ID})
    assert result.state == expected
    assert "--conflict-exit-code 75" in calls[0][-1]
    assert projection.WORKER_LOCK in calls[0][-1]
    assert "untrusted diagnostic" not in (result.reason or "")


@pytest.mark.parametrize("error", [OSError("failed"), subprocess.TimeoutExpired("probe", 30)])
def test_guest_worker_probe_transport_failure_is_unknown(target, monkeypatch, error):
    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(projection.subprocess, "run", fail)
    assert projection.probe_worker_liveness(PHYSICAL_ID, {"Id": PHYSICAL_ID}).state == "unknown"


@pytest.mark.parametrize("reason", ["provider session admission is active", "container lifecycle hold"])
def test_task_launch_maps_provider_busy_to_exit_75(monkeypatch, capsys, reason):
    from agent_containers import __main__ as cli

    def busy(*args):
        raise lease.ProviderAdmissionError(reason)

    monkeypatch.setattr(projection, "_launch_task", busy)
    args = SimpleNamespace(dispatch_binding_id="binding", expected_container_id=PHYSICAL_ID)
    assert cli._cmd_exec(args) == 75
    assert reason in capsys.readouterr().err


@pytest.mark.parametrize("updates", [
    {"task_token": ""}, {"task_token": "secret\n"}, {"task_token": "x" * 8193},
    {"expires_at": True}, {"expires_at": float("inf")}, {"expires_at": 0},
    {"owner_session_id": ""}, {"extra": "field"},
])
def test_invalid_grant_does_not_bind_native_identity(target, updates):
    binding = projection.prepare("pool-1", PHYSICAL_ID, FACTS)
    with pytest.raises(RuntimeError):
        projection.project("pool-1", PHYSICAL_ID, grant(binding, **updates))
    assert projection._read(PHYSICAL_ID)["owner_session_id"] is None


def test_unreadable_state_never_appears_available(target):
    binding = projection.prepare("pool-1", PHYSICAL_ID, FACTS)
    projection._path(PHYSICAL_ID).write_text("{bad", encoding="utf-8")
    with pytest.raises(RuntimeError, match="unreadable"):
        projection.prepare("pool-1", PHYSICAL_ID, FACTS)
    with pytest.raises(RuntimeError, match="unreadable"):
        projection.verify_binding(PHYSICAL_ID, binding["binding_id"])


def test_missing_birth_authority_or_forged_birth_label_never_allows_reassignment(target, monkeypatch):
    projection._path(PHYSICAL_ID).unlink()
    with pytest.raises(RuntimeError, match="birth authority is missing"):
        projection.prepare("pool-1", PHYSICAL_ID, FACTS)
    projection.register_birth(PHYSICAL_ID, "e" * 32)
    with pytest.raises(RuntimeError, match="birth identity does not match"):
        projection.prepare("pool-1", PHYSICAL_ID, FACTS)


def test_worker_argv_contains_nonsecret_projection_and_immutable_bash(target):
    binding = projection.prepare("pool-1", PHYSICAL_ID, FACTS)
    argv = projection.build_spawn_command(
        target, binding, "copilot --acp --stdio", worker_admission=(1, "e" * 32),
    )
    assert argv[:3] == ["docker", "exec", "-i"]
    assert argv[-6:-1] == [PHYSICAL_ID, "/usr/bin/bash", "--noprofile", "--norc", "-c"]
    assert f"exec 9<{projection.WORKER_LOCK}" in argv[-1]
    assert "/usr/bin/flock --nonblock 9 || exit 75" in argv[-1]
    assert shlex.quote(projection._CHECK_WORKER_ADMISSION) in argv[-1]
    assert "copilot --acp --stdio" in argv[-1]
    assert f"AGENT_DISPATCH_TASK_TOKEN_FILE={projection.TOKEN_FILE}" in argv
    assert "AGENT_DISPATCH_TASK_TOKEN" not in argv
    assert "GH_TOKEN" not in argv and "AGENT_DISPATCH_CONTROL_TOKEN" not in argv


def test_worker_admission_generation_advances_before_guest_publication(target):
    binding = projection.prepare("pool-1", PHYSICAL_ID, FACTS)
    inspected = lifecycle.inspect_container(PHYSICAL_ID)
    with lease.session_admission(
        "pool-1", dispatch_container_id=PHYSICAL_ID, dispatch_binding_id=binding["binding_id"],
    ) as admission:
        first = projection._reserve_worker_admission(
            "pool-1", PHYSICAL_ID, inspected, admission_token=admission.token,
        )
        assert first[0] == 1 and len(first[1]) == 32
    with lease.deploy_hold("pool-1", "stop") as hold:
        closed = projection._reserve_worker_admission(
            "pool-1", PHYSICAL_ID, inspected, hold_token=hold.token,
        )
        assert closed == (2, "")
        assert projection._read(PHYSICAL_ID)["worker_admission_generation"] == 2
        with pytest.raises(RuntimeError, match="admission changed"):
            projection._reserve_worker_admission(
                "pool-1", PHYSICAL_ID, inspected, admission_token=first[1],
            )
    with lease.session_admission(
        "pool-1", dispatch_container_id=PHYSICAL_ID, dispatch_binding_id=binding["binding_id"],
    ) as admission:
        reopened = projection._reserve_worker_admission(
            "pool-1", PHYSICAL_ID, inspected, admission_token=admission.token,
        )
        assert reopened[0] == 3 and reopened[1] != first[1]
    assert projection._read(PHYSICAL_ID)["task_id"] == binding["task_id"]


def test_executable_reuse_is_scoped_to_user_kind_and_immutable_instance_snapshot(target, monkeypatch):
    import copy
    calls = []
    monkeypatch.setattr(
        projection, "resolve_executable",
        lambda container, user, inspected, **kwargs: (
            calls.append((container, user, kwargs["kind"])) or ("/usr/bin/node", "/home/worker")
        ),
    )
    inspected = lifecycle.inspect_container(PHYSICAL_ID)
    inspected.update(
        State={"StartedAt": "generation-1"}, HostConfig={"ReadonlyRootfs": True}, Mounts=[],
    )
    cache = {}
    def lookup(snapshot=inspected, user="0", kind="node", memo=cache):
        return projection._worker_executable(
            PHYSICAL_ID, user, snapshot, kind=kind, deadline=time.monotonic() + 30,
            executables=memo,
        )
    assert lookup() == lookup()
    assert len(calls) == 1
    lookup(user="node")
    lookup(kind="bash")
    lookup(memo={})
    for field, value in (
        ("State", {"StartedAt": "generation-2"}),
        ("Config", {"Labels": {"different": "configuration"}}),
        ("HostConfig", {"ReadonlyRootfs": False}),
        ("Mounts", [{"Destination": "/usr/bin"}]),
        ("Id", OTHER_ID),
    ):
        changed = copy.deepcopy(inspected)
        changed[field] = value
        lookup(changed)
    assert len(calls) == 9


@pytest.mark.parametrize("generation", [-1, True, "1", 2**53])
def test_invalid_worker_admission_generation_fails_closed(target, generation):
    binding = projection.prepare("pool-1", PHYSICAL_ID, FACTS)
    binding["worker_admission_generation"] = generation
    projection.atomic_write_json(projection._path(PHYSICAL_ID), binding)
    with pytest.raises(RuntimeError, match="binding is invalid"):
        projection._read(PHYSICAL_ID)


@pytest.mark.parametrize("code,expected", [(0, "idle"), (75, "active"), (1, "unknown")])
def test_closing_guest_admission_requires_exact_lifecycle_hold(target, monkeypatch, code, expected):
    calls = []
    monkeypatch.setattr(
        projection.subprocess, "run",
        lambda argv, **kwargs: calls.append(argv) or subprocess.CompletedProcess(argv, code),
    )
    inspected = lifecycle.inspect_container(PHYSICAL_ID)
    with lease.deploy_hold("pool-1", "stop") as hold:
        result = projection.close_worker_admission(
            "pool-1", PHYSICAL_ID, inspected, hold_token=hold.token,
        )
    assert result.state == expected
    assert shlex.quote(projection._WRITE_WORKER_ADMISSION) in calls[0][-1]
    assert "--conflict-exit-code 75" in calls[0][-1]
    assert projection.close_worker_admission(
        "pool-1", PHYSICAL_ID, inspected, hold_token="missing",
    ).state == "unknown"


def test_cli_projection_output_never_includes_token(target, monkeypatch, capsys):
    import sys

    binding = projection.prepare("pool-1", PHYSICAL_ID, FACTS)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(grant(binding))))
    monkeypatch.setattr(projection.subprocess, "run", lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0))
    args = SimpleNamespace(name="pool-1", expected_container_id=PHYSICAL_ID)
    assert projection._command(args, projection.project) == 0
    output = capsys.readouterr().out
    assert "task-credential" not in output
    result = json.loads(output)
    assert result["task_token_file"] == projection.TOKEN_FILE
    assert result["spawn_command"][-7:] == [
        "exec", "--stdio", "--dispatch-binding-id", binding["binding_id"],
        "--expected-container-id", PHYSICAL_ID, "pool-1",
    ]


def test_returned_task_command_parses_appended_copilot_arguments(target, monkeypatch, capsys):
    import sys
    from agent_containers import __main__ as cli

    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(FACTS)))
    args = SimpleNamespace(name="pool-1", expected_container_id=PHYSICAL_ID)
    assert projection._command(args, projection.prepare) == 0
    command = json.loads(capsys.readouterr().out)["spawn_command"]
    binding = projection._read(PHYSICAL_ID)
    launches = []
    monkeypatch.setattr(
        projection, "launch_task", lambda args, run_stdio: launches.append(args) or 0,
    )
    assert cli.main(command[command.index("exec"):] + ["--", "--agent", "worker"]) == 0
    assert len(launches) == 1
    parsed = launches[0]
    assert (parsed.name, parsed.dispatch_binding_id, parsed.expected_container_id) == (
        "pool-1", binding["binding_id"], PHYSICAL_ID,
    )
    assert parsed.copilot_args == ["--agent", "worker"]


@pytest.mark.parametrize("raw", ["{bad", "x" * 16385, "\udcff"])
def test_invalid_cli_bytes_never_reach_projection(target, monkeypatch, raw):
    import sys

    monkeypatch.setattr(sys, "stdin", io.StringIO(raw))
    args = SimpleNamespace(name="pool-1", expected_container_id=PHYSICAL_ID)
    with pytest.raises(RuntimeError):
        projection._command(args, lambda *args: pytest.fail("invalid input reached projection"))


def test_corrupt_birth_schema_never_allows_preparation(target):
    record = projection._read(PHYSICAL_ID)
    record["version"] = True
    projection._path(PHYSICAL_ID).write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(RuntimeError, match="binding is invalid"):
        projection.prepare("pool-1", PHYSICAL_ID, FACTS)


def test_dispatch_only_is_explicit_restricted_creation_policy():
    ordinary = FleetConfig(image="worker-image", security_profile=RESTRICTED_PROFILE)
    dedicated = replace(ordinary, dispatch_only=True)
    dedicated.validate_restricted()
    assert ordinary.security_policy_fingerprint("/workspace", "worker") != (
        dedicated.security_policy_fingerprint("/workspace", "worker")
    )
    for invalid in (
        FleetConfig(dispatch_only=True),
        replace(dedicated, devcontainer_path="/repo"),
        replace(dedicated, dispatch_only="true"),
    ):
        with pytest.raises(RuntimeError, match="dispatch_only"):
            invalid.validate_restricted()


@pytest.mark.asyncio
async def test_task_only_venue_does_not_advertise_ordinary_launch_readiness(target, monkeypatch):
    info = SimpleNamespace(
        fleet="task-pool", container_id=PHYSICAL_ID, security_profile=RESTRICTED_PROFILE,
        state="running", is_running=True,
    )
    monkeypatch.setattr(resolver, "load_config", lambda: ContainersConfig(fleets={"task-pool": target.fleet}))
    monkeypatch.setattr(resolver, "get_container", lambda *args: info)
    monkeypatch.setattr(resolver, "get_lease", lambda *args: None)
    spec = await resolver.ContainerResolver().resolve_spec("pool-1")
    assert spec["venue"]["ready"] is False
    assert spec["venue"]["dispatch_only"] is True
    assert spec["venue"]["capabilities"]["task_projection"] is True
    with pytest.raises(RuntimeError, match="explicit task preparation"):
        await resolver.ContainerResolver().ensure_ready("pool-1")


@pytest.mark.parametrize("operation", ["prepare", "native-binding", "rotation"])
def test_delayed_binding_transactions_cannot_be_stolen(target, monkeypatch, operation):
    entered, release = threading.Event(), threading.Event()
    results = []
    real_lock = lease._lease_lock
    monkeypatch.setattr(lease, "_lease_lock", lambda **kwargs: real_lock(timeout=0.1))
    monkeypatch.setattr(
        projection.subprocess, "run",
        lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0, "", ""),
    )

    def delay():
        if threading.current_thread().name == "holder":
            entered.set()
            assert release.wait(3)

    if operation == "prepare":
        inspect = lifecycle.inspect_container
        monkeypatch.setattr(lifecycle, "inspect_container", lambda name: (delay(), inspect(name))[1])
        requests = [FACTS, {**FACTS, "task_id": "task-other"}]
        action = projection.prepare
    else:
        binding = projection.prepare("pool-1", PHYSICAL_ID, FACTS)
        requests = [grant(binding), grant(binding, owner_session_id="native-other")]
        if operation == "rotation":
            current = projection.project("pool-1", PHYSICAL_ID, requests[0])
            requests = [
                grant(binding, expires_at=current["expires_at"] + 100),
                grant(binding, expires_at=current["expires_at"] + 200),
            ]
        monkeypatch.setattr(
            resolver, "resolve_live_exec_target", lambda *args, **kwargs: (delay(), target)[1],
        )
        action = projection.project

    def call(index):
        try:
            action("pool-1", PHYSICAL_ID, requests[index])
            results.append((index, "success"))
        except RuntimeError:
            results.append((index, "blocked"))

    first = threading.Thread(target=call, args=(0,), name="holder")
    second = threading.Thread(target=call, args=(1,), name="contender")
    first.start()
    try:
        assert entered.wait(3)
        import os

        os.utime(lease._LOCK_FILE, (1, 1))
        second.start()
        second.join(timeout=2)
        assert not second.is_alive()
        assert results == [(1, "blocked")]
    finally:
        release.set()
        first.join(timeout=3)
    assert not first.is_alive()
    assert results == [(1, "blocked"), (0, "success")]
    state = projection._read(PHYSICAL_ID)
    assert state["task_id"] == FACTS["task_id"]
    if operation != "prepare":
        assert state["owner_session_id"] == "native-1"
    if operation == "rotation":
        assert state["expires_at"] == requests[0]["expires_at"]
        projection.project("pool-1", PHYSICAL_ID, requests[1])
        assert projection._read(PHYSICAL_ID)["expires_at"] == requests[1]["expires_at"]
