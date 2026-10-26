"""Setup reports native activation honestly and holds the handover transaction."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from unittest.mock import Mock

import pytest
from zdd.cutover_lock import CutoverLock, CutoverLockedError

from worktree_manager import automatic_updates_autostart as startup
from worktree_manager import automatic_updates_cli as cli
from worktree_manager import automatic_updates_legacy as legacy
from worktree_manager import automatic_updates_policy as policy
from worktree_manager import automatic_updates_process as process


@pytest.fixture
def enrollment(tmp_path: Path, monkeypatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setattr(cli, "_os_account_home", lambda: home)
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    monkeypatch.delenv("AGENT_HOME", raising=False)
    monkeypatch.setattr(process, "selected_slot", lambda root: tmp_path / "selected")
    monkeypatch.setattr(legacy, "require_legacy_retired", lambda: None)
    monkeypatch.setattr(process, "worker_status", lambda root: {"running": False})
    monkeypatch.setattr(cli, "require_registered_project", lambda name: None)
    return home / ".worktree-manager"


def _enable(root: Path, *, harness: bool = False) -> list[str]:
    args = ["enroll", "--harness", "example"] if harness else ["enable"]
    return [*args, "--project", "example", "--root", str(root), "--apply", "--json"]


@pytest.mark.parametrize("binding", ["AGENT_HOME", "COPILOT_EXTENSIONS_CONTEXT"])
@pytest.mark.parametrize("harness", [False, True])
def test_unpersisted_installation_binding_blocks_before_any_write(
    enrollment: Path, monkeypatch: pytest.MonkeyPatch, capsys,
    binding: str, harness: bool,
) -> None:
    monkeypatch.setenv(binding, str(enrollment.parent / "session-only"))
    register, ensure = Mock(), Mock()
    monkeypatch.setattr(startup, "register_autostart", register)
    monkeypatch.setattr(process, "ensure_worker", ensure)
    assert cli.main(_enable(enrollment, harness=harness)) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["state"] == "blocked"
    assert binding in result["reason"] and "durable enrollment binding" in result["reason"]
    assert not enrollment.exists()
    register.assert_not_called()
    ensure.assert_not_called()


@pytest.mark.parametrize("variable", ["HOME", "USERPROFILE"])
@pytest.mark.parametrize("use_redirected_root", [False, True])
def test_process_profile_override_cannot_hide_enrollment_from_legacy_controllers(
    enrollment: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys,
    variable: str, use_redirected_root: bool,
) -> None:
    redirected = tmp_path / "process-only-home"
    monkeypatch.setenv(variable, str(redirected))
    root = redirected / ".worktree-manager" if use_redirected_root else enrollment
    register, ensure = Mock(), Mock()
    monkeypatch.setattr(startup, "register_autostart", register)
    monkeypatch.setattr(process, "ensure_worker", ensure)
    assert cli.main(_enable(root, harness=True)) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["state"] == "blocked"
    assert "durable binding" in result["reason"]
    assert not root.exists()
    register.assert_not_called()
    ensure.assert_not_called()


@pytest.mark.skipif(process.os.name == "nt", reason="passwd-database profile")
def test_posix_enrollment_profile_comes_from_account_not_process_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import pwd
    from types import SimpleNamespace

    account = tmp_path / "account"
    monkeypatch.setenv("HOME", str(tmp_path / "process-only"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "other-process-only"))
    monkeypatch.setattr(pwd, "getpwuid", lambda uid: SimpleNamespace(pw_dir=str(account)))
    assert cli._os_account_home() == account


@pytest.mark.parametrize("status", [0, -1])
def test_windows_account_profile_uses_existing_shell_api_without_environment_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: int,
) -> None:
    import ctypes
    from types import SimpleNamespace

    account = tmp_path / "account"

    def resolve(window, folder, token, flags, buffer):
        assert (window, folder, token, flags) == (None, 40, None, 0)
        buffer.value = str(account)
        return status

    native = Mock(side_effect=resolve)
    load = Mock(return_value=SimpleNamespace(SHGetFolderPathW=native))
    monkeypatch.setattr(cli, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(ctypes, "WinDLL", load, raising=False)
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "process-only"))
    if status:
        with pytest.raises(policy.PolicyError, match="operating-system account profile"):
            cli._os_account_home()
    else:
        assert cli._os_account_home() == account
    load.assert_called_once_with("shell32", winmode=0x800)
    assert native.restype == ctypes.wintypes.LONG
    assert len(native.argtypes) == 5


def test_user_disable_still_precedes_unsupported_setup_binding(
    enrollment: Path, monkeypatch: pytest.MonkeyPatch, capsys,
) -> None:
    policy.configure(enrollment, owner="disabled", provenance="user")
    monkeypatch.setenv("AGENT_HOME", str(enrollment.parent / "session-only"))
    register = Mock()
    monkeypatch.setattr(startup, "register_autostart", register)
    assert cli.main(_enable(enrollment, harness=True)) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "user-disabled"
    assert policy.load_policy(enrollment).user_disabled is True
    register.assert_not_called()


def test_enrollment_holds_inventory_and_consent_transaction(enrollment, monkeypatch, capsys) -> None:
    def inventory() -> None:
        with pytest.raises(CutoverLockedError):
            with CutoverLock(policy.state_dir(enrollment) / "update-transaction"):
                pytest.fail("legacy registration raced the inventory")

    monkeypatch.setattr(legacy, "require_legacy_retired", inventory)
    monkeypatch.setattr(startup, "register_autostart", lambda root: {
        "registered": True, "activation": "pending", "blockers": ["worker-readiness-not-checked"],
    })
    monkeypatch.setattr(process, "ensure_worker", lambda root: {"running": True})
    assert cli.main(_enable(enrollment, harness=True)) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["policy"]["owner"] == "manager"
    assert result["policy"]["provenance"] == "harness:example"
    assert result["startup"]["activation"] == "running"
    assert result["startup"]["worker_readiness"] == "verified"


def test_registration_block_is_not_hidden_by_an_existing_worker(enrollment, monkeypatch, capsys) -> None:
    monkeypatch.setattr(startup, "register_autostart", lambda root: {
        "registered": True, "activation": "blocked", "blockers": ["unit-is-foreign"],
    })
    monkeypatch.setattr(process, "worker_status", lambda root: {"running": True})

    def forbidden(*args, **kwargs):
        pytest.fail("blocked native registration must not spawn a fallback")

    monkeypatch.setattr(process, "ensure_worker", forbidden)
    assert cli.main(_enable(enrollment)) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["state"] == "blocked"
    assert result["startup"]["blockers"] == ["unit-is-foreign"]
    assert result["worker"]["running"]


def test_activation_pending_does_not_claim_running(enrollment, monkeypatch, capsys) -> None:
    monkeypatch.setattr(startup, "register_autostart", lambda root: {
        "registered": True, "activation": "pending", "blockers": ["worker-readiness-not-checked"],
    })
    monkeypatch.setattr(process, "ensure_worker", lambda root: {
        "running": False, "state": "activation-pending",
    })
    assert cli.main(_enable(enrollment)) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["startup"]["activation"] == "pending"
    assert result["worker"]["running"] is False


@pytest.mark.skipif(process.os.name == "nt", reason="native Linux activation branch")
def test_linux_ensure_activates_native_unit_without_detached_fallback(enrollment, monkeypatch) -> None:
    policy.configure(enrollment, owner="manager", provenance="user", project="example")
    running = False
    activated = []
    monkeypatch.setattr(startup, "inspect_autostart", lambda root: {
        "registered": True, "activation": "pending",
    })
    monkeypatch.setattr(process, "worker_status", lambda root: {"running": running})

    def activate(root: Path) -> dict:
        nonlocal running
        activated.append(root)
        running = True
        return {"activation": "pending"}

    def forbidden(*args, **kwargs):
        pytest.fail("a registered Linux unit owns this process lifetime")

    monkeypatch.setattr(startup, "activate_autostart", activate)
    monkeypatch.setattr(process, "spawn_worker", forbidden)
    assert process.ensure_worker(enrollment)["running"]
    assert activated == [enrollment]


@pytest.mark.skipif(process.os.name == "nt", reason="native Linux activation branch")
def test_unhosted_worker_is_a_native_activation_blocker(enrollment, monkeypatch) -> None:
    policy.configure(enrollment, owner="manager", provenance="user", project="example")
    monkeypatch.setattr(startup, "inspect_autostart", lambda root: {
        "registered": True, "service_active": False,
    })
    monkeypatch.setattr(process, "worker_status", lambda root: {
        "running": True, "worker_id": "unhosted",
    })
    result = process.ensure_worker(enrollment)
    assert result["running"]
    assert result["activation"] == "blocked"
    assert "outside the registered native unit" in result["reason"]


def test_invalid_preview_is_not_a_success_shaped_plan(tmp_path: Path, capsys) -> None:
    assert cli.main([
        "enable", "--root", str(tmp_path / "absent"), "--interval-seconds", "0", "--json",
    ]) == 1
    assert json.loads(capsys.readouterr().out)["state"] == "blocked"
    assert not (tmp_path / "absent").exists()


@pytest.mark.parametrize("running", [False, True])
def test_ensure_propagates_inspection_block_before_spawn_or_success(
    enrollment, monkeypatch, running: bool,
) -> None:
    policy.configure(enrollment, owner="manager", provenance="user", project="example")
    monkeypatch.setattr(startup, "inspect_autostart", lambda root: {
        "registered": False, "activation": "blocked", "blockers": ["foreign-startup-entry"],
    })
    monkeypatch.setattr(process, "worker_status", lambda root: {"running": running})

    def forbidden(*args, **kwargs):
        pytest.fail("ownership failure cannot authorize a detached fallback")

    monkeypatch.setattr(process, "spawn_worker", forbidden)
    monkeypatch.setattr(startup, "activate_autostart", forbidden)
    result = process.ensure_worker(enrollment)
    assert result["running"] is running
    assert result["activation"] == "blocked"
    assert result["reason"] == ["foreign-startup-entry"]


def test_missing_project_refuses_before_consent_or_startup(enrollment, monkeypatch, capsys) -> None:
    def forbidden(*args, **kwargs):
        pytest.fail("missing project cannot authorize startup")

    monkeypatch.setattr(startup, "register_autostart", forbidden)
    assert cli.main(["enable", "--root", str(enrollment), "--apply", "--json"]) == 1
    assert "registered project" in json.loads(capsys.readouterr().out)["reason"]
    assert not (enrollment / policy.POLICY_NAME).exists()


def test_project_verification_reads_strict_registration_only(monkeypatch) -> None:
    from worktree_manager import harness_state

    calls = []

    def registry(**kwargs):
        calls.append(kwargs)
        return {"projects": {"example": {"config_dir": "owned"}}}

    monkeypatch.setattr(harness_state, "projects_registry", registry)
    cli.require_registered_project("example")
    assert calls == [{"strict": True}]
    with pytest.raises(policy.PolicyError, match="not registered"):
        cli.require_registered_project("unknown")


def test_native_unit_has_deliberate_user_tool_path_without_checkout_paths(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("PATH", "/untrusted-checkout/tools")
    body = startup._unit(tmp_path, "owned", ["/usr/bin/python3", "owned-startup.py"])
    assert f"PATH={tmp_path}/.local/bin:/usr/local/sbin:/usr/local/bin:" in body
    assert "/usr/bin:/sbin:/bin" in body
    assert "/untrusted-checkout" not in body


def test_alternate_root_cannot_hide_owner_from_legacy_controllers(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    root = tmp_path / "alternate-manager"
    assert cli.main([
        "enable", "--root", str(root), "--project", "example", "--json",
    ]) == 1
    assert "durable binding shared with legacy" in json.loads(capsys.readouterr().out)["reason"]
    assert not root.exists()


def test_disable_retains_user_preference_but_reports_residual_legacy_jobs(
    enrollment, monkeypatch, capsys,
) -> None:
    def residual() -> None:
        raise policy.PolicyError("an inactive pre-flag fleet task still exists")

    monkeypatch.setattr(legacy, "require_legacy_retired", residual)
    assert cli.main(["disable", "--root", str(enrollment), "--apply", "--json"]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["state"] == "blocked"
    assert result["policy"]["owner"] == "disabled"
    assert result["policy"]["user_disabled"] is True
    assert policy.load_policy(enrollment).user_disabled
    assert "pre-flag fleet task" in result["reason"]


def test_effective_user_disable_is_rechecked_before_startup(enrollment, monkeypatch, capsys) -> None:
    original = cli.configure

    def user_disables_before_publication(root: Path, **kwargs):
        policy.atomic_json(root / policy.POLICY_NAME, asdict(policy.Policy(
            provenance="user", user_disabled=True,
        )))
        return original(root, **kwargs)

    def forbidden(*args, **kwargs):
        pytest.fail("effective user disable must prevent startup registration")

    monkeypatch.setattr(cli, "configure", user_disables_before_publication)
    monkeypatch.setattr(startup, "register_autostart", forbidden)
    assert cli.main(_enable(enrollment, harness=True)) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "user-disabled"
