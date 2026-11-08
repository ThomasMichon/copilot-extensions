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


def test_packaged_runtime_controller_works_without_a_legacy_payload(
    switch_home: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import shutil
    import worktree_manager

    package = switch_home / "package"
    scripts = package / "_runtime_assets" / "scripts"
    scripts.mkdir(parents=True)
    canonical = Path(__file__).resolve().parents[2] / "libs" / "bootstrap-killswitch"
    for name in ("bootstrap-killswitch-guard.sh", "bootstrap-killswitch-guard.ps1", "killswitch_mutation.py"):
        shutil.copy2(canonical / name, scripts / name)
    monkeypatch.setattr(worktree_manager, "__file__", str(package / "__init__.py"))
    monkeypatch.setattr(self_install, "running_payload_dir", lambda: switch_home / "absent-payload")
    before = {path: path.read_bytes() for path in scripts.iterdir()}
    row = policy.configuration_row()
    assert row["bootstrap_control"], row["card"]["body"]
    blocked = policy.set_enabled(False, expected=row["policy_revision"])
    assert blocked.enabled is False
    cleared = policy.set_enabled(True, expected=blocked.revision)
    assert cleared.enabled is True and not any(path.exists() for path in cleared.paths)
    assert before == {path: path.read_bytes() for path in scripts.iterdir()}
    assert not (switch_home / "absent-payload").exists()


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


def test_missing_parent_segments_keep_manager_and_controller_confirmation_aligned(
    switch_home: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = switch_home / "hold.json"
    monkeypatch.setenv("BOOTSTRAP_KILLSWITCH_STATE_FILE", str(switch_home / "missing/../hold.json"))
    first = policy.inspect_policy()
    assert first.paths == (path,)
    disabled = policy.set_enabled(False, expected=first.revision)
    assert disabled.enabled is False
    assert policy.inspect_policy().revision == disabled.revision
    assert json.loads(path.read_bytes())["active"] is True
    enabled = policy.set_enabled(True, expected=disabled.revision)
    assert enabled.enabled is True and not path.exists()
    assert not (switch_home / "missing").exists()


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
    def controller(argv, **kwargs):
        calls.append((argv, kwargs))
        if argv[-1] == "capabilities":
            return SimpleNamespace(
                returncode=0, stderr="", stdout=json.dumps({
                    "schema": "copilot-extensions.bootstrap-killswitch-controller",
                    "version": 1, "capabilities": ["conditional-mutation-v1"],
                }),
            )
        return SimpleNamespace(returncode=code, stdout="controller fixture", stderr="")

    monkeypatch.setattr(
        agent_procutil.owned_process, "run_caller_owned",
        controller,
    )
    with pytest.raises(manager_config.ManagerConfigError):
        policy.set_enabled(False, expected=policy.inspect_policy().revision)
    assert len(calls) == 2
    assert policy.inspect_policy().enabled is True
    assert calls[1][0][-4:-2] == ["on", "Disabled in Manager Configuration / Installation & Updates"]
    assert calls[1][1]["timeout"] <= 15
    assert calls[1][1]["max_stdout_bytes"] == 65536


@pytest.mark.parametrize("enabled,index", [(True, 0), (True, 1), (False, 0)])
def test_new_audit_hold_between_ui_check_and_controller_is_never_overwritten(
    switch_home: Path, monkeypatch: pytest.MonkeyPatch, enabled: bool, index: int,
) -> None:
    import agent_procutil.owned_process

    original = policy.inspect_policy()
    if enabled:
        for path in original.paths:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('{"active":true,"reason":"confirmed old hold"}', encoding="utf-8")
    confirmed = policy.inspect_policy()
    selected = confirmed.paths[index]
    new_hold = b'{"active":true,"reason":"new unconfirmed audit hold"}'
    execute = agent_procutil.owned_process.run_caller_owned
    injected = []

    def race(argv, **kwargs):
        if argv[-1] != "capabilities" and kwargs["env"]["BOOTSTRAP_KILLSWITCH_STATE_FILE"] == str(selected):
            selected.parent.mkdir(parents=True, exist_ok=True)
            selected.write_bytes(new_hold)
            injected.append(selected)
        return execute(argv, **kwargs)

    monkeypatch.setattr(agent_procutil.owned_process, "run_caller_owned", race)
    with pytest.raises(manager_config.ManagerConfigError, match="changed"):
        policy.set_enabled(enabled, expected=confirmed.revision)
    assert injected == [selected]
    assert selected.read_bytes() == new_hold
    assert policy.inspect_policy().enabled is False


@pytest.mark.parametrize("reply", [
    "",
    "{",
    '{"schema":"copilot-extensions.bootstrap-killswitch-controller","version":true,"capabilities":["conditional-mutation-v1"]}',
    '{"schema":"copilot-extensions.bootstrap-killswitch-controller","version":1,"capabilities":[]}',
])
def test_downlevel_controller_never_receives_unconditional_mutation(
    switch_home: Path, monkeypatch: pytest.MonkeyPatch, reply: str,
) -> None:
    import agent_procutil.owned_process

    calls = []
    monkeypatch.setattr(
        agent_procutil.owned_process, "run_caller_owned",
        lambda argv, **kwargs: calls.append(argv) or SimpleNamespace(
            returncode=0 if reply else 2, stdout=reply, stderr="unknown or unsupported command",
        ),
    )
    with pytest.raises(manager_config.ManagerConfigError, match="does not declare conditional"):
        policy.set_enabled(False, expected=policy.inspect_policy().revision)
    assert len(calls) == 1 and calls[0][-1] == "capabilities"
    assert not list(switch_home.iterdir())


def test_materialized_payload_keeps_both_canonical_controllers(
    switch_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import shutil
    import tomllib
    import worktree_manager

    original = Path(__file__).resolve().parents[1]
    source = tmp_path / "source"
    source.mkdir()
    for relative in self_install._SLOT_KEY_FILES:
        target = source / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(original / relative, target)
    project = tomllib.loads((original / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    # Full dependency-graph delivery belongs to the wheel/sdist round-trip packet.
    (source / "pyproject.toml").write_text(
        f"[project]\nname = {project['name']!r}\nversion = {project['version']!r}\n",
        encoding="utf-8",
    )
    canonical = original.parent / "libs/bootstrap-killswitch"
    scripts = source / "scripts"
    scripts.mkdir(exist_ok=True)
    for name in ("bootstrap-killswitch-guard.sh", "bootstrap-killswitch-guard.ps1", "killswitch_mutation.py"):
        shutil.copy2(canonical / name, scripts / name)
    payload = tmp_path / "payload"
    self_install._copy_payload(source, payload)
    legacy_package = tmp_path / "legacy-package"
    legacy_package.mkdir()
    monkeypatch.setattr(worktree_manager, "__file__", str(legacy_package / "__init__.py"))
    monkeypatch.setattr(self_install, "running_payload_dir", lambda: payload)
    for name in ("bootstrap-killswitch-guard.sh", "bootstrap-killswitch-guard.ps1", "killswitch_mutation.py"):
        assert (payload / "scripts" / name).read_bytes() == (canonical / name).read_bytes()
    assert Path(policy._guard_command()[-1]).parent == payload / "scripts"
