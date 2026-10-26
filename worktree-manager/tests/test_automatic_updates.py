"""Local consent, serialized admission and actual maintenance liveness contracts."""

from __future__ import annotations

import asyncio
import json
import os
import shlex
import shutil
import subprocess
import time
from contextlib import nullcontext
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import pytest
from zdd.cutover_lock import CutoverLock, CutoverLockedError

from worktree_manager import automatic_updates_cli as cli
from worktree_manager import automatic_updates_policy as policy
from worktree_manager import automatic_updates_process as process
from worktree_manager import automatic_updates_runtime as runtime


def test_constructed_worker_identity_is_its_retained_generation(tmp_path, monkeypatch):
    slot = tmp_path / "versions" / "1.2.3"
    monkeypatch.setattr(process.sys, "prefix", str(slot))
    assert process.runtime_identity() == str(slot)


def test_default_off_read_has_no_side_effect(tmp_path: Path) -> None:
    root = tmp_path / "absent"
    assert policy.load_policy(root) == policy.Policy()
    assert process.worker_status(root)["running"] is False
    assert not root.exists()


def test_cli_preview_and_ownership_are_read_only(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setattr(cli, "_os_account_home", lambda: tmp_path)
    monkeypatch.delenv("AGENT_HOME", raising=False)
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    root = tmp_path / ".worktree-manager"
    assert cli.main(["enable", "--project", "example", "--root", str(root), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "planned"
    assert cli.main(["ownership", "--root", str(root), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["policy"]["owner"] == "disabled"
    assert not root.exists()


def test_cli_harness_disable_precedence_has_no_startup_mutation(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    from worktree_manager import automatic_updates_autostart as autostart

    policy.configure(tmp_path, owner="disabled", provenance="user")

    def forbidden(*args, **kwargs):
        pytest.fail("setup must not change startup after explicit user disable")

    monkeypatch.setattr(autostart, "register_autostart", forbidden)
    assert cli.main([
        "enroll", "--harness", "example", "--root", str(tmp_path), "--apply", "--json",
    ]) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "user-disabled"


def test_cli_requires_explicit_harness_provenance(tmp_path: Path, capsys) -> None:
    assert cli.main(["enroll", "--root", str(tmp_path), "--json"]) == 1
    assert "requires --harness" in json.loads(capsys.readouterr().out)["reason"]


@pytest.mark.parametrize("change", [
    {"owner": "surprise"}, {"version": True}, {"version": 2},
    {"interval_seconds": True}, {"interval_seconds": 299}, {"interval_seconds": 86401},
    {"provenance": ""}, {"provenance": "setup\ncommand"}, {"project": False},
    {"external_controller": "other"}, {"owner": "external"},
    {"owner": "manager", "user_disabled": True}, {"owner": "manager"}, {"extra": "ignored"},
])
def test_policy_rejects_invalid_shape(change: dict[str, object]) -> None:
    with pytest.raises(policy.PolicyError):
        policy.validate({**asdict(policy.Policy()), **change})


def test_invalid_policy_never_becomes_default(tmp_path: Path) -> None:
    (tmp_path / policy.POLICY_NAME).write_text("{", encoding="utf-8")
    with pytest.raises(policy.PolicyError, match="invalid"):
        policy.load_policy(tmp_path)


def test_user_disable_wins_setup_but_user_can_enable(tmp_path: Path) -> None:
    disabled = policy.configure(tmp_path, owner="disabled", provenance="user")
    recommended = policy.configure(
        tmp_path, owner="manager", provenance="harness:example", harness=True, project="example",
    )
    assert recommended == disabled
    assert policy.load_policy(tmp_path).user_disabled
    enabled = policy.configure(tmp_path, owner="manager", provenance="user", project="example")
    assert enabled.owner == "manager"
    assert not enabled.user_disabled
    assert policy.load_policy(tmp_path) == enabled


def test_setup_idempotent_and_conflicts_loud(tmp_path: Path) -> None:
    kwargs = dict(owner="manager", provenance="harness:example", harness=True, project="example")
    first = policy.configure(tmp_path, **kwargs)
    stamp = (tmp_path / policy.POLICY_NAME).stat().st_mtime_ns
    assert policy.configure(tmp_path, **kwargs) == first
    assert (tmp_path / policy.POLICY_NAME).stat().st_mtime_ns == stamp
    with pytest.raises(policy.PolicyError, match="another enrollment"):
        policy.configure(
            tmp_path, owner="manager", provenance="harness:other", harness=True, project="other",
        )
    with pytest.raises(policy.PolicyError, match="disable"):
        policy.configure(tmp_path, owner="external", provenance="user", external_controller="other")


def test_setup_preserves_a_matching_explicit_user_selection(tmp_path: Path) -> None:
    existing = policy.configure(
        tmp_path, owner="manager", provenance="user", project="example",
    )
    stamp = (tmp_path / policy.POLICY_NAME).stat().st_mtime_ns
    assert policy.configure(
        tmp_path, owner="manager", provenance="harness:example", project="example", harness=True,
    ) == existing
    assert (tmp_path / policy.POLICY_NAME).stat().st_mtime_ns == stamp


def test_external_handover_requires_disable(tmp_path: Path) -> None:
    policy.configure(tmp_path, owner="external", provenance="user", external_controller="one")
    with pytest.raises(policy.PolicyError, match="disable"):
        policy.configure(tmp_path, owner="external", provenance="user", external_controller="two")
    policy.configure(tmp_path, owner="disabled", provenance="user")
    assert policy.configure(
        tmp_path, owner="external", provenance="user", external_controller="two",
    ).external_controller == "two"


def test_manual_and_automatic_admission(tmp_path: Path) -> None:
    assert policy.admit_trigger(tmp_path, "legacy-agent-machines").owner == "disabled"
    with pytest.raises(policy.PolicyError, match="refused"):
        policy.admit_trigger(tmp_path, "manager")
    policy.configure(tmp_path, owner="manager", provenance="user", project="example")
    assert policy.admit_trigger(tmp_path, "manager").owner == "manager"
    assert policy.admit_trigger(tmp_path, None).owner == "manager"
    with pytest.raises(policy.PolicyError, match="refused"):
        policy.admit_trigger(tmp_path, "legacy-agent-machines")
    policy.configure(tmp_path, owner="disabled", provenance="user")
    with pytest.raises(policy.PolicyError, match="refused"):
        policy.admit_trigger(tmp_path, "manager")
    assert policy.admit_trigger(tmp_path, None).user_disabled


def test_external_trigger_is_controller_specific(tmp_path: Path) -> None:
    policy.configure(tmp_path, owner="external", provenance="user", external_controller="one")
    assert policy.admit_trigger(tmp_path, "external", "one").owner == "external"
    for trigger, controller in (("external", None), ("external", "two"), ("manager", None)):
        with pytest.raises(policy.PolicyError):
            policy.admit_trigger(tmp_path, trigger, controller)


def test_configure_cannot_change_owner_during_active_update(tmp_path: Path) -> None:
    policy.configure(tmp_path, owner="manager", provenance="user", project="example")
    with CutoverLock(policy.state_dir(tmp_path) / "update-transaction"):
        with pytest.raises(CutoverLockedError):
            policy.configure(tmp_path, owner="disabled", provenance="user")
    assert policy.load_policy(tmp_path).owner == "manager"


def test_update_guard_strips_only_its_flags_and_serializes(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(cli, "default_root", lambda: tmp_path)
    monkeypatch.setenv("COPILOT_EXTENSIONS_UPDATE_HOME", str(tmp_path / "telemetry"))
    policy.configure(tmp_path, owner="manager", provenance="user", project="example")
    calls = []

    def execute(args: list[str], trigger: str, automatic_trigger: str | None) -> int:
        calls.append((args, trigger, automatic_trigger))
        with pytest.raises(CutoverLockedError):
            with CutoverLock(policy.state_dir(tmp_path) / "update-transaction"):
                pytest.fail("concurrent writer entered")
        return 4

    assert cli.guarded_update(
        ["--automatic-trigger", "manager", "--project", "example", "--no-anchor-sync"], execute,
    ) == 4
    assert calls == [(["--project", "example", "--no-anchor-sync"], "schedule", "manager")]
    policy.configure(tmp_path, owner="disabled", provenance="user")
    assert cli.guarded_update(["--automatic-trigger", "manager"], execute) == 3
    assert len(calls) == 1


@pytest.mark.parametrize("locked,code,outcome,error_code", [
    (True, 5, "blocked_by_lock", "update_transaction_held"),
    (False, 3, "configuration_limitation", "automatic_policy_refused"),
])
def test_guard_refusal_keeps_canonical_result_and_telemetry(
    tmp_path: Path, monkeypatch, locked: bool, code: int, outcome: str, error_code: str,
) -> None:
    telemetry = tmp_path / "telemetry"
    monkeypatch.setattr(cli, "default_root", lambda: tmp_path)
    monkeypatch.setenv("COPILOT_EXTENSIONS_UPDATE_HOME", str(telemetry))
    for key in ("COPILOT_EXTENSIONS_UPDATE_RUN_ID", "COPILOT_EXTENSIONS_UPDATE_EVENTS",
                "COPILOT_EXTENSIONS_UPDATE_OWNER"):
        monkeypatch.delenv(key, raising=False)
    if locked:
        policy.configure(tmp_path, owner="manager", provenance="user", project="example")
    guard = CutoverLock(policy.state_dir(tmp_path) / "update-transaction") if locked else nullcontext()
    with guard:
        assert cli.guarded_update(
            ["--automatic-trigger", "manager"], lambda args, trigger, automatic_trigger: 0,
        ) == code
    status = json.loads((telemetry / "update-status.json").read_text(encoding="utf-8"))
    assert status["run"]["outcome"] == outcome
    assert status["run"]["trigger"] == "schedule"
    assert status["run"]["event_parse_errors"] == 0
    events = [
        json.loads(line)
        for line in Path(status["run"]["events_path"]).read_text(encoding="utf-8").splitlines()
    ]
    event = next(
        event for event in events
        if event.get("action") == "update_admission" and event.get("component") == "manager"
    )
    assert event["outcome"] == outcome
    assert event["error"]["code"] == error_code


def test_selected_command_rejects_uninstalled_and_traversal(tmp_path: Path) -> None:
    with pytest.raises(policy.PolicyError, match="selected"):
        process.selected_command(tmp_path, ["update"])
    (tmp_path / "current-version").write_text("../other", encoding="utf-8")
    with pytest.raises(policy.PolicyError, match="valid"):
        process.selected_slot(tmp_path)
    (tmp_path / "current-version").write_text("1.0.0", encoding="utf-8")
    with pytest.raises(policy.PolicyError, match="unavailable"):
        process.selected_slot(tmp_path)


def test_environment_clears_session_and_run_identity(tmp_path: Path, monkeypatch) -> None:
    for key in ("GH_TOKEN", "GITHUB_TOKEN", "PYTHONPATH", "VIRTUAL_ENV", "GIT_ASKPASS",
                "SSH_ASKPASS", "SUDO_ASKPASS", "COPILOT_EXTENSIONS_UPDATE_RUN_ID",
                "COPILOT_EXTENSIONS_UPDATE_EVENTS", "COPILOT_EXTENSIONS_UPDATE_OWNER",
                "WORKTREE_MANAGER_ENGINE_CMD", "WORKTREE_MANAGER_ENGINE_ARGV",
                "COPILOT_EXTENSIONS_CONTEXT", "GIT_DIR", "GIT_CONFIG_COUNT",
                "PYTHONEXECUTABLE", "UV_INTERNAL__PYTHONHOME", "__PYVENV_LAUNCHER__",
                "UV_PROJECT_ENVIRONMENT", "AGENT_WORKTREES_UPDATE_CONTEXT",
                "AGENT_WORKTREES_PAYLOAD_ROOT", "AGENT_HOME"):
        monkeypatch.setenv(key, "old-session")
    monkeypatch.setenv("SSH_ASKPASS_REQUIRE", "force")
    monkeypatch.setenv("DISPLAY", ":fixture")
    env = process.update_environment(tmp_path)
    assert "old-session" not in env.values()
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    assert env["GCM_INTERACTIVE"] == "never"
    assert env["SSH_ASKPASS_REQUIRE"] == "never"
    assert "SSH_ASKPASS" not in env and "GIT_SSH_COMMAND" not in env
    assert env["GH_PROMPT_DISABLED"] == "1"
    assert env["WORKTREE_MANAGER_ROOT"] == str(tmp_path)


def test_worker_engine_root_does_not_depend_on_a_prior_session_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from worktree_manager.agent_plugin_runtime import legacy_plugin_root

    home = tmp_path / "account"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("AGENT_HOME", str(tmp_path / "session-only"))
    immediate = process.update_environment(tmp_path / "manager")
    monkeypatch.delenv("AGENT_HOME")
    later = process.update_environment(tmp_path / "manager")
    assert legacy_plugin_root("agent-worktrees", environment=immediate) == (
        legacy_plugin_root("agent-worktrees", environment=later)
    ) == home / ".agent-worktrees"


@pytest.mark.skipif(os.name == "nt", reason="detached POSIX OpenSSH control; Windows environment contract is shared")
def test_unattended_ssh_never_invokes_askpass(tmp_path: Path, monkeypatch) -> None:
    keygen = shutil.which("ssh-keygen")
    if keygen is None:
        pytest.skip("OpenSSH key tools unavailable; environment contract remains covered")
    key = tmp_path / "fixture-key"
    subprocess.run(
        [keygen, "-q", "-t", "ed25519", "-N", "fixture-only-passphrase", "-f", str(key)],
        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=10, check=True,
        start_new_session=True,
    )
    marker = tmp_path / "askpass-invoked"
    helper = tmp_path / "askpass"
    helper.write_text(
        "#!/bin/sh\n"
        f"printf invoked > {shlex.quote(str(marker))}\n"
        "printf '%s\\n' wrong-fixture-passphrase\n",
        encoding="utf-8",
    )
    helper.chmod(0o700)
    monkeypatch.setenv("SSH_ASKPASS", str(helper))
    monkeypatch.setenv("SSH_ASKPASS_REQUIRE", "force")
    monkeypatch.setenv("DISPLAY", ":fixture")
    arguments = [keygen, "-y", "-f", str(key)]
    control = subprocess.run(
        arguments, env=dict(os.environ), stdin=subprocess.DEVNULL,
        capture_output=True, text=True, timeout=10, start_new_session=True,
    )
    assert control.returncode != 0 and marker.is_file()
    marker.unlink()
    environment = process.update_environment(tmp_path)
    assert "SSH_ASKPASS" not in environment
    # Stand in for OpenSSH's default helper: never must also veto an explicit one.
    environment["SSH_ASKPASS"] = str(helper)
    actual = subprocess.run(
        arguments, env=environment, stdin=subprocess.DEVNULL,
        capture_output=True, text=True, timeout=10, start_new_session=True,
    )
    assert actual.returncode != 0
    assert not marker.exists()


def test_targeted_updates_share_automatic_admission_and_preserve_trigger(
    tmp_path: Path, monkeypatch,
) -> None:
    from worktree_manager import __main__ as manager, installation_updates

    monkeypatch.setattr(cli, "default_root", lambda: tmp_path)
    monkeypatch.setenv("COPILOT_EXTENSIONS_UPDATE_HOME", str(tmp_path / "telemetry"))
    calls = []

    def targeted(arguments, *, trigger="manual", automatic_trigger=None):
        calls.append((arguments, trigger, automatic_trigger))
        return 0

    monkeypatch.setattr(installation_updates, "run_targeted_update", targeted)
    assert manager._cmd_update(["--target", "agent-bridge"]) == 0
    assert calls == [(["--target", "agent-bridge"], "manual", None)]
    policy.configure(tmp_path, owner="manager", provenance="user", project="example")
    automatic = ["--automatic-trigger", "manager", "--target", "agent-bridge"]
    assert manager._cmd_update(automatic) == 0
    assert calls[-1] == (["--target", "agent-bridge"], "schedule", "manager")
    policy.configure(tmp_path, owner="disabled", provenance="user")
    assert manager._cmd_update(automatic) == 3
    assert len(calls) == 2


@pytest.mark.parametrize("enabled", [False, True])
def test_public_targeted_executor_applies_policy_and_consumes_manager_only_flags(
    tmp_path: Path, monkeypatch, enabled: bool,
) -> None:
    import update_driver
    from worktree_manager import engine_client, update_driver_cli as boundary

    monkeypatch.setenv("WORKTREE_MANAGER_ROOT", str(tmp_path))
    monkeypatch.setenv("COPILOT_EXTENSIONS_UPDATE_HOME", str(tmp_path / "telemetry"))
    monkeypatch.setattr(cli, "default_root", lambda: tmp_path)
    policy.configure(
        tmp_path, owner="manager" if enabled else "disabled", provenance="user",
        project="example" if enabled else None,
    )
    arguments = ["--target", "agent-bridge", "--automatic-trigger", "manager", "--project", "example"]
    request = SimpleNamespace(
        state="running",
        spec=update_driver.RequestSpec(
            backend="manager", argv=tuple(arguments), project="example", cwd=str(tmp_path),
            source=boundary._source_key(), context=tuple(boundary.request_context().items()),
        ),
    )

    class Custody:
        request_id = "targeted-executor"
        root = tmp_path

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def assert_owned(self):
            pass

    calls = []
    monkeypatch.setattr(update_driver, "borrowed_custody", Custody)
    monkeypatch.setattr(update_driver, "status", lambda *a, **k: request)
    monkeypatch.setattr(engine_client, "run_engine_passthrough", lambda project, args:
        calls.append((project, args)) or 0)
    assert boundary.cmd_update_request(arguments) == (0 if enabled else 3)
    assert calls == ([("example", ["update", "--no-manager", "--target", "agent-bridge"])] if enabled else [])


def test_health_proves_live_worker_and_rejects_wrong_cookie(tmp_path: Path) -> None:
    from agent_procutil._daemon_instance_identity import birth_identity

    async def exercise() -> None:
        worker = runtime.Worker(tmp_path)
        server = await asyncio.start_server(worker.health, "127.0.0.1", 0, limit=16384)
        async with server:
            worker.snapshot["port"] = server.sockets[0].getsockname()[1]
            worker.publish()
            status = await asyncio.to_thread(process.worker_status, tmp_path)
            assert status["running"]
            assert status["worker_id"] == worker.snapshot["worker_id"]
            assert status["birth"] == birth_identity(os.getpid())
            assert "cookie" not in status
            assert await asyncio.to_thread(
                process.probe, {**worker.snapshot, "cookie": "0" * 64},
            ) is None
        assert process.worker_status(tmp_path)["running"] is False
        assert "cookie" not in process.worker_status(tmp_path)["last_observed"]

    asyncio.run(exercise())


@pytest.mark.parametrize("exit_code", [0, 4, 5, 1])
def test_cycle_uses_fresh_canonical_command_and_result(
    tmp_path: Path, monkeypatch, exit_code: int,
) -> None:
    policy.configure(tmp_path, owner="manager", provenance="user", project="example")
    worker = runtime.Worker(tmp_path)
    commands = []
    launches = []

    def selected(root: Path, args: list[str]) -> list[str]:
        commands.append((root, args))
        return ["selected-runtime", *args]

    async def spawn(*args, **kwargs):
        launches.append((args, kwargs))

        async def wait() -> int:
            return exit_code

        return SimpleNamespace(wait=wait)

    monkeypatch.setattr(process, "selected_command", selected)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    asyncio.run(worker.cycle())
    assert commands == [(tmp_path, [
        "update", "--automatic-trigger", "manager", "--project", "example",
    ])]
    assert launches[0][1]["env"]["GIT_TERMINAL_PROMPT"] == "0"
    assert worker.snapshot["last_exit_code"] == exit_code
    assert worker.snapshot["state"] == ("waiting" if exit_code == 0 else "deferred")
    assert worker.snapshot["next_due"] > time.time()
    assert json.loads((policy.state_dir(tmp_path) / "worker.json").read_text())[
        "last_exit_code"
    ] == exit_code


def test_disabled_cycle_never_launches(tmp_path: Path, monkeypatch) -> None:
    def forbidden(*args, **kwargs):
        pytest.fail("disabled worker launched a command")

    monkeypatch.setattr(process, "selected_command", forbidden)
    asyncio.run(runtime.Worker(tmp_path).cycle())


def test_worker_lease_excludes_second_scheduler(tmp_path: Path) -> None:
    async def exercise() -> None:
        one, two = runtime.Worker(tmp_path), runtime.Worker(tmp_path)
        assert await one.acquire()
        assert not await two.acquire()
        one.lease.release()
        assert await two.acquire()
        two.lease.release()

    asyncio.run(exercise())


@pytest.mark.parametrize("change", [
    {"next_due": "tomorrow"}, {"next_due": float("nan")},
    {"next_due": time.time() + 172800}, {"failures": -1}, {"last_exit_code": False},
])
def test_invalid_saved_schedule_is_visible(tmp_path: Path, change: dict[str, object]) -> None:
    with pytest.raises(policy.PolicyError):
        runtime.Worker(tmp_path).resume_schedule(change)


def test_worker_self_cycle_opt_out_is_separate_from_enrollment(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("WORKTREE_MANAGER_AUTOMATIC_UPDATES_SELF_CYCLE", "0")
    worker = runtime.Worker(tmp_path)
    assert worker.snapshot["self_cycle_enabled"] is False
    assert policy.load_policy(tmp_path).owner == "disabled"
    monkeypatch.setenv("WORKTREE_MANAGER_AUTOMATIC_UPDATES_SELF_CYCLE", "maybe")
    with pytest.raises(policy.PolicyError, match="must be 0 or 1"):
        runtime.Worker(tmp_path)
