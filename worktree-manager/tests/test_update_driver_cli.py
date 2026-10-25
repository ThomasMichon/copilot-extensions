"""Public update request normalization, independent of the updater executor."""

from __future__ import annotations

import pytest
from pathlib import Path
from types import SimpleNamespace

from worktree_manager import update_driver_cli as driver

# Include interpreter, observer, worker startup and participant handoff.
_NATIVE_QUEUE_TIMEOUT = 30


@pytest.fixture
def short_home(tmp_path_factory: pytest.TempPathFactory) -> Path:
    # Exercise queue ownership, not the platform's AF_UNIX address-length limit.
    home = tmp_path_factory.mktemp("ud") / "home"
    home.mkdir()
    return home


def test_default_wait_preserves_canonical_update_arguments():
    flags = ["--force", "--project", "example", "--skip-modules", "agent-bridge"]
    options, forwarded = driver.parse_options(flags)
    assert forwarded == flags
    assert not options.detach
    assert not options.supervised_debug
    assert options.request_status is None


@pytest.mark.parametrize("observer", [[], ["--detach"], ["--supervised-debug"]])
def test_bootstrap_repair_prefix_survives_durable_observer_normalization(observer):
    arguments = ["--repair-engine", "/fresh/source with spaces", "--project", "example"]
    _options, forwarded = driver.parse_options([*observer, *arguments])
    assert forwarded == arguments


def test_bootstrap_repair_cannot_hide_after_ordinary_arguments():
    with pytest.raises(SystemExit):
        driver.parse_options(["--force", "--repair-engine", "/fresh/source"])


def test_detach_json_is_an_observer_option_not_an_installer_argument():
    options, forwarded = driver.parse_options(["--detach", "--json", "--force"])
    assert options.detach and options.json
    assert forwarded == ["--force"]


@pytest.mark.parametrize("flags", [
    ["--recover-for-repair", "--force"],
    ["--recover-for-repair", "--auth-report"],
    ["--recover-for-repair", "--detach"],
])
def test_operator_recovery_rejects_unrelated_modes(flags):
    with pytest.raises(SystemExit):
        driver.parse_options(flags)


def test_explicit_operator_recovery_reports_authority_not_native_proof(queued, monkeypatch, capsys):
    import json
    import update_driver

    seen, request = queued
    request.state = "interrupted"
    request.recovery_authority, request.released_at = "operator_repair", 2.0
    request.settled_at = request.settlement_kind = None

    def recover(*, authorized):
        assert authorized is True
        return (request,)

    monkeypatch.setattr(update_driver, "recover_for_repair", recover)
    assert driver.cmd_update_request(["--recover-for-repair", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["replayed"] is False
    assert result["released"][0]["recovery_authority"] == "operator_repair"
    assert result["released"][0]["settlement_kind"] is None
    assert seen == {}


@pytest.mark.parametrize("flags", [
    ["--detach", "--wait"],
    ["--detach", "--supervised-debug"],
    ["--request-status", "one", "--force"],
    ["--wait", "--json"],
    ["--auth-report", "--detach"],
    ["--auth-report", "--supervised-debug"],
    ["--token", "synthetic-unknown-secret"],
    ["--auth-source", "https://example.invalid/tools.git"],
    ["--request-status", "one", "--engine-acquisition-timeout", "600"],
    ["--recovery-status", "--engine-acquisition-timeout", "600"],
    ["--auth-report", "--engine-acquisition-timeout", "600"],
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
    from worktree_manager.runtime_source_config import RuntimeSource
    from worktree_manager import self_install

    monkeypatch.setattr(self_install, "default_root", lambda: tmp_path)
    monkeypatch.setattr(self_install, "manager_repo", lambda root: "https://example.invalid/tools.git")
    monkeypatch.setattr(self_install, "manager_ref", lambda root: "0123456789abcdef")
    assert driver.source_identity() == {
        "root": str(tmp_path.resolve()),
        "repo": "https://example.invalid/tools.git",
        "ref": "0123456789abcdef",
        "runtime_source": RuntimeSource().document(),
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


def test_fresh_repair_is_queued_without_running_in_the_observer(queued, monkeypatch):
    seen, _request = queued
    monkeypatch.setenv("AGENT_WORKTREES_PAYLOAD_ROOT", "/registered/native/payload")
    arguments = ["--repair-engine", "/fresh/source with spaces", "--project", "example"]
    assert driver.cmd_update_request(arguments) == 6
    assert seen["spec"].argv == tuple(arguments)
    assert dict(seen["spec"].context)["AGENT_WORKTREES_PAYLOAD_ROOT"] == (
        "/registered/native/payload"
    )
    assert seen["runner"].argv[-len(arguments) - 1:] == ("update", *arguments)


def test_engine_acquisition_budget_is_captured_by_the_durable_request(queued, monkeypatch):
    seen, _request = queued
    key = "WORKTREE_MANAGER_ENGINE_ACQUISITION_TIMEOUT_SEC"
    monkeypatch.setenv(key, "600")
    assert driver.cmd_update_request([]) == 6
    assert key not in dict(seen["spec"].context)
    assert key not in seen["runner"].context_keys
    assert seen["spec"].argv == ("--engine-acquisition-timeout", "600.0")
    options, forwarded = driver.parse_options(list(seen["spec"].argv))
    assert forwarded == []
    monkeypatch.setenv(key, "180")
    with driver._request_environment(seen["spec"].context), driver._acquisition_environment(
        options.engine_acquisition_timeout,
    ):
        assert driver.os.environ[key] == "600.0"
    assert driver.os.environ[key] == "180"
    with driver._acquisition_environment(None):
        assert key not in driver.os.environ
    assert driver.os.environ[key] == "180"


def test_manager_budget_is_consumed_before_engine_argument_forwarding():
    options, forwarded = driver.parse_options([
        "--repair-engine", "/fresh/source", "--engine-acquisition-timeout", "600",
        "--no-anchor-sync",
    ])
    assert options.engine_acquisition_timeout == "600"
    assert forwarded == ["--repair-engine", "/fresh/source", "--no-anchor-sync"]


def test_invalid_engine_acquisition_budget_is_not_enqueued(queued, monkeypatch):
    seen, _request = queued
    monkeypatch.setenv("WORKTREE_MANAGER_ENGINE_ACQUISITION_TIMEOUT_SEC", "inf")
    assert driver.cmd_update_request([]) == 1
    assert "spec" not in seen


def test_borrowed_execution_passes_repair_to_the_canonical_body(queued, monkeypatch, tmp_path):
    import update_driver
    from worktree_manager import __main__ as manager

    seen, request = queued
    request.state = "running"
    request.spec = SimpleNamespace(
        backend="manager", source=driver._source_key(),
        context=(("AGENT_WORKTREES_PAYLOAD_ROOT", "/registered/native/payload"),),
    )

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
    monkeypatch.setattr(
        manager, "_cmd_update", lambda args: seen.update(body=args) or 7,
    )
    arguments = ["--repair-engine", "/fresh/source with spaces", "--force"]
    assert driver.cmd_update_request(arguments) == 7
    assert seen["custody"] is True and seen["body"] == arguments
    assert "spec" not in seen


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


@pytest.mark.timeout(2 * _NATIVE_QUEUE_TIMEOUT)
def test_real_cli_worker_preserves_exit_and_serializes_processes(tmp_path, monkeypatch, short_home):
    import json
    import sys
    from concurrent.futures import ThreadPoolExecutor
    from pathlib import Path

    from agent_procutil.owned_process import run_owned
    import update_driver

    home = short_home
    events = tmp_path / "events.jsonl"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("UPDATE_DRIVER_TEST_EVENTS", str(events))
    fixture = Path(__file__).parent / "fixtures" / "update_driver_cli.py"
    command = [sys.executable, str(fixture), "submit", "--wait"]
    with ThreadPoolExecutor(max_workers=2) as callers:
        futures = [
            callers.submit(run_owned, command, timeout=_NATIVE_QUEUE_TIMEOUT) for _ in range(2)
        ]
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


@pytest.mark.parametrize("fresh_repair", [False, True], ids=["ordinary", "fresh-repair"])
@pytest.mark.timeout(2 * _NATIVE_QUEUE_TIMEOUT)
def test_actual_queued_manager_keeps_consumed_provider(
    tmp_path, monkeypatch, short_home, fresh_repair,
):
    import json
    import sys
    from pathlib import Path
    from agent_procutil.owned_process import run_owned
    from worktree_manager import engine_client

    home = short_home
    events = tmp_path / "events.jsonl"
    provider = [sys.executable, str(tmp_path / "selected-provider.py")]
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("UPDATE_DRIVER_TEST_EVENTS", str(events))
    monkeypatch.setenv(engine_client.ENGINE_ARGV_ENV, json.dumps(provider))
    fixture = Path(__file__).parent / "fixtures" / "update_driver_cli.py"
    forwarded = ["--repair-engine", str(tmp_path / "fresh source")] if fresh_repair else []
    result = run_owned(
        [sys.executable, str(fixture), "submit", "--wait", *forwarded],
        timeout=_NATIVE_QUEUE_TIMEOUT,
    )
    assert result.returncode == 6, result.stderr
    records = [json.loads(line) for line in events.read_text().splitlines()]
    assert records[0]["provider"] == provider
    assert records[0]["provider"] != ["synthetic-marker-provider"]
    assert records[0]["arguments"] == forwarded


@pytest.mark.timeout(7 * _NATIVE_QUEUE_TIMEOUT)
def test_actual_queued_manager_isolates_explicit_and_absent_engine_overrides(
    tmp_path, monkeypatch, short_home,
):
    import json
    import sys
    from pathlib import Path
    import update_driver
    from agent_procutil.owned_process import run_owned
    from worktree_manager import engine_client

    home = short_home
    events = tmp_path / "events.jsonl"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("UPDATE_DRIVER_TEST_EVENTS", str(events))
    accepted = ["synthetic-accepted-provider"]
    budget_key = "WORKTREE_MANAGER_ENGINE_ACQUISITION_TIMEOUT_SEC"
    monkeypatch.setenv(engine_client.ENGINE_ARGV_ENV, json.dumps(accepted))
    fixture = Path(__file__).parent / "fixtures" / "update_driver_cli.py"
    for override, budget in (
        ("synthetic-first-provider", "600"), ("synthetic-second-provider", "1200"), (None, None),
    ):
        for key, value in ((engine_client.ENGINE_CMD_ENV, override), (budget_key, budget)):
            if value is None:
                monkeypatch.delenv(key)
            else:
                monkeypatch.setenv(key, value)
        result = run_owned(
            [sys.executable, str(fixture), "submit-only"], timeout=_NATIVE_QUEUE_TIMEOUT,
        )
        assert result.returncode == 0, result.stderr
    root = home / ".copilot-extensions" / "update-driver"
    requests = update_driver.status(root=root)
    assert len(requests) == 3
    monkeypatch.setenv(engine_client.ENGINE_CMD_ENV, "wrong-worker-provider")
    monkeypatch.setenv(budget_key, "180")
    result = run_owned(
        [sys.executable, "-m", "update_driver", "worker"],
        timeout=len(requests) * _NATIVE_QUEUE_TIMEOUT,
    )
    assert result.returncode == 0, result.stderr
    records = [json.loads(line) for line in events.read_text().splitlines()]
    starts = [record for record in records if record["phase"] == "start"]
    assert [record["provider"] for record in starts] == [
        ["synthetic-first-provider"], ["synthetic-second-provider"], accepted,
    ]
    assert [record["acquisition_budget"] for record in starts] == ["600.0", "1200.0", None]


@pytest.mark.timeout(4 * _NATIVE_QUEUE_TIMEOUT)
def test_real_manager_to_engine_custody_keeps_the_legacy_context_contract(
    tmp_path, monkeypatch, short_home,
):
    import json
    import sys
    import update_driver
    from agent_procutil.owned_process import run_owned

    events = tmp_path / "events.jsonl"
    key = "WORKTREE_MANAGER_ENGINE_ACQUISITION_TIMEOUT_SEC"
    monkeypatch.setenv("HOME", str(short_home))
    monkeypatch.setenv("USERPROFILE", str(short_home))
    monkeypatch.setenv("UPDATE_DRIVER_TEST_EVENTS", str(events))
    monkeypatch.setenv("UPDATE_DRIVER_TEST_ENGINE_HANDOFF", "1")
    monkeypatch.setenv(key, "600")
    fixture = Path(__file__).parent / "fixtures" / "update_driver_cli.py"
    result = run_owned(
        [sys.executable, str(fixture), "submit", "--wait"],
        timeout=3 * _NATIVE_QUEUE_TIMEOUT,
    )
    assert result.returncode == 6, result.stderr
    records = [json.loads(line) for line in events.read_text().splitlines()]
    manager = next(record for record in records if record["phase"] == "start")
    engine = next(record for record in records if record["phase"] == "engine")
    assert manager["acquisition_budget"] == "600.0"
    assert manager["arguments"] == []
    assert engine["no_manager"] is True and key not in engine["context_keys"]
    assert manager["pid"] != engine["pid"]
    requests = update_driver.status(root=short_home / ".copilot-extensions" / "update-driver")
    assert len(requests) == 1 and requests[0].state == "completed"
    assert requests[0].exit_code == 6


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
