"""The Manager edits the existing scoped switch through its canonical owner."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from worktree_manager import bootstrap_policy as policy
from worktree_manager import maintenance_policy, manager_config, self_install


@pytest.fixture
def switch_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("WORKTREE_MANAGER_ROOT", str(tmp_path / "manager"))
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    monkeypatch.delenv("BOOTSTRAP_KILLSWITCH_STATE_FILE", raising=False)
    return tmp_path


def test_read_only_default_has_an_actionable_existing_controller(switch_home: Path) -> None:
    state = policy.inspect_policy()
    assert state.enabled is True and not state.error and len(state.paths) == 2
    row = policy.configuration_row()
    assert row["enabled"] == "ON" and row["bootstrap_control"]
    assert not list(switch_home.iterdir())
    command = policy._guard_command()
    assert Path(command[-1]).is_file()
    assert Path(command[-1]).name.startswith("bootstrap-killswitch-guard.")


def test_real_canonical_controller_disables_then_clears_central_and_legacy(
    switch_home: Path,
) -> None:
    first = policy.inspect_policy()
    blocked = policy.set_enabled(False, expected=first.revision)
    assert blocked.enabled is False
    assert json.loads(first.paths[0].read_text(encoding="utf-8-sig"))["active"] is True
    legacy = first.paths[1]
    legacy.write_text('{"active":true,"reason":"retained legacy"}', encoding="utf-8")
    current = policy.inspect_policy()
    allowed = policy.set_enabled(True, expected=current.revision)
    assert allowed.enabled is True
    assert not any(path.exists() for path in first.paths)
    assert "cleared" in allowed.detail


def test_switch_change_never_enables_the_independent_manager_audit_veto(
    switch_home: Path,
) -> None:
    root = switch_home / "manager"
    root.mkdir()
    config = root / "config.toml"
    config.write_text("[maintenance]\nautomatic = false\n", encoding="utf-8")
    baseline = config.read_bytes()
    blocked = policy.set_enabled(False, expected=policy.inspect_policy().revision)
    policy.set_enabled(True, expected=blocked.revision)
    assert config.read_bytes() == baseline
    assert manager_config.load_config().maintenance.automatic is False
    assert policy.configuration_row()["activity"] == "blocked"
    assert "maintenance.automatic" in maintenance_policy.update_refusal(["--auto"])


def test_explicit_override_does_not_change_default_or_legacy_switches(
    switch_home: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    default = policy.inspect_policy().paths
    for path in default:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"active":true}', encoding="utf-8")
    original = {path: path.read_bytes() for path in default}
    override = switch_home / "selected" / "switch.json"
    monkeypatch.setenv("BOOTSTRAP_KILLSWITCH_STATE_FILE", str(override))
    state = policy.inspect_policy()
    assert state.paths == (override,)
    disabled = policy.set_enabled(False, expected=state.revision)
    policy.set_enabled(True, expected=disabled.revision)
    assert not override.exists()
    assert {path: path.read_bytes() for path in default} == original


def test_stale_switch_never_calls_controller(
    switch_home: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import agent_procutil.owned_process

    before = policy.inspect_policy()
    before.paths[0].parent.mkdir(parents=True)
    before.paths[0].write_text('{"active":true,"reason":"new owner"}', encoding="utf-8")
    baseline = before.paths[0].read_bytes()
    monkeypatch.setattr(
        agent_procutil.owned_process, "run_caller_owned",
        lambda *args, **kwargs: pytest.fail("stale switch executed a controller"),
    )
    with pytest.raises(manager_config.ManagerConfigError, match="changed"):
        policy.set_enabled(True, expected=before.revision)
    assert before.paths[0].read_bytes() == baseline


def test_independent_cell_neither_reads_nor_changes_default_switch(
    switch_home: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = policy.inspect_policy()
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", str(switch_home / "independent/install.json"))
    monkeypatch.setattr(policy, "_read", lambda *args: pytest.fail("cell read global switch"))
    state = policy.inspect_policy()
    assert state.enabled is None and state.scope == "independent-cell"
    assert not policy.configuration_row()["bootstrap_control"]
    with pytest.raises(manager_config.ManagerConfigError, match="Not applicable"):
        policy.set_enabled(True, expected=before.revision)
    assert not list(switch_home.iterdir())


@pytest.mark.parametrize("value", ['{"active":"false"}', "{"])
def test_invalid_switch_is_visible_not_silently_overwritten(
    switch_home: Path, value: str,
) -> None:
    path = policy.inspect_policy().paths[0]
    path.parent.mkdir(parents=True)
    path.write_text(value, encoding="utf-8")
    state = policy.inspect_policy()
    assert state.enabled is None and state.error
    row = policy.configuration_row()
    assert row["enabled"] == "unknown" and row["activity"] == "unknown"
    assert not row["bootstrap_control"]
    with pytest.raises(manager_config.ManagerConfigError):
        policy.set_enabled(True, expected="0" * 64)
    assert path.read_text(encoding="utf-8") == value


@pytest.mark.parametrize("code", [0, 7])
def test_controller_exit_is_not_a_state_change_witness(
    switch_home: Path, monkeypatch: pytest.MonkeyPatch, code: int,
) -> None:
    import agent_procutil.owned_process

    calls = []
    monkeypatch.setattr(
        agent_procutil.owned_process, "run_caller_owned",
        lambda argv, **kwargs: calls.append((argv, kwargs)) or SimpleNamespace(
            returncode=code, stdout="controller fixture", stderr="",
        ),
    )
    with pytest.raises(manager_config.ManagerConfigError):
        policy.set_enabled(False, expected=policy.inspect_policy().revision)
    assert len(calls) == 1
    assert policy.inspect_policy().enabled is True
    assert calls[0][0][-2:] == ["on", "Disabled in Manager Configuration / Installation & Updates"]
    assert calls[0][1]["timeout"] <= 15
    assert calls[0][1]["max_stdout_bytes"] == 65536


def test_materialized_payload_keeps_both_canonical_controllers(
    switch_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = Path(__file__).resolve().parents[1]
    payload = tmp_path / "payload"
    self_install._copy_payload(source, payload)
    monkeypatch.setattr(self_install, "running_payload_dir", lambda: payload)
    canonical = source.parent / "libs/bootstrap-killswitch"
    for name in ("bootstrap-killswitch-guard.sh", "bootstrap-killswitch-guard.ps1"):
        assert (payload / "scripts" / name).read_bytes() == (canonical / name).read_bytes()
    assert Path(policy._guard_command()[-1]).parent == payload / "scripts"
