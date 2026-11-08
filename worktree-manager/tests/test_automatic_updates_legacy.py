"""Read-only owner handover never substitutes an unknown inventory for absence."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from worktree_manager import agent_plugin_runtime
from worktree_manager import automatic_updates_legacy as legacy
from worktree_manager.automatic_updates_policy import PolicyError


def _inventory() -> dict:
    return {
        "schema": "copilot-extensions.legacy-update-inventory", "version": 1,
        "ok": True, "adoption_ready": True,
        "legacy_artifacts": [
            {
                "kind": kind, "present": False, "in_flight": False,
                "retirement_required": False, "blocks_adoption": False,
                "inspection_complete": True, "detail": "absent",
            }
            for kind in ("fleet", "self")
        ],
    }


@pytest.fixture
def installed(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(
        agent_plugin_runtime, "resolve_installed_plugin_command",
        lambda plugin: ["installed-python", "-m", "agent_machines"],
    )
    monkeypatch.setattr(agent_plugin_runtime, "legacy_plugin_root", lambda plugin: tmp_path / plugin)
    monkeypatch.setattr(legacy, "default_root", lambda: tmp_path, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    return tmp_path


def test_public_inventory_uses_exact_installed_command(installed, monkeypatch) -> None:
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=0, stdout=json.dumps(_inventory()), stderr="")

    monkeypatch.setattr(legacy.subprocess, "run", run)
    assert legacy.installed_inventory()["adoption_ready"]
    assert calls[0][0] == [
        "installed-python", "-m", "agent_machines", "fleet-update", "inventory", "--json",
    ]
    assert calls[0][1]["env"]["GIT_TERMINAL_PROMPT"] == "0"


@pytest.mark.parametrize("change", [
    {"version": True}, {"version": 2}, {"ok": False}, {"adoption_ready": False},
    {"legacy_artifacts": []}, {"legacy_artifacts": [{"kind": []}, {"kind": "self"}]},
])
def test_incomplete_public_inventory_blocks_adoption(installed, monkeypatch, change) -> None:
    monkeypatch.setattr(
        legacy.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(
            returncode=0, stdout=json.dumps({**_inventory(), **change}), stderr="",
        ),
    )
    with pytest.raises(PolicyError):
        legacy.require_legacy_retired()


@pytest.mark.parametrize("kind,key,value", [
    ("fleet", "present", True), ("fleet", "retirement_required", True),
    ("self", "in_flight", True), ("self", "in_flight", None),
    ("self", "blocks_adoption", True),
])
def test_ready_claim_cannot_override_artifact_evidence(
    installed, monkeypatch, kind: str, key: str, value: object,
) -> None:
    payload = _inventory()
    next(item for item in payload["legacy_artifacts"] if item["kind"] == kind)[key] = value
    monkeypatch.setattr(
        legacy.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(
            returncode=0, stdout=json.dumps(payload), stderr="",
        ),
    )
    with pytest.raises(PolicyError, match="handover is blocked"):
        legacy.require_legacy_retired()


def test_older_installed_cli_is_unavailable_not_absent(installed, monkeypatch) -> None:
    monkeypatch.setattr(
        legacy.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(
            returncode=2, stdout="", stderr="invalid choice: inventory",
        ),
    )
    with pytest.raises(PolicyError, match="canonical installer"):
        legacy.require_legacy_retired()


def test_corrupt_install_receipt_is_not_optional_absence(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "agent-machines"
    root.mkdir()
    (root / "current-version").write_text("1.0.0", encoding="utf-8")
    monkeypatch.setattr(agent_plugin_runtime, "resolve_installed_plugin_command", lambda plugin: None)
    monkeypatch.setattr(agent_plugin_runtime, "legacy_plugin_root", lambda plugin: root)
    with pytest.raises(PolicyError, match="inventory runtime is unavailable"):
        legacy.require_legacy_retired()


def test_absent_optional_plugin_still_checks_native_triggers(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(agent_plugin_runtime, "resolve_installed_plugin_command", lambda plugin: None)
    monkeypatch.setattr(agent_plugin_runtime, "legacy_plugin_root", lambda plugin: tmp_path / plugin)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setattr(legacy, "legacy_triggers", lambda: ["owned legacy timer"])
    with pytest.raises(PolicyError, match="retirement"):
        legacy.require_legacy_retired()


def test_orphaned_manual_lock_is_not_ignored(tmp_path: Path, monkeypatch) -> None:
    lock = tmp_path / ".agent-machines" / "self-update" / "sweep.lock.json"
    lock.parent.mkdir(parents=True)
    lock.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(agent_plugin_runtime, "resolve_installed_plugin_command", lambda plugin: None)
    monkeypatch.setattr(agent_plugin_runtime, "legacy_plugin_root", lambda plugin: tmp_path / plugin)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    with pytest.raises(PolicyError, match="requires verification"):
        legacy.require_legacy_retired()


@pytest.fixture
def native_inventory(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(agent_plugin_runtime, "resolve_installed_plugin_command", lambda plugin: None)
    monkeypatch.setattr(agent_plugin_runtime, "legacy_plugin_root", lambda plugin: tmp_path / plugin)
    monkeypatch.setattr(legacy, "sys", SimpleNamespace(platform="linux"))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    calls = []
    replies = {
        unit: SimpleNamespace(
            returncode=0, stdout="LoadState=not-found\nActiveState=inactive\nJob=0\n", stderr="",
        )
        for unit in (f"{legacy.LEGACY_NAME}.timer", f"{legacy.LEGACY_NAME}.service",
                     f"{legacy.SELF_NAME}.service")
    }

    def run(argv, **kwargs):
        unit = argv[3]
        assert argv == [
            "systemctl", "--user", "show", unit, "--property=LoadState,ActiveState,Job", "--no-pager",
        ]
        calls.append(unit)
        reply = replies[unit]
        if isinstance(reply, BaseException):
            raise reply
        return reply

    monkeypatch.setattr(legacy.subprocess, "run", run)
    return calls, replies


@pytest.mark.parametrize("state", ["active", "activating", "deactivating", "reloading"])
def test_cached_fleet_timer_blocks_even_with_inactive_services(
    native_inventory, tmp_path: Path, state: str,
) -> None:
    calls, replies = native_inventory
    timer = f"{legacy.LEGACY_NAME}.timer"
    replies[timer].stdout = f"LoadState=loaded\nActiveState={state}\nJob=0\n"
    before = list(tmp_path.rglob("*"))
    with pytest.raises(PolicyError, match=rf"{timer} \({state}\)"):
        legacy.require_legacy_retired()
    assert calls == list(replies)
    assert list(tmp_path.rglob("*")) == before


@pytest.mark.parametrize("stdout", [
    "", "LoadState=loaded\n", "LoadState=loaded\nActiveState=unknown\nJob=0\n",
    "LoadState=unknown\nActiveState=inactive\nJob=0\n",
])
def test_unverifiable_cached_timer_blocks_adoption(native_inventory, stdout: str) -> None:
    calls, replies = native_inventory
    timer = f"{legacy.LEGACY_NAME}.timer"
    replies[timer].stdout = stdout
    with pytest.raises(PolicyError, match="activity could not be verified"):
        legacy.require_legacy_retired()
    assert calls == [timer]


@pytest.mark.parametrize("load_state", ["not-found", "loaded", "masked"])
@pytest.mark.parametrize("active_state", ["inactive", "failed"])
def test_known_inactive_cached_units_allow_adoption(
    native_inventory, load_state: str, active_state: str,
) -> None:
    calls, replies = native_inventory
    for reply in replies.values():
        reply.stdout = f"LoadState={load_state}\nActiveState={active_state}\nJob=0\n"
    legacy.require_legacy_retired()
    assert calls == list(replies)


@pytest.mark.parametrize("unit", [
    f"{legacy.LEGACY_NAME}.timer", f"{legacy.LEGACY_NAME}.service", f"{legacy.SELF_NAME}.service",
])
def test_inactive_unit_with_queued_start_blocks_adoption(native_inventory, unit: str) -> None:
    calls, replies = native_inventory
    replies[unit].stdout = "LoadState=loaded\nActiveState=inactive\nJob=42\n"
    with pytest.raises(PolicyError, match=rf"{unit} \(pending job 42\)"):
        legacy.require_legacy_retired()
    assert calls == list(replies)


@pytest.mark.parametrize("job", [None, "", "unknown", "-1", "1 start", "42\nJob=0"])
def test_unverifiable_unit_job_blocks_adoption(native_inventory, job: str | None) -> None:
    calls, replies = native_inventory
    unit = f"{legacy.LEGACY_NAME}.service"
    replies[unit].stdout = "LoadState=loaded\nActiveState=inactive\n"
    if job is not None:
        replies[unit].stdout += f"Job={job}\n"
    with pytest.raises(PolicyError, match="could not be verified"):
        legacy.require_legacy_retired()
    assert calls == list(replies)[:2]


def test_queued_legacy_start_refuses_before_enrollment(
    native_inventory, tmp_path: Path, monkeypatch, capsys,
) -> None:
    from worktree_manager import automatic_updates_autostart as startup
    from worktree_manager import automatic_updates_cli as cli, automatic_updates_process as process
    from worktree_manager.automatic_updates_policy import POLICY_NAME

    calls, replies = native_inventory
    replies[f"{legacy.LEGACY_NAME}.service"].stdout = "LoadState=loaded\nActiveState=inactive\nJob=42\n"
    monkeypatch.setattr(cli, "require_legacy_root_binding", lambda root: None)
    monkeypatch.setattr(cli, "require_registered_project", lambda name: None)
    monkeypatch.setattr(process, "selected_slot", lambda root: tmp_path / "slot")
    monkeypatch.setattr(
        startup, "register_autostart", lambda root: pytest.fail("queued legacy job enrolled startup"),
    )
    monkeypatch.setattr(
        process, "ensure_worker", lambda root: pytest.fail("queued legacy job launched a worker"),
    )
    root = tmp_path / "manager"
    assert cli.main(["enable", "--project", "example", "--root", str(root), "--apply", "--json"]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["state"] == "blocked" and "pending job 42" in result["reason"]
    assert not (root / POLICY_NAME).exists()
    assert calls == list(replies)


@pytest.mark.parametrize("failure", [
    OSError("inspection denied"), subprocess.TimeoutExpired("systemctl", 10),
    SimpleNamespace(returncode=1, stdout="", stderr="user bus unavailable"),
])
def test_timer_inspection_failure_blocks_adoption(native_inventory, failure) -> None:
    calls, replies = native_inventory
    timer = f"{legacy.LEGACY_NAME}.timer"
    replies[timer] = failure
    with pytest.raises(PolicyError, match="legacy user-unit inventory"):
        legacy.require_legacy_retired()
    assert calls == [timer]


def test_missing_systemctl_preserves_optional_absence(native_inventory) -> None:
    calls, replies = native_inventory
    replies[f"{legacy.LEGACY_NAME}.timer"] = FileNotFoundError("systemctl")
    legacy.require_legacy_retired()
    assert calls == [f"{legacy.LEGACY_NAME}.timer"]


def test_missing_systemctl_does_not_discard_observed_timer(native_inventory) -> None:
    _calls, replies = native_inventory
    replies[f"{legacy.LEGACY_NAME}.timer"].stdout = "LoadState=loaded\nActiveState=active\nJob=0\n"
    replies[f"{legacy.LEGACY_NAME}.service"] = FileNotFoundError("systemctl")
    with pytest.raises(PolicyError, match="timer"):
        legacy.require_legacy_retired()


def test_default_directory_timer_blocks_with_custom_xdg_config(
    native_inventory, tmp_path: Path,
) -> None:
    calls, _replies = native_inventory
    timer = tmp_path / ".config" / "systemd" / "user" / f"{legacy.LEGACY_NAME}.timer"
    timer.parent.mkdir(parents=True)
    timer.write_text("[Timer]\nOnBootSec=1min\n", encoding="utf-8")
    before = timer.read_bytes()
    with pytest.raises(PolicyError, match="retirement"):
        legacy.require_legacy_retired()
    assert calls == []
    assert timer.read_bytes() == before
