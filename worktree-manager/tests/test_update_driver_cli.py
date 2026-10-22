"""Public update request normalization, independent of the updater executor."""

from __future__ import annotations

import pytest
from types import SimpleNamespace

from worktree_manager import update_driver_cli as driver


def test_default_wait_preserves_canonical_update_arguments():
    flags = ["--force", "--project", "example", "--skip-modules", "agent-bridge"]
    options, forwarded = driver.parse_options(flags)
    assert forwarded == flags
    assert not options.detach
    assert not options.supervised_debug
    assert options.request_status is None


def test_detach_json_is_an_observer_option_not_an_installer_argument():
    options, forwarded = driver.parse_options(["--detach", "--json", "--force"])
    assert options.detach and options.json
    assert forwarded == ["--force"]


@pytest.mark.parametrize("flags", [
    ["--detach", "--wait"],
    ["--detach", "--supervised-debug"],
    ["--request-status", "one", "--force"],
    ["--wait", "--json"],
    ["--auth-report", "--detach"],
    ["--auth-report", "--supervised-debug"],
    ["--token", "synthetic-unknown-secret"],
    ["--auth-source", "https://example.invalid/tools.git"],
])
def test_invalid_observer_combinations_refuse(flags):
    with pytest.raises(SystemExit) as error:
        driver.parse_options(flags)
    assert error.value.code == 2


def test_request_context_never_persists_credentials(monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "synthetic-secret")
    monkeypatch.setenv("GITHUB_TOKEN", "other-synthetic-secret")
    monkeypatch.setenv("AGENT_WORKTREES_UPDATE_CONTEXT", "example-context")
    context = driver.request_context()
    assert "GH_TOKEN" not in context
    assert "GITHUB_TOKEN" not in context
    assert context["AGENT_WORKTREES_UPDATE_CONTEXT"] == "example-context"


def test_queue_captures_consumed_provider_without_restoring_ambient_transport(queued, monkeypatch):
    import json
    from worktree_manager import engine_client

    provider = ["synthetic-selected-provider", "--provider-option"]
    monkeypatch.setattr(engine_client, "_INHERITED_ENGINE_COMMAND", provider)
    monkeypatch.delenv(engine_client.ENGINE_ARGV_ENV, raising=False)
    seen, _request = queued
    assert driver.cmd_update_request([]) == 6
    assert json.loads(dict(seen["spec"].context)[engine_client.ENGINE_ARGV_ENV]) == provider
    assert engine_client.ENGINE_ARGV_ENV in seen["runner"].context_keys
    assert engine_client.ENGINE_ARGV_ENV not in driver.os.environ


def test_source_identity_preserves_explicit_selection(tmp_path, monkeypatch):
    from worktree_manager import self_install

    monkeypatch.setattr(self_install, "default_root", lambda: tmp_path)
    monkeypatch.setattr(self_install, "manager_repo", lambda root: "https://example.invalid/tools.git")
    monkeypatch.setattr(self_install, "manager_ref", lambda root: "0123456789abcdef")
    assert driver.source_identity() == {
        "root": str(tmp_path.resolve()),
        "repo": "https://example.invalid/tools.git",
        "ref": "0123456789abcdef",
    }


def test_relative_source_is_anchored_before_worker_changes_directory(tmp_path, monkeypatch):
    from worktree_manager import self_install

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(self_install, "default_root", lambda: tmp_path / "manager")
    monkeypatch.setattr(self_install, "manager_repo", lambda root: "selected-source")
    monkeypatch.setattr(self_install, "manager_ref", lambda root: "canary")
    identity = driver.source_identity()
    assert identity["repo"] == str(tmp_path / "selected-source")
    assert identity["ref"] == "canary"


def test_credential_bearing_source_is_not_written_to_queue(tmp_path, monkeypatch):
    from worktree_manager import self_install

    monkeypatch.setattr(self_install, "default_root", lambda: tmp_path)
    monkeypatch.setattr(self_install, "manager_repo", lambda root: "https://user:synthetic@example.invalid/tools.git")
    with pytest.raises(ValueError, match="cannot be journaled"):
        driver.source_identity()


@pytest.fixture
def queued(monkeypatch, tmp_path):
    import update_driver
    from worktree_manager import __main__ as manager

    seen = {}
    request = SimpleNamespace(
        id="synthetic-request", state="pending", exit_code=None, error=None,
        log_path=tmp_path / "update.log",
    )

    def enqueue(spec, *, runner):
        seen["spec"], seen["runner"] = spec, runner
        request.spec = spec
        return request

    def wait(request_id, **kwargs):
        seen["wait"] = request_id
        return SimpleNamespace(**{**vars(request), "state": "completed", "exit_code": 6})

    def forbidden(*args, **kwargs):
        pytest.fail("ordinary caller executed the installer")

    monkeypatch.setattr(update_driver, "borrowed_custody", lambda: None)
    monkeypatch.setattr(update_driver, "enqueue", enqueue)
    monkeypatch.setattr(update_driver, "runtime_command", lambda module, args=(): (
        (str(tmp_path / "python.exe"), "-m", module, *args), {},
    ))
    monkeypatch.setattr(update_driver, "ensure_worker", lambda *args, **kwargs: seen.update(worker=args))
    monkeypatch.setattr(update_driver, "wait", wait)
    monkeypatch.setattr(driver, "source_identity", lambda: {"repo": "synthetic-source", "ref": "fixed"})
    monkeypatch.setattr(manager, "_cmd_update", forbidden)
    return seen, request


def test_ordinary_update_only_enqueues_and_observes_canonical_exit(queued):
    seen, request = queued
    assert driver.cmd_update_request(["--force", "--project", "example"]) == 6
    assert seen["spec"].argv == ("--force", "--project", "example")
    assert seen["spec"].project == "example"
    assert seen["runner"].argv[-4:] == ("update", "--force", "--project", "example")
    assert seen["wait"] == request.id
    assert "worker" in seen


def test_detached_receipt_does_not_wait_or_claim_completion(queued, capsys):
    import json

    seen, request = queued
    assert driver.cmd_update_request(["--detach", "--json"]) == 0
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["request_id"] == request.id
    assert receipt["state"] == "pending"
    assert receipt["exit_code"] is None
    assert "wait" not in seen


def test_failed_worker_launch_is_not_foreground_fallback(queued, monkeypatch):
    import update_driver

    seen, _request = queued

    def fail(*args, **kwargs):
        raise update_driver.SpawnError("fixture launch denied")

    monkeypatch.setattr(update_driver, "ensure_worker", fail)
    assert driver.cmd_update_request([]) == 1
    assert "spec" in seen
    assert "wait" not in seen


def test_invalid_witness_cannot_become_an_ordinary_request(queued, monkeypatch):
    import update_driver

    seen, _request = queued

    def fail():
        raise update_driver.ParticipantError("stale custody")

    monkeypatch.setattr(update_driver, "borrowed_custody", fail)
    assert driver.cmd_update_request([]) == 1
    assert "spec" not in seen


def test_source_change_refuses_before_checked_participant_executes(queued, monkeypatch, tmp_path):
    import update_driver

    seen, request = queued
    request.state = "running"
    request.spec = SimpleNamespace(backend="manager", source="different-selection", context=())

    class Custody:
        root = tmp_path
        request_id = request.id

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def assert_owned(self):
            seen["custody"] = True

    monkeypatch.setattr(update_driver, "borrowed_custody", Custody)
    monkeypatch.setattr(update_driver, "status", lambda *args, **kwargs: request)
    assert driver.cmd_update_request([]) == 1
    assert "spec" not in seen
    assert "custody" not in seen


def test_public_manager_route_uses_request_boundary(monkeypatch):
    from worktree_manager import __main__ as manager

    seen = []
    monkeypatch.setattr(driver, "cmd_update_request", lambda rest: seen.append(rest) or 17)
    assert manager.main(["update", "--wait"]) == 17
    assert seen == [["--wait"]]


def test_real_cli_worker_preserves_exit_and_serializes_processes(tmp_path, monkeypatch):
    import json
    import sys
    from concurrent.futures import ThreadPoolExecutor
    from pathlib import Path

    from agent_procutil.owned_process import run_owned
    import update_driver

    home = tmp_path / "home"
    home.mkdir()
    events = tmp_path / "events.jsonl"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("UPDATE_DRIVER_TEST_EVENTS", str(events))
    fixture = Path(__file__).parent / "fixtures" / "update_driver_cli.py"
    command = [sys.executable, str(fixture), "submit", "--wait"]
    with ThreadPoolExecutor(max_workers=2) as callers:
        futures = [callers.submit(run_owned, command, timeout=20) for _ in range(2)]
        results = [future.result() for future in futures]
    assert [result.returncode for result in results] == [6, 6]
    assert all("Update request " in result.stderr for result in results)
    assert all("synthetic canonical updater started" in result.stderr for result in results)
    records = [json.loads(line) for line in events.read_text(encoding="utf-8").splitlines()]
    assert len(records) in (2, 4)
    for index in range(0, len(records), 2):
        assert records[index]["phase"] == "start"
        assert records[index + 1]["phase"] == "end"
        assert records[index]["pid"] == records[index + 1]["pid"]
    requests = update_driver.status()
    assert len(requests) in (1, 2)
    assert all(request.state == "completed" and request.exit_code == 6 for request in requests)


def test_actual_queued_manager_keeps_consumed_provider(tmp_path, monkeypatch):
    import json
    import sys
    from pathlib import Path
    from agent_procutil.owned_process import run_owned
    from worktree_manager import engine_client

    home = tmp_path / "home"
    home.mkdir()
    events = tmp_path / "events.jsonl"
    provider = [sys.executable, str(tmp_path / "selected-provider.py")]
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("UPDATE_DRIVER_TEST_EVENTS", str(events))
    monkeypatch.setenv(engine_client.ENGINE_ARGV_ENV, json.dumps(provider))
    fixture = Path(__file__).parent / "fixtures" / "update_driver_cli.py"
    result = run_owned([sys.executable, str(fixture), "submit", "--wait"], timeout=20)
    assert result.returncode == 6, result.stderr
    records = [json.loads(line) for line in events.read_text().splitlines()]
    assert records[0]["provider"] == provider
    assert records[0]["provider"] != ["synthetic-marker-provider"]


def test_actual_queued_manager_isolates_explicit_and_absent_engine_overrides(tmp_path, monkeypatch):
    import json
    import sys
    from pathlib import Path
    import update_driver
    from agent_procutil.owned_process import run_owned
    from worktree_manager import engine_client

    home = tmp_path / "home"
    home.mkdir()
    events = tmp_path / "events.jsonl"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("UPDATE_DRIVER_TEST_EVENTS", str(events))
    accepted = ["synthetic-accepted-provider"]
    monkeypatch.setenv(engine_client.ENGINE_ARGV_ENV, json.dumps(accepted))
    fixture = Path(__file__).parent / "fixtures" / "update_driver_cli.py"
    for override in ("synthetic-first-provider", "synthetic-second-provider", None):
        if override is None:
            monkeypatch.delenv(engine_client.ENGINE_CMD_ENV)
        else:
            monkeypatch.setenv(engine_client.ENGINE_CMD_ENV, override)
        result = run_owned([sys.executable, str(fixture), "submit-only"], timeout=20)
        assert result.returncode == 0, result.stderr
    root = home / ".copilot-extensions" / "update-driver"
    assert len(update_driver.status(root=root)) == 3
    monkeypatch.setenv(engine_client.ENGINE_CMD_ENV, "wrong-worker-provider")
    result = run_owned([sys.executable, "-m", "update_driver", "worker"], timeout=20)
    assert result.returncode == 0, result.stderr
    records = [json.loads(line) for line in events.read_text().splitlines()]
    assert [record["provider"] for record in records if record["phase"] == "start"] == [
        ["synthetic-first-provider"], ["synthetic-second-provider"], accepted,
    ]


def test_recovery_inspection_and_settlement_never_start_an_updater(queued, monkeypatch, capsys):
    import json
    import update_driver

    seen, request = queued
    monkeypatch.setattr(update_driver, "recovery_status", lambda: SimpleNamespace(
        eligible_ids=(request.id,), refused=(), error=None,
    ))
    assert driver.cmd_update_request(["--recovery-status", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["eligible_requests"] == [request.id]
    monkeypatch.setattr(update_driver, "recover_after_restart", lambda: (request,))
    assert driver.cmd_update_request(["--recover-after-restart", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["replayed"] is False
    assert seen == {}


def test_same_boot_recovery_refusal_is_structured_and_does_not_enqueue(queued, monkeypatch, capsys):
    import json
    import update_driver

    seen, _request = queued

    def refuse():
        raise update_driver.RecoveryError("same_kernel_boot")

    monkeypatch.setattr(update_driver, "recover_after_restart", refuse)
    assert driver.cmd_update_request(["--recover-after-restart", "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["error"] == "same_kernel_boot"
    assert seen == {}


@pytest.mark.parametrize(("args", "carries_witness"), [
    (["update", "--no-manager"], True),
    (["reconcile-plugins", "--apply"], True),
    (["stage-update"], True),
    (["update", "--auth-report"], False),
    (["stage-update", "--indicator-state"], False),
    (["doctor"], False),
])
def test_engine_handoff_is_explicit_only_for_mutating_update_commands(args, carries_witness, monkeypatch):
    import update_driver
    from worktree_manager import engine_client

    seen = {}
    monkeypatch.setattr(update_driver, "participant_environment", lambda: {"UPDATE_DRIVER_WITNESS": "fixture"})
    monkeypatch.setattr(engine_client, "_engine_environment", lambda: {"SAFE": "fixture"})
    monkeypatch.setattr(engine_client, "engine_base_command", lambda: ["fixture-engine"])

    def run(argv, **kwargs):
        seen.update(kwargs)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(engine_client.subprocess, "run", run)
    assert engine_client.run_engine_passthrough(None, args) == 0
    assert ("UPDATE_DRIVER_WITNESS" in seen["env"]) == carries_witness
    if carries_witness and engine_client.os.name == "nt":
        assert seen["creationflags"] & engine_client.subprocess.CREATE_NO_WINDOW
    elif not carries_witness:
        assert "creationflags" not in seen
