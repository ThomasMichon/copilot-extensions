"""Network-free tests of source, admission, coordinator and bridge boundaries."""

import importlib
import json
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_dispatch import bridge
from agent_dispatch.ci_repair_spawn import make_ci_repair_spawn
from agent_dispatch.producers import ci_repair, emitter
from agent_dispatch.queue import TaskError, TaskQueue


EXECUTION = {
    "venue": "container:repair-1",
    "charter": "repair-a",
    "workspace": "/workspace/project",
}
EVIDENCE = {
    "repository": "example/project",
    "run_id": 123,
    "run_attempt": 1,
    "sha": "a" * 40,
    "job_id": 456,
    "signature": "b" * 12,
}


def config(**kwargs):
    return {
        "repository": "example/project",
        "source_command": ["fake-source"],
        "admission_command": ["fake-admission"],
        "execution": EXECUTION,
        "lane_registration": "repair-lane",
        **kwargs,
    }


def verdict(evidence=EVIDENCE, selected=EXECUTION):
    return {
        "allowed": True,
        "failure": evidence,
        "execution": selected,
        "source_ref": "verified-ci-record",
        "policy_ref": "contained-policy",
        "inference_auth_ref": "fake-inference-proof",
        "repository_auth_ref": "fake-repository-proof",
    }


class Client:
    def __init__(self, path):
        self.queue = TaskQueue(path / "tasks.db")
        self.created = []

    def create(self, title, **fields):
        task = asdict(self.queue.create(title, **fields))
        self.created.append(task)
        return task

    def acquire_schedule_lease(self, *args, **kwargs):
        return {"granted": True, "lease": {}}


def adapters(command, **kwargs):
    request = json.loads(kwargs["input"])
    if command == ["fake-source"]:
        assert request == {"repository": "example/project"}
        output = [EVIDENCE]
    else:
        assert request["failure"] == EVIDENCE
        output = verdict(selected=request["execution"])
        if "phase" in request:
            output["phase"] = request["phase"]
    return SimpleNamespace(returncode=0, stdout=json.dumps(output), stderr="")


@pytest.mark.parametrize("lane", [None, "github.com/example/project"])
def test_catalog_routing_matches_the_actual_admitted_task(tmp_path, lane):
    from agent_dispatch.registrar import load_declaration
    from agent_dispatch.workers_catalog import _component_metadata

    binding = config(enabled=True, **({"lane_repository": lane} if lane else {}))
    declaration = load_declaration({
        "name": "repair-source", "kind": "emitter",
        "spec": {"id": "repair-source", "interval_seconds": 60, "ci_repair": binding},
    })
    component, = _component_metadata((declaration,), basis="source")["components"]
    task, = ci_repair.run_tick(Client(tmp_path), binding, runner=adapters)["created"]
    assert component["repo_scope"] == task["repo"] == (lane or binding["repository"])
    assert component["repo_scopes"] == [task["repo"]]
    assert component["labels"] == task["labels"] == ["ci-repair"]
    assert task["target_repo"] == binding["repository"]


def test_disabled_real_emitter_and_registrar(tmp_path, monkeypatch):
    client = Client(tmp_path)
    spec = {"id": "repair", "interval_seconds": 3600, "ci_repair": config()}

    def forbidden(*args, **kwargs):
        pytest.fail("OFF must not call adapters")

    result = emitter.run_tick(client, spec, holder="test", runner=forbidden)
    assert result["result"]["status"] == "disabled"
    assert not client.created
    from agent_dispatch.supervisor_registration import build_command

    # The existing registrar materializes the same builtin emitter spec.
    command = build_command(
        {"id": "repair", "kind": "emitter", "spec": spec}, materialize=lambda *args: "spec.json"
    )
    assert "serve" in command


def test_admission_dedup_and_real_bridge_argv(tmp_path, monkeypatch):
    client = Client(tmp_path)
    first = ci_repair.run_tick(client, config(enabled=True), runner=adapters)
    second = ci_repair.run_tick(client, config(enabled=True), runner=adapters)
    assert first["created"][0]["id"] == second["created"][0]["id"]
    task = first["created"][0]
    calls = []
    monkeypatch.setattr(bridge, "_agent_bridge_launch_prefix", lambda: ["fake-bridge"])
    monkeypatch.setattr(bridge, "_resolve_agent_record", lambda *args, **kwargs: None)

    def transport(argv, **kwargs):
        calls.append(argv)
        assert "--allow-all" not in argv
        return SimpleNamespace(returncode=0, stdout='{"session_id":"fake-session"}', stderr="")

    monkeypatch.setattr(bridge.subprocess, "run", transport)
    spawn = make_ci_repair_spawn(
        [EXECUTION], admission_command=["fake-admission"], admission_runner=adapters
    )
    ok, handle = spawn(task)
    assert ok, handle
    argv = calls[0]
    assert "--capability ci-repair-admitted" in handle["task_seed"]
    assert argv[:3] == ["fake-bridge", "--json", "create"]
    assert argv[argv.index("--target-dir") + 1] == EXECUTION["workspace"]
    assert argv[argv.index("--charter") + 1] == EXECUTION["charter"]
    assert EXECUTION["venue"] in argv
    assert "--no-wait" in argv
    assert handle["session"] == "local-body:fake-session"
    assert spawn.requires_reusable_worktree is False


@pytest.mark.parametrize(
    "change",
    [
        {"allowed": False, "reason": "cooldown"},
        {"allowed": False, "reason": "unresolved episode policy"},
        {"allowed": False, "reason": "venue auth not proven"},
        {"failure": {**EVIDENCE, "run_attempt": 2}},
        {"execution": {**EXECUTION, "charter": "unreviewed"}},
        {"policy_ref": ""},
        {"inference_auth_ref": "fake-repository-proof"},
        {"source_ref": ""},
    ],
)
def test_refused_admission_never_authors(tmp_path, change):
    client = Client(tmp_path)

    def runner(command, **kwargs):
        if command == ["fake-source"]:
            return adapters(command, **kwargs)
        return SimpleNamespace(returncode=0, stdout=json.dumps({**verdict(), **change}), stderr="")

    if change.get("allowed") is False:
        result = ci_repair.run_tick(client, config(enabled=True), runner=runner)
        assert result["refused"][0]["reason"] == change["reason"]
    else:
        with pytest.raises(ci_repair.AdmissionRefused):
            ci_repair.run_tick(client, config(enabled=True), runner=runner)
    assert not client.created


@pytest.mark.parametrize(
    "candidate",
    [
        {"labels": ["ci-failure-signature"]},
        {**EVIDENCE, "repository": "other/project"},
        {**EVIDENCE, "run_id": True},
        {**EVIDENCE, "sha": "unverified"},
        {**EVIDENCE, "signature": "issue-123"},
    ],
)
def test_untrusted_candidates_refused(tmp_path, candidate):
    client = Client(tmp_path)

    def runner(*args, **kwargs):
        return SimpleNamespace(returncode=0, stdout=json.dumps([candidate]), stderr="")

    with pytest.raises(ci_repair.AdmissionRefused):
        ci_repair.run_tick(client, config(enabled=True), runner=runner)
    assert not client.created


@pytest.mark.parametrize("output,code", [("not-json", 0), ("[]", 1)])
def test_adapter_errors_are_not_empty_success(tmp_path, output, code):
    with pytest.raises(ci_repair.AdmissionRefused):
        ci_repair.run_tick(
            Client(tmp_path),
            config(enabled=True),
            runner=lambda *a, **k: SimpleNamespace(
                returncode=code, stdout=output, stderr="denied"
            ),
        )


def test_two_reviewed_charters_and_spoofed_task(tmp_path, monkeypatch):
    client = Client(tmp_path)
    second = {**EXECUTION, "charter": "repair-b", "workspace": "/workspace/other"}
    task = ci_repair.run_tick(client, config(enabled=True, execution=second), runner=adapters)[
        "created"
    ][0]
    called = []
    monkeypatch.setattr(
        bridge,
        "spawn_worker",
        lambda *a, **k: (
            called.append(k)
            or SimpleNamespace(returncode=0, stdout='{"session_id":"second"}', stderr="")
        ),
    )
    assert make_ci_repair_spawn(
        [EXECUTION, second], admission_command=["fake-admission"], admission_runner=adapters
    )(task)[0]
    assert called[0]["charter"] == "repair-b"
    assert called[0]["target_dir"] == second["workspace"]
    assert not make_ci_repair_spawn([EXECUTION])(task)[0]
    task["source"] = "arbitrary-issue-label"
    assert not make_ci_repair_spawn([second])(task)[0]
    assert len(called) == 1


def test_watchdog_source_reuses_signature_and_rejects_stale_jobs(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[3] / "tools"))
    source = importlib.import_module("agent_dispatch.producers.ci_failure_source")
    watchdog = importlib.import_module("ci_failure_watchdog")
    run = {
        "id": 123,
        "run_attempt": 1,
        "status": "completed",
        "conclusion": "failure",
        "repository": {"full_name": "example/project"},
    }
    job = {
        "id": 456,
        "run_id": 123,
        "run_attempt": 1,
        "status": "completed",
        "conclusion": "failure",
        "name": "full - example",
    }
    text = "FAILED tests/example.py::test_regression - AssertionError"
    records = source.candidates("example/project", 123, "a" * 40, run, [job], lambda _: text)
    assert records[0]["signature"] == source.watchdog.signature_key(
        job["name"], "tests/example.py::test_regression"
    )
    assert records[0]["signature"] == watchdog.signature_key(
        job["name"], "tests/example.py::test_regression"
    )
    assert (
        source.candidates("example/project", 123, "a" * 40, run, [job], lambda _: text) == records
    )
    for changed in ({"run_attempt": 2}, {"run_id": 999}, {"status": "in_progress"}):
        with pytest.raises(ValueError):
            source.candidates(
                "example/project", 123, "a" * 40, run, [{**job, **changed}], lambda _: text
            )


def test_real_adapter_processes_and_coordinator_lifecycle(tmp_path, monkeypatch):
    monkeypatch.setattr("agent_dispatch.ci_repair_spawn._invoke", lambda command, config, **kwargs: {
        "allowed": True, **kwargs["request"],
    })
    client = Client(tmp_path)
    selected = config(enabled=True)
    selected["source_command"] = [
        sys.executable,
        "-c",
        f"import json; print(json.dumps([{EVIDENCE!r}]))",
    ]
    selected["admission_command"] = [
        sys.executable,
        "-c",
        "import json,sys; r=json.load(sys.stdin); "
        f"v={verdict()!r}; v.update(r); print(json.dumps(v))",
    ]
    task = ci_repair.run_tick(client, selected)["created"][0]
    assert client.queue.claim_one("ordinary-worker", repo=task["repo"], task_id=task["id"]) is None
    client.queue.register_registration(
        "supervised-lane",
        {
            "repo": task["repo"],
            "labels": ["ci-repair"],
            "ci_repair_bindings": [EXECUTION],
            "ci_repair_admission_command": ["fake-admission"],
        },
        reg_id="repair-lane",
    )
    from agent_dispatch.supervisor import Supervisor
    ordinary = Supervisor(client, spawn_fn=lambda task: pytest.fail("catch-all cannot spawn"),
                          repo=task["repo"])
    assert not ordinary._matches_pool(task)
    with pytest.raises(TaskError):
        client.queue.reserve_spawn(task["id"], reserved_by="catch-all")
    reservation, won = client.queue.reserve_spawn(task["id"], reserved_by="repair-lane")
    assert won
    client.queue.record_spawn(reservation.key, session_handle="local-body:fake-session")
    claimed = client.queue.claim_one(
        "fake-worker", capabilities=["ci-repair-admitted"], repo=task["repo"], task_id=task["id"]
    )
    assert claimed.id == task["id"]
    assert client.queue.start(task["id"], "fake-worker").status == "started"
    assert client.queue.complete(task["id"], "fake-worker").status == "submitted"
    assert client.queue.confirm(task["id"], actor="contained-verifier").status == "completed"
    # A terminal task releases ordinary coordinator dedup. The policy adapter,
    # not a made-up episode budget in this emitter, must refuse recurrence.
    selected["admission_command"] = [
        sys.executable,
        "-c",
        "import json; print(json.dumps({'allowed':False,'reason':'not rearmed'}))",
    ]
    assert ci_repair.run_tick(client, selected)["refused"][0]["reason"] == "not rearmed"
    assert len(client.created) == 1


def test_registration_preserves_bindings_and_refuses_incompatible_modes():
    from agent_dispatch.supervise_cli import _validate_spec_charter_mode
    from agent_dispatch.supervisor_registration import build_command

    spec = {
        "labels": ["ci-repair"],
        "ci_repair_bindings": [EXECUTION],
        "ci_repair_admission_command": ["fake-admission"],
    }
    _validate_spec_charter_mode(spec)
    argv = build_command({"id": "repair-lane", "kind": "supervised-lane", "spec": spec})
    assert json.loads(argv[argv.index("--ci-repair-bindings") + 1]) == [EXECUTION]
    assert json.loads(argv[argv.index("--ci-repair-admission-command") + 1]) == ["fake-admission"]
    for extra in (
        {"fleet": {"pool": ["host"]}},
        {"embody_backend": "cli"},
        {"labels": []},
        {"labels": ["other"]},
        {"labels": ["ci-repair", "other"]},
    ):
        with pytest.raises(SystemExit):
            _validate_spec_charter_mode({**spec, **extra})


@pytest.mark.parametrize("command", [None, [], "adapter", [""], [None]])
def test_invalid_admission_argv_is_refused_at_config_boundary(command):
    from agent_dispatch.ci_repair_spawn import validate_admission_command
    from agent_dispatch.supervise_cli import _validate_spec_charter_mode

    with pytest.raises(ci_repair.AdmissionRefused):
        validate_admission_command(command)
    with pytest.raises(SystemExit):
        _validate_spec_charter_mode(
            {
                "labels": ["ci-repair"],
                "ci_repair_bindings": [EXECUTION],
                "ci_repair_admission_command": command,
            }
        )


@pytest.mark.parametrize(
    "bindings,command", [("bad", '["adapter"]'), ("[]", "bad"), ("[]", "null")]
)
def test_cli_json_errors_are_concise(bindings, command):
    from agent_dispatch.ci_repair_spawn import cli_bindings

    with pytest.raises(SystemExit, match="Invalid CI repair"):
        cli_bindings(bindings, command)


def test_registrar_refuses_missing_admission_before_serialization():
    from agent_dispatch.supervisor_registration import UnsupportedKind, build_command

    with pytest.raises(UnsupportedKind, match="admission command"):
        build_command(
            {
                "id": "repair",
                "kind": "supervised-lane",
                "spec": {"ci_repair_bindings": [EXECUTION], "labels": ["ci-repair"]},
            }
        )


def test_api_registration_rejects_bad_ci_bindings_before_reconciliation(tmp_path):
    queue = TaskQueue(tmp_path / "registrations.db")
    for spec in (
        {"repo": "example/project", "labels": ["ci-repair"], "ci_repair_bindings": [EXECUTION]},
        {"repo": "example/project", "labels": ["other"], "ci_repair_bindings": [EXECUTION],
         "ci_repair_admission_command": ["fake-adapter"]},
    ):
        with pytest.raises(TaskError):
            queue.register_registration("supervised-lane", spec)
    with pytest.raises(TaskError):
        queue.register_registration("emitter", {
            "id": "bad", "interval_seconds": 1, "ci_repair": {"enabled": True},
        })
    assert queue.register_registration("supervised-lane", {"repo": "example/project"}).id


@pytest.mark.parametrize("scope", [{"all_repos": True}, {"repo": "https://github.com/example/project.git"}])
def test_ci_reservation_accepts_canonical_and_all_repository_scopes(tmp_path, scope, monkeypatch):
    monkeypatch.setattr("agent_dispatch.ci_repair_spawn._invoke", lambda command, config, **kwargs: {
        "allowed": True, **kwargs["request"],
    })
    client = Client(tmp_path)
    task = ci_repair.run_tick(client, config(enabled=True, lane_repository="github.com/example/project"), runner=adapters)["created"][0]
    client.queue.register_registration("supervised-lane", {
        **scope, "labels": ["ci-repair"], "ci_repair_bindings": [EXECUTION],
        "ci_repair_admission_command": ["fake-adapter"],
    }, reg_id="repair-lane")
    assert client.queue.reserve_spawn(task["id"], reserved_by="repair-lane")[1]


@pytest.mark.parametrize(
    "error", [OSError("adapter missing"), subprocess.TimeoutExpired("adapter", 30)]
)
def test_spawn_adapter_failure_returns_failed_reservation_result(tmp_path, error):
    task = ci_repair.run_tick(Client(tmp_path), config(enabled=True), runner=adapters)["created"][
        0
    ]

    def failing_adapter(*args, **kwargs):
        raise error

    ok, result = make_ci_repair_spawn(
        [EXECUTION], admission_command=["fake-adapter"], admission_runner=failing_adapter
    )(task)
    assert not ok and result["error"]


def test_forged_task_or_revoked_authority_cannot_launch(tmp_path, monkeypatch):
    task = ci_repair.run_tick(Client(tmp_path), config(enabled=True), runner=adapters)["created"][
        0
    ]
    monkeypatch.setattr(bridge, "spawn_worker", lambda *a, **k: pytest.fail("must not launch"))
    assert not make_ci_repair_spawn([EXECUTION])(task)[0]
    revoked = lambda *a, **k: SimpleNamespace(
        returncode=0, stdout='{"allowed":false,"reason":"auth revoked"}', stderr=""
    )
    assert not make_ci_repair_spawn(
        [EXECUTION], admission_command=["fake-admission"], admission_runner=revoked
    )(task)[0]


def test_ci_repair_side_load_cannot_bypass_admission():
    spec = {
        "id": "repair",
        "interval_seconds": 1,
        "ci_repair": config(),
        "side_load": {"command": ["untrusted", "{change_ref}"]},
    }
    with pytest.raises(emitter.EmitterError, match="ungated side_load"):
        emitter.validate_spec(spec)


def test_denied_signature_does_not_suppress_valid_peer(tmp_path):
    second = {**EVIDENCE, "signature": "c" * 12}
    def runner(command, **kwargs):
        if command == ["fake-source"]:
            value = [EVIDENCE, second]
        else:
            request = json.loads(kwargs["input"])
            value = ({"allowed": False, "reason": "cooldown"} if request["failure"] == EVIDENCE
                     else verdict(second))
        return SimpleNamespace(returncode=0, stdout=json.dumps(value), stderr="")
    result = ci_repair.run_tick(Client(tmp_path), config(enabled=True), runner=runner)
    assert len(result["created"]) == 1 and len(result["refused"]) == 1
    assert result["created"][0]["dedup_key"].endswith("c" * 12)


def test_spoofed_registration_id_and_fake_session_do_not_authorize_claim(tmp_path, monkeypatch):
    client = Client(tmp_path)
    task = ci_repair.run_tick(client, config(enabled=True), runner=adapters)["created"][0]
    client.queue.register_registration("supervised-lane", {
        "repo": task["repo"], "labels": ["ci-repair"], "ci_repair_bindings": [EXECUTION],
        "ci_repair_admission_command": ["independent-authority"],
    }, reg_id="repair-lane")
    monkeypatch.setattr("agent_dispatch.ci_repair_spawn._invoke",
                        lambda *a, **k: {"allowed": False, "reason": "unverified caller"})
    with pytest.raises(TaskError):
        client.queue.reserve_spawn(task["id"], reserved_by="repair-lane")
    assert client.queue.claim_one("spoofed-worker", capabilities=["ci-repair-admitted"],
                                 repo=task["repo"], task_id=task["id"]) is None


def test_authority_probe_does_not_hold_coordinator_write_lock(tmp_path, monkeypatch):
    client = Client(tmp_path)
    task = ci_repair.run_tick(client, config(enabled=True), runner=adapters)["created"][0]
    client.queue.register_registration("supervised-lane", {
        "repo": task["repo"], "labels": ["ci-repair"], "ci_repair_bindings": [EXECUTION],
        "ci_repair_admission_command": ["independent-authority"],
    }, reg_id="repair-lane")
    def probe(*args, **kwargs):
        other = TaskQueue(tmp_path / "tasks.db")
        other.create("unrelated write", repo="example/other")
        return {"allowed": True, **kwargs["request"]}
    monkeypatch.setattr("agent_dispatch.ci_repair_spawn._invoke", probe)
    reservation, _ = client.queue.reserve_spawn(task["id"], reserved_by="repair-lane")
    client.queue.record_spawn(reservation.key, session_handle="local-body:verified")
    assert client.queue.claim_one("verified-worker", capabilities=["ci-repair-admitted"],
                                 repo=task["repo"], task_id=task["id"])


def test_coordinator_launch_never_grants_caller_selected_spawn(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from agent_dispatch.coordinator_spawn import register_spawn_routes
    from agent_dispatch.events import EventBus
    client = Client(tmp_path)
    task = ci_repair.run_tick(client, config(enabled=True), runner=adapters)["created"][0]
    client.queue.register_registration("supervised-lane", {
        "repo": task["repo"], "labels": ["ci-repair"], "ci_repair_bindings": [EXECUTION],
        "ci_repair_admission_command": ["fake-admission"],
    }, reg_id="repair-lane")
    monkeypatch.setattr("agent_dispatch.ci_repair_spawn._invoke", lambda command, config, **kwargs: {
        "allowed": True, **kwargs["request"],
    })
    calls = []
    monkeypatch.setattr(bridge, "spawn_worker", lambda *a, **k: (
        calls.append(k) or SimpleNamespace(returncode=0, stdout='{"session_id":"trusted"}', stderr="")
    ))
    monkeypatch.setattr(bridge, "resume_worker", lambda *a, **k: True)
    def admission(command, config, **kwargs):
        if kwargs["request"].get("phase") == "spawned":
            staging = client.queue.latest_reservation(task["id"])
            assert staging.state == "reserving"
            assert staging.session_handle == "local-body:trusted"
            assert client.queue.claim_one("premature", capabilities=["ci-repair-admitted"],
                                          repo=task["repo"], task_id=task["id"]) is None
        return {**verdict(), **kwargs["request"]}
    monkeypatch.setattr("agent_dispatch.ci_repair_spawn._invoke", admission)
    app = FastAPI()
    register_spawn_routes(app, client.queue, EventBus())
    with TestClient(app) as http:
        result = http.post("/spawn-reservations", json={
            "task_id": task["id"], "reserved_by": "repair-lane",
        })
    assert result.status_code == 200, result.text
    assert result.json()["reserved"] is False
    assert result.json()["coordinator_launch"]["ok"] is True
    assert calls[0]["agent"] == EXECUTION["venue"]
    assert calls[0]["charter"] == EXECUTION["charter"]


def test_terminal_coordinator_failure_does_not_starve_next_task(tmp_path, monkeypatch):
    from agent_dispatch.supervisor import Supervisor
    from tests.test_supervisor import QueueBackedClient
    queue = TaskQueue(tmp_path / "starvation.db")
    first = queue.create("first", repo="example/project", source="ci-repair", origin_ref="repair-lane")
    second = queue.create("second", repo="example/project", source="ci-repair", origin_ref="repair-lane")
    client = QueueBackedClient(queue)
    calls = []
    def reserve(task_id, **kwargs):
        calls.append(task_id)
        return {"reserved": False, "reservation": {"state": "failed" if task_id == first.id else "spawned"},
                "coordinator_launch": {"ok": task_id != first.id}}
    monkeypatch.setattr(client, "reserve_spawn", reserve)
    supervisor = Supervisor(client, spawn_fn=lambda task: pytest.fail("caller must not launch"),
                            repo="example/project", supervisor_id="repair-lane", max_attempts=0)
    assert supervisor.poll_once() == [second.id]
    assert calls == [first.id, second.id]


def test_client_deadlines_are_scoped_to_delayed_ci_operations():
    from agent_dispatch.client import DispatchClient
    calls = []
    def post(path, **kwargs):
        body = kwargs["json"]
        delayed = body.get("task_id") == "ci-task"
        budget = kwargs.get("timeout", 10)
        assert budget > (31 if delayed else 1)
        calls.append((path, kwargs))
        return SimpleNamespace(status_code=200, json=lambda: {"ok": True})
    client = object.__new__(DispatchClient)
    client._http = SimpleNamespace(post=post)
    client.reserve_spawn("ordinary")
    client.claim("ordinary-worker", task_id="ordinary")
    assert all("timeout" not in kwargs for _, kwargs in calls)
    client.reserve_spawn("ci-task", coordinator_launch=True)
    client.claim("ci-worker", capabilities=["ci-repair-admitted"], task_id="ci-task")
    assert calls[-2][1]["timeout"] == 180
    assert calls[-1][1]["timeout"] == 60


def test_watchdog_source_cli_uses_only_scoped_wrapper(monkeypatch, capsys):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[3] / "tools"))
    source = importlib.import_module("agent_dispatch.producers.ci_failure_source")
    calls = []

    def request(argv, **kwargs):
        calls.append(argv)
        assert argv[:2] == ["scoped-wrapper", "example/project"]
        if "/jobs?" in argv[3]:
            value = {
                "jobs": [
                    {
                        "id": 456,
                        "run_id": 123,
                        "run_attempt": 1,
                        "status": "completed",
                        "conclusion": "failure",
                        "name": "unit",
                    }
                ]
            }
        elif "/logs" in argv[3]:
            return SimpleNamespace(returncode=0, stdout="FAILED tests/a.py::test_b", stderr="")
        else:
            value = {
                "id": 123,
                "run_attempt": 1,
                "status": "completed",
                "conclusion": "failure",
                "repository": {"full_name": "example/project"},
            }
        return SimpleNamespace(returncode=0, stdout=json.dumps(value), stderr="")

    monkeypatch.setattr(source.subprocess, "run", request)
    args = [
        "--repo",
        "example/project",
        "--run-id",
        "123",
        "--sha",
        "a" * 40,
        "--gh-command",
        '["scoped-wrapper","example/project"]',
    ]
    assert source.main(args) == 0
    assert json.loads(capsys.readouterr().out)[0]["job_id"] == 456
    assert len(calls) == 3
    assert source.main([*args[:-1], '["gh"]']) == 1
    assert len(calls) == 3
