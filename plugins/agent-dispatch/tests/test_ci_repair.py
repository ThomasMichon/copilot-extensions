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
from agent_dispatch.queue import TaskQueue


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
    return SimpleNamespace(returncode=0, stdout=json.dumps(output), stderr="")


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
    source = importlib.import_module("ci_failure_dispatch_source")
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
    assert (
        source.candidates("example/project", 123, "a" * 40, run, [job], lambda _: text) == records
    )
    for changed in ({"run_attempt": 2}, {"run_id": 999}, {"status": "in_progress"}):
        with pytest.raises(ValueError):
            source.candidates(
                "example/project", 123, "a" * 40, run, [{**job, **changed}], lambda _: text
            )


def test_real_adapter_processes_and_coordinator_lifecycle(tmp_path):
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
    claimed = client.queue.claim_one("fake-worker", repo=task["repo"], task_id=task["id"])
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
    with pytest.raises(ci_repair.AdmissionRefused, match="not rearmed"):
        ci_repair.run_tick(client, selected)
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
    from agent_dispatch.supervisor_registration import build_command

    with pytest.raises(ci_repair.AdmissionRefused, match="admission command"):
        build_command(
            {
                "id": "repair",
                "kind": "supervised-lane",
                "spec": {"ci_repair_bindings": [EXECUTION], "labels": ["ci-repair"]},
            }
        )


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


def test_watchdog_source_cli_uses_only_scoped_wrapper(monkeypatch, capsys):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[3] / "tools"))
    source = importlib.import_module("ci_failure_dispatch_source")
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
