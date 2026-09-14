from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agent_machines import __main__ as cli
from agent_machines import self_update
from agent_machines.manifest import ManifestError, load_package
from agent_machines.reconcile import plan
from agent_machines.resources import resolve_resources

from ._helpers import base_package, write_package


def _pkg(
    tmp_path: Path,
    name: str,
    resources: list[dict],
    *,
    authority: int | None = None,
) -> object:
    data = base_package(
        name=name,
        schema_version=4,
        gate=["box-1"],
        manage={},
        resources=resources,
    )
    if authority is not None:
        data["authority"] = authority
    path = write_package(tmp_path / name.replace("/", "_"), "pkg.yaml", data)
    return load_package(path, source_repo=name.split("/")[0])


class _FakeMutex:
    def __init__(self, outcome: str):
        self.outcome = outcome
        self.released = False
        self.closed = False

    def try_acquire(self) -> str:
        return self.outcome

    def release(self) -> None:
        self.released = True

    def close(self) -> None:
        self.closed = True


def _task_snapshot(
    tier: str,
    *,
    present: bool,
    matching: bool = True,
    enabled: bool | None = True,
):
    return self_update.ScheduledTaskSnapshot(
        task_name=self_update.TIER_SPECS[tier].task_name,
        present=present,
        enabled=enabled,
        logon_type="Interactive" if present else None,
        description=self_update.task_description(tier) if present else None,
        execute="conhost.exe" if present else None,
        arguments=self_update.task_action_arguments(tier),
        working_directory=self_update.task_working_directory(),
        matching=matching,
    )


def test_manifest_accepts_self_update_resource_and_rejects_bad_tier(tmp_path):
    package = _pkg(
        tmp_path,
        "acme/watchdog",
        [{"type": "self-update", "tier": "watchdog"}],
    )
    assert package.resources[0]["tier"] == "watchdog"
    with pytest.raises(ManifestError, match="self-update tier"):
        _pkg(
            tmp_path,
            "acme/invalid",
            [{"type": "self-update", "tier": "weekly"}],
        )


def test_self_update_resource_selects_highest_authority_and_equal_highest_conflicts(
    tmp_path,
):
    disabled = _pkg(
        tmp_path,
        "acme/disabled",
        [{"type": "self-update", "tier": "watchdog", "state": "absent"}],
        authority=0,
    )
    enabled = _pkg(
        tmp_path,
        "acme/enabled",
        [{"type": "self-update", "tier": "watchdog", "state": "present"}],
        authority=10,
    )
    resolved, findings = resolve_resources([disabled, enabled], "box-1", "windows")
    watchdog = next(resource for resource in resolved if resource.type == "self-update")
    assert watchdog.desired["state"] == "present"
    assert any(finding.code == "authority-supersession" for finding in findings)
    equal = _pkg(
        tmp_path,
        "acme/equal",
        [{"type": "self-update", "tier": "watchdog", "state": "absent"}],
        authority=10,
    )
    _, equal_findings = resolve_resources([equal, enabled], "box-1", "windows")
    assert any(finding.code == "resource-conflict" for finding in equal_findings)


def test_run_noops_when_tier_not_opted_in():
    result = self_update.run_tier("watchdog", opted_in=False)
    assert result.ok is True
    assert result.status == "noop"
    assert result.detail == "tier 'watchdog' is not opted in"


def test_lock_refuses_live_owner_without_double_drive(tmp_path, monkeypatch):
    started = datetime(2026, 1, 1, tzinfo=UTC)
    self_update.lock_path("watchdog", tmp_path).parent.mkdir(parents=True, exist_ok=True)
    self_update.lock_path("watchdog", tmp_path).write_text(
        json.dumps(
            {
                "schema_version": 1,
                "tier": "watchdog",
                "pid": 4321,
                "started_at": started.isoformat(),
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(self_update, "_WindowsMutex", lambda _name: _FakeMutex("timeout"))
    monkeypatch.setattr(self_update.sys, "platform", "win32")
    lock = self_update.TierLock(tier="watchdog", home=tmp_path)
    acquired, detail = lock.acquire()
    assert acquired is False
    assert "already active" in detail
    assert lock.snapshot is not None
    assert lock.snapshot.pid == 4321


def test_lock_reclaims_dead_owner_only_after_stale_window(tmp_path, monkeypatch):
    started = datetime(2026, 1, 1, tzinfo=UTC)
    path = self_update.lock_path("watchdog", tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "tier": "watchdog",
                "pid": 4321,
                "started_at": started.isoformat(),
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(self_update, "_WindowsMutex", lambda _name: _FakeMutex("acquired"))
    monkeypatch.setattr(self_update.sys, "platform", "win32")
    lock = self_update.TierLock(
        tier="watchdog",
        home=tmp_path,
        pid=9999,
        now=lambda: started + timedelta(minutes=11),
        pid_alive=lambda _pid: False,
    )
    acquired, detail = lock.acquire()
    assert acquired is True
    assert detail == "acquired"
    assert lock.reclaimed is True
    record = json.loads(path.read_text(encoding="utf-8"))
    assert record["pid"] == 9999
    lock.release()


def test_lock_refuses_recent_dead_owner(tmp_path, monkeypatch):
    started = datetime(2026, 1, 1, tzinfo=UTC)
    path = self_update.lock_path("watchdog", tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "tier": "watchdog",
                "pid": 4321,
                "started_at": started.isoformat(),
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(self_update, "_WindowsMutex", lambda _name: _FakeMutex("acquired"))
    monkeypatch.setattr(self_update.sys, "platform", "win32")
    lock = self_update.TierLock(
        tier="watchdog",
        home=tmp_path,
        pid=9999,
        now=lambda: started + timedelta(minutes=9),
        pid_alive=lambda _pid: False,
    )
    acquired, detail = lock.acquire()
    assert acquired is False
    assert "stale window has not elapsed" in detail
    assert lock.snapshot is not None
    assert json.loads(path.read_text(encoding="utf-8"))["pid"] == 4321


def test_watchdog_starts_launcher_when_missing(monkeypatch):
    config = self_update.DtsshConfig(
        config_path=Path("config.json"),
        install_root=Path("install"),
        launcher_path=Path(r"C:\agent-ssh-dtssh\dtssh-host-launcher.ps1"),
        alias="box-1",
        port=2222,
    )
    state = {"running": False}

    def process_lister():
        return (
            []
            if not state["running"]
            else [{"ProcessId": 321, "CommandLine": str(config.launcher_path)}]
        )

    def starter(_config):
        state["running"] = True
        return True

    monkeypatch.setattr(self_update, "load_dtssh_config", lambda local_app_data=None: config)
    steps = self_update.ensure_watchdog(
        process_lister=process_lister,
        launcher_starter=starter,
        sleeper=lambda _seconds: None,
        now=iter([0.0, 0.0]).__next__,
    )
    assert steps[0].status == "changed"
    assert "started dtssh host launcher" in steps[0].detail


def test_fast_forward_repo_skips_dirty_and_diverged(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()

    def dirty_runner(argv, *, cwd=None, timeout=0):
        if argv[:2] == ["git", "status"]:
            return self_update.CommandResult(list(argv), 0, " M tracked.txt\n", "")
        raise AssertionError(argv)

    dirty = self_update.fast_forward_repo(repo, runner=dirty_runner)
    assert dirty.status == "skipped"
    assert "dirty" in dirty.detail

    def diverged_runner(argv, *, cwd=None, timeout=0):
        mapping = {
            ("git", "status"): self_update.CommandResult(list(argv), 0, "", ""),
            ("git", "rev-parse"): self_update.CommandResult(list(argv), 0, "origin/main\n", ""),
            ("git", "fetch"): self_update.CommandResult(list(argv), 0, "", ""),
            ("git", "rev-list"): self_update.CommandResult(list(argv), 0, "1\t2\n", ""),
        }
        for prefix, result in mapping.items():
            if tuple(argv[: len(prefix)]) == prefix:
                return result
        raise AssertionError(argv)

    diverged = self_update.fast_forward_repo(repo, runner=diverged_runner)
    assert diverged.status == "skipped"
    assert "diverged" in diverged.detail


def test_sweep_defers_when_live_session_is_present(tmp_path, monkeypatch):
    monkeypatch.setattr(self_update.sys, "platform", "win32")
    monkeypatch.setattr(self_update, "_WindowsMutex", lambda _name: _FakeMutex("acquired"))
    repo = type("Repo", (), {"path": tmp_path / "repo"})
    repo.path.mkdir()

    def runner(argv, *, cwd=None, timeout=0):
        mapping = {
            ("git", "status"): self_update.CommandResult(list(argv), 0, "", ""),
            ("git", "rev-parse"): self_update.CommandResult(list(argv), 0, "origin/main\n", ""),
            ("git", "fetch"): self_update.CommandResult(list(argv), 0, "", ""),
            ("git", "rev-list"): self_update.CommandResult(list(argv), 0, "0\t0\n", ""),
        }
        for prefix, result in mapping.items():
            if tuple(argv[: len(prefix)]) == prefix:
                return result
        raise AssertionError(argv)

    result = self_update.run_tier(
        "sweep",
        opted_in=True,
        discovered_repos=[repo],
        runner=runner,
        worktree_lister=lambda: [
            {
                "id": "wt-1",
                "title": "busy worktree",
                "live_rest": "busy",
                "reciprocal_relation": {"binding": {"state": "bound-here"}},
            }
        ],
        home=tmp_path,
    )
    assert result.status == "deferred"
    assert any(step.name == "pre-mutation" for step in result.steps)


def test_plan_includes_self_update_observed_status(tmp_path, monkeypatch):
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    self_update.write_status(
        tmp_path,
        "watchdog",
        attempt="2026-09-14T09:00:00+00:00",
        success="2026-09-14T09:05:00+00:00",
    )
    package = _pkg(
        tmp_path,
        "acme/watchdog",
        [{"type": "self-update", "tier": "watchdog"}],
    )
    current = plan([package], "box-1", "windows")
    resource = next(item for item in current.resources if item["type"] == "self-update")
    assert resource["observed"] == {
        "last_attempt": "2026-09-14T09:00:00+00:00",
        "last_success": "2026-09-14T09:05:00+00:00",
    }
    assert "last-attempt=2026-09-14T09:00:00+00:00" in resource["summary"]


def test_cli_self_update_run_emits_json(monkeypatch, capsys):
    monkeypatch.setattr(
        cli,
        "_resolve_machine_identity",
        lambda args: type(
            "Identity",
            (),
            {"canonical": "box-1", "accepted": ("box-1",), "warnings": [], "raw": "box-1"},
        )(),
    )
    monkeypatch.setattr(cli, "_emit_identity_warnings", lambda identity: None)
    monkeypatch.setattr(cli, "_self_update_packages", lambda machine, accepted_machines=None: [])
    monkeypatch.setattr(
        cli._reconcile, "resolve_union", lambda packages, machine, accepted_machines=None: []
    )
    monkeypatch.setattr(cli._validator, "validate", lambda resolved, machine, plat=None: [])
    monkeypatch.setattr(
        cli._resources, "resolve_resources", lambda resolved, machine, plat: ([], [])
    )
    monkeypatch.setattr(cli._discover, "discover", lambda machine, accepted_machines=None: [])
    monkeypatch.setattr(
        cli._self_update,
        "run_tier",
        lambda tier, **kwargs: self_update.RunResult(
            tier=tier,
            status="noop",
            opted_in=False,
            detail="tier 'watchdog' is not opted in",
        ),
    )
    rc = cli.main(["self-update", "run", "--tier", "watchdog", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["tier"] == "watchdog"
    assert payload["status"] == "noop"


def test_reconcile_task_registers_new_opt_in(monkeypatch, tmp_path):
    monkeypatch.setattr(self_update.sys, "platform", "win32")
    monkeypatch.setattr(self_update, "_WindowsMutex", lambda _name: _FakeMutex("acquired"))
    state = {"present": False}

    def query(tier, *, runner=None, home=None):
        return _task_snapshot(tier, present=state["present"])

    def register(tier, *, runner=None, home=None):
        state["present"] = True
        return self_update.CommandResult(["pwsh"], 0, "", "")

    monkeypatch.setattr(self_update, "query_scheduled_task", query)
    monkeypatch.setattr(self_update, "register_scheduled_task", register)
    result = self_update.reconcile_scheduled_task("watchdog", desired_present=True, home=tmp_path)
    assert result.status == "changed"
    assert result.changed is True
    assert result.snapshot is not None and result.snapshot.present is True


def test_reconcile_task_defers_to_elevated_install_when_registration_is_denied(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(self_update.sys, "platform", "win32")
    monkeypatch.setattr(self_update, "_WindowsMutex", lambda _name: _FakeMutex("acquired"))
    monkeypatch.setattr(
        self_update,
        "query_scheduled_task",
        lambda tier, **kwargs: _task_snapshot(tier, present=False, matching=False),
    )
    monkeypatch.setattr(
        self_update,
        "register_scheduled_task",
        lambda tier, **kwargs: self_update.CommandResult(["pwsh"], 1, "", "Access is denied."),
    )
    result = self_update.reconcile_scheduled_task("watchdog", desired_present=True, home=tmp_path)
    assert result.status == "deferred"
    assert result.ok is False
    assert result.commands == [["agent-machines", "self-update", "install", "--tier", "watchdog"]]
    assert "run once from an elevated PowerShell" in result.detail


def test_reconcile_task_removes_opted_out_task_without_retry_prompt(monkeypatch, tmp_path):
    monkeypatch.setattr(self_update.sys, "platform", "win32")
    monkeypatch.setattr(self_update, "_WindowsMutex", lambda _name: _FakeMutex("acquired"))
    monkeypatch.setattr(
        self_update,
        "query_scheduled_task",
        lambda tier, **kwargs: _task_snapshot(tier, present=True, matching=True, enabled=True),
    )
    calls: list[str] = []

    def unregister(tier, *, runner=None):
        calls.append(tier)
        return self_update.CommandResult(["pwsh"], 0, "", "")

    monkeypatch.setattr(self_update, "unregister_scheduled_task", unregister)
    result = self_update.reconcile_scheduled_task("sweep", desired_present=False, home=tmp_path)
    assert result.status == "changed"
    assert result.changed is True
    assert calls == ["sweep"]
    assert "elevated PowerShell" not in result.detail


def test_cli_self_update_install_skips_non_opted_in_tier_without_registration(
    monkeypatch, capsys
):
    resource = type("Resource", (), {"desired": {"state": "absent"}})()
    monkeypatch.setattr(
        cli,
        "_resolve_self_update_resources",
        lambda args: (None, "box-1", {"watchdog": resource}),
    )

    def fail(*args, **kwargs):
        raise AssertionError("install should not be attempted for opted-out tiers")

    monkeypatch.setattr(cli._self_update, "reconcile_scheduled_task", fail)
    rc = cli.main(["self-update", "install", "--tier", "watchdog", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["tiers"][0]["status"] == "skipped"
    assert "not opted in" in payload["tiers"][0]["detail"]


def test_cli_self_update_status_emits_json(monkeypatch, capsys):
    resource = type("Resource", (), {"desired": {"state": "present"}})()
    monkeypatch.setattr(
        cli,
        "_resolve_self_update_resources",
        lambda args: (None, "box-1", {"watchdog": resource}),
    )
    monkeypatch.setattr(
        cli._self_update,
        "scheduled_task_status",
        lambda tier, opted_in: self_update.ScheduledTaskStatus(
            tier=tier,
            opted_in=opted_in,
            task_name=self_update.TIER_SPECS[tier].task_name,
            registered=True,
            enabled=True,
            matching=True,
            state="Ready",
            detail="Scheduled Task is registered",
            last_attempt="2026-09-14T09:00:00+00:00",
            last_success="2026-09-14T09:05:00+00:00",
        ),
    )
    rc = cli.main(["self-update", "status", "--tier", "watchdog", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["tiers"][0]["registered"] is True
    assert payload["tiers"][0]["opted_in"] is True
