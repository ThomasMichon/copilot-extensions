"""Local automation controls never veto an explicit update or enroll a worker."""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from worktree_manager import installation_updates as ui
from worktree_manager import maintenance_policy as policy
from worktree_manager import manager_config, source_config, update_driver_cli


@pytest.fixture
def policy_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("WORKTREE_MANAGER_ROOT", str(tmp_path / "manager"))
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    monkeypatch.delenv("BOOTSTRAP_KILLSWITCH_STATE_FILE", raising=False)
    return tmp_path / "manager"


def test_default_control_is_read_only_and_does_not_enroll(policy_home: Path) -> None:
    state = policy.inspect_policy()
    assert state.enabled is True and not state.error and len(state.revision) == 64
    row = policy.configuration_row()
    assert row["enabled"] == "ON" and row["maintenance_control"] is True
    assert not row["updatable"] and row["running"] == "not a worker"
    assert not policy_home.exists()


@pytest.mark.parametrize("root_form", ["absolute", "relative", "home", "missing-parent"])
def test_configuration_reads_and_edits_the_enforced_root_not_a_shadow(
    policy_home: Path, monkeypatch: pytest.MonkeyPatch, root_form: str, capsys,
) -> None:
    from worktree_manager import runtime_source_config

    home = policy_home.parent
    caller = home / "caller"
    caller.mkdir()
    policy_home.mkdir()
    config = policy_home / "config.toml"
    config.write_text(
        "# selected policy\n[maintenance]\nautomatic = false\n[source]\nref = 'kept'\n",
        encoding="utf-8",
    )
    shadow = caller / "~" / "manager" / "config.toml"
    shadow.parent.mkdir(parents=True)
    shadow.write_text("[maintenance]\nautomatic = true\n", encoding="utf-8")
    shadow_before = shadow.read_bytes()
    roots = {
        "absolute": str(policy_home), "relative": "../manager", "home": "~/manager",
        "missing-parent": str(home / "missing/../manager"),
    }
    monkeypatch.chdir(caller)
    monkeypatch.setenv("WORKTREE_MANAGER_ROOT", roots[root_form])
    monkeypatch.setattr(ui.engine_client, "accept_inherited_engine_command", lambda: None)
    assert source_config.config_path() == config.resolve()
    assert source_config.config_path(Path(roots[root_form])) == config.resolve()
    before = policy.inspect_policy()
    row = policy.configuration_row()
    assert before.enabled is False and row["enabled"] == "OFF"
    assert row["activity"] == "blocked" and row["source"] == str(config.resolve())
    assert "maintenance.automatic is false" in policy.update_refusal(["--auto"])
    assert ui.main(["maintenance", "on", "--expected-policy", before.revision]) == 0
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["path"] == str(config.resolve()) and receipt["automatic"] is True
    assert policy.inspect_policy().enabled is True
    assert policy.configuration_row()["enabled"] == "ON"
    assert policy.update_refusal(["--auto"]) is None
    source_config.set_source(ref="updated", root=Path(roots[root_form]))
    runtime_source_config.set_source(
        runtime_source_config.RuntimeSource("main"), root=Path(roots[root_form]),
    )
    assert policy.inspect_policy().enabled is True
    assert source_config.configured_source() == (None, "updated")
    assert runtime_source_config.configured_source().channel == "main"
    assert "# selected policy" in config.read_text(encoding="utf-8")
    assert shadow.read_bytes() == shadow_before
    assert not list(shadow.parent.glob(".*.lock"))


@pytest.mark.parametrize("maintenance", [
    "[maintenance]\nautomatic = false\n",
    "maintenance = { automatic = false }\n",
    "maintenance.automatic = false\n",
    '["maintenance"]\n"automatic" = false\n',
])
def test_control_preserves_real_toml_and_unrelated_config(
    policy_home: Path, maintenance: str,
) -> None:
    policy_home.mkdir()
    path = policy_home / "config.toml"
    original = maintenance + (
        "\n# Keep this explanation.\n[source]\nref = 'dev'\n"
        "[unrelated]\nwhen = 2026-10-24\nnames = ['one', 'two']\nnumber = nan\n"
    )
    path.write_text(original, encoding="utf-8")
    before = policy.inspect_policy()
    saved = policy.set_enabled(True, expected=before.revision)
    assert saved.enabled is True and saved.revision != before.revision
    after = path.read_text(encoding="utf-8")
    data = source_config.tomllib.loads(after)
    assert "# Keep this explanation." in after
    assert data["source"] == {"ref": "dev"}
    assert data["unrelated"]["names"] == ["one", "two"]
    assert str(data["unrelated"]["when"]) == "2026-10-24"
    assert math.isnan(data["unrelated"]["number"])
    assert data["maintenance"] == {"automatic": True}
    assert sorted(item.name for item in policy_home.iterdir()) == [".config.toml.lock", "config.toml"]


def test_stale_control_refuses_without_overwriting_other_edits(policy_home: Path) -> None:
    old = policy.inspect_policy()
    policy_home.mkdir()
    path = policy_home / "config.toml"
    path.write_text("[source]\nref = 'new-owner'\n", encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(manager_config.ManagerConfigError, match="changed"):
        policy.set_enabled(False, expected=old.revision)
    assert path.read_bytes() == before
    assert sorted(item.name for item in policy_home.iterdir()) == [".config.toml.lock", "config.toml"]


@pytest.mark.parametrize("content", [
    "[maintenance]\nautomatic = 'false'\n",
    "[maintenance]\nautomatic = false\nunknown = true\n",
    "maintenance = false\n",
    "[",
])
def test_invalid_policy_is_visible_and_not_editable(
    policy_home: Path, content: str,
) -> None:
    policy_home.mkdir()
    path = policy_home / "config.toml"
    path.write_text(content, encoding="utf-8")
    state = policy.inspect_policy()
    assert state.enabled is None and state.error
    row = policy.configuration_row()
    assert row["enabled"] == "unknown" and not row["maintenance_control"]
    assert state.error in row["card"]["body"]
    with pytest.raises(manager_config.ManagerConfigError):
        policy.set_enabled(True, expected="0" * 64)
    assert path.read_text(encoding="utf-8") == content


def test_failed_atomic_publish_is_not_success(
    policy_home: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = policy.inspect_policy()

    def refused(*args: object) -> None:
        raise PermissionError("synthetic configuration write denied")

    monkeypatch.setattr(policy.os, "replace", refused)
    with pytest.raises(PermissionError, match="synthetic configuration write denied"):
        policy.set_enabled(False, expected=state.revision)
    assert not (policy_home / "config.toml").exists()
    assert [item.name for item in policy_home.iterdir()] == [".config.toml.lock"]


def test_enable_does_not_clear_an_independent_automatic_hold(policy_home: Path) -> None:
    held = policy_home.parent / ".copilot-extensions" / "killSwitches" / "bootstrap-killswitch.json"
    held.parent.mkdir(parents=True)
    held.write_text('{"active":true,"reason":"independent owner"}', encoding="utf-8")
    baseline = held.read_bytes()
    saved = policy.set_enabled(True, expected=policy.inspect_policy().revision)
    assert saved.enabled is True
    row = policy.configuration_row()
    assert row["enabled"] == "ON" and row["activity"] == "blocked", (
        row["card"], Path.home(), policy.update_refusal(["--auto"]),
    )
    assert "KillSwitch active" in row["card"]["body"]
    assert held.read_bytes() == baseline


@pytest.mark.parametrize("writer", ["source", "runtime", "maintenance-after-source", "maintenance-after-runtime"])
def test_all_config_writers_serialize_read_modify_publish(
    policy_home: Path, writer: str,
) -> None:
    import os
    import time
    from concurrent.futures import ThreadPoolExecutor
    from agent_procutil.owned_process import run_caller_owned
    from worktree_manager import runtime_source_config
    from worktree_manager.config_write_lock import config_write_lock

    policy_home.mkdir()
    path = policy_home / "config.toml"
    path.write_text(
        "# retained comment\n[maintenance]\nautomatic = true\n"
        "[unrelated]\nwhen = 2026-10-24\nnumber = nan\n", encoding="utf-8",
    )
    expected = policy.inspect_policy().revision
    blocked = policy_home / "contended"
    code = """
import sys
from pathlib import Path
from worktree_manager import config_write_lock as lock, source_config, runtime_source_config, maintenance_policy
from worktree_manager.manager_config import ManagerConfigError
root, blocked, writer, expected = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3], sys.argv[4]
attempt = lock._try_lock_file_once
def observed(stream):
    acquired = attempt(stream)
    if not acquired:
        blocked.write_text("native lock contended")
    return acquired
lock._try_lock_file_once = observed
if writer == "source":
    source_config.set_source(ref="other-owner", root=root)
elif writer == "runtime":
    runtime_source_config.set_source(runtime_source_config.RuntimeSource("main"), root=root)
else:
    try:
        maintenance_policy.set_enabled(False, expected=expected, root=root)
    except ManagerConfigError as error:
        assert "changed" in str(error), str(error)
    else:
        raise AssertionError("stale maintenance confirmation overwrote a newer source")
"""
    environment = {**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)}
    with ThreadPoolExecutor(max_workers=1) as pool:
        with config_write_lock(path):
            future = pool.submit(
                run_caller_owned,
                [sys.executable, "-c", code, str(policy_home), str(blocked), writer, expected],
                timeout=15, env=environment, max_stdout_bytes=65536, max_stderr_bytes=65536,
            )
            deadline = time.monotonic() + 5
            while not blocked.exists():
                assert not future.done(), future.result() if future.done() else None
                assert time.monotonic() < deadline, "writer never attempted the native config lock"
                time.sleep(0.01)
            if writer.startswith("maintenance"):
                if writer.endswith("source"):
                    source_config._write(None, "new-owner", policy_home)
                else:
                    runtime_source_config._set_source_locked(
                        runtime_source_config.RuntimeSource("main"), root=policy_home,
                    )
            else:
                policy._set_enabled_locked(False, expected=expected, root=policy_home)
        result = future.result(timeout=15)
    assert result.returncode == 0, (result.stdout, result.stderr)
    content = path.read_text(encoding="utf-8")
    data = source_config.tomllib.loads(content)
    assert "# retained comment" in content
    assert str(data["unrelated"]["when"]) == "2026-10-24"
    assert math.isnan(data["unrelated"]["number"])
    assert data["maintenance"]["automatic"] is writer.startswith("maintenance")
    if writer == "source":
        assert data["source"]["ref"] == "other-owner"
    elif writer == "maintenance-after-source":
        assert data["source"]["ref"] == "new-owner"
    else:
        assert data["runtime_source"]["channel"] == "main"


def test_config_writer_lock_is_bounded_and_reusable(policy_home: Path) -> None:
    from worktree_manager.config_write_lock import config_write_lock

    path = source_config.config_path()
    with config_write_lock(path):
        with pytest.raises(TimeoutError, match="writer lock unavailable"):
            with config_write_lock(path, timeout=0.05):
                pytest.fail("contended lock admitted a second writer")
    with config_write_lock(path, timeout=0.05):
        pass


def test_manual_admission_does_not_even_read_automation_policy(
    policy_home: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import update_driver.core

    monkeypatch.setattr(
        update_driver.core, "automatic_update_refusal",
        lambda *args: pytest.fail("explicit update consulted automation policy"),
    )
    assert policy.admit_update([])
    assert policy.admit_update(["--force"])
    assert policy.admit_update(["--target", "worktree-manager"])


def test_disabled_auto_retains_owner_receipt_without_starting_an_updater(
    policy_home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    import update_driver

    policy_home.mkdir()
    (policy_home / "config.toml").write_text("[maintenance]\nautomatic = false\n", encoding="utf-8")
    monkeypatch.setattr(update_driver, "borrowed_custody", lambda: None)
    monkeypatch.setattr(
        update_driver, "runtime_command",
        lambda *args: ([sys.executable, "-c", "raise RuntimeError('disabled updater ran')"], {}),
    )
    monkeypatch.setattr(update_driver_cli, "_source_key", lambda: "fixture-source")
    assert update_driver_cli.cmd_update_request(["--auto", "--supervised-debug"]) == 3
    request, = update_driver.status()
    assert request.state == "completed" and request.exit_code == 3 and request.started_at is None
    result = capsys.readouterr()
    assert "maintenance.automatic is false" in result.err


def test_plain_public_update_still_submits_when_automation_is_off(
    policy_home: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import update_driver

    policy_home.mkdir()
    (policy_home / "config.toml").write_text("[maintenance]\nautomatic = false\n", encoding="utf-8")
    observed = []
    request = SimpleNamespace(id="fixture-request", log_path=policy_home / "request.log")
    monkeypatch.setattr(update_driver, "borrowed_custody", lambda: None)
    monkeypatch.setattr(update_driver, "runtime_command", lambda *args: (["fixture"], {}))
    monkeypatch.setattr(update_driver, "enqueue", lambda spec, **kwargs: observed.append(spec) or request)
    monkeypatch.setattr(update_driver, "ensure_worker", lambda *args, **kwargs: None)
    monkeypatch.setattr(update_driver_cli, "_report", lambda *args, **kwargs: None)
    monkeypatch.setattr(update_driver_cli, "_source_key", lambda: "fixture-source")
    assert update_driver_cli.cmd_update_request(["--detach", "--json"]) == 0
    assert len(observed) == 1 and "--auto" not in observed[0].argv and "--force" not in observed[0].argv
    assert dict(observed[0].context)["WORKTREE_MANAGER_ROOT"] == str(policy_home)


def test_control_actions_have_exact_scope_revision_and_confirmation(policy_home: Path) -> None:
    from worktree_manager.production_picker.picker_tui import pivots

    registration = ui.configuration_pivot()
    row = policy.configuration_row()
    actions = [item for item in registration.actions if pivots.entry_matches(item.when, row)]
    off = next(item for item in actions if item.key == "maintenance-off")
    assert off.confirm
    assert not any(item.key in {"update", "maintenance-on"} for item in actions)
    command = pivots.format_template(off.run, row)
    assert command[-4:] == ["maintenance", "off", "--expected-policy", row["policy_revision"]]
    assert not pivots.entry_matches(off.when, {**row, "id": "unrelated-plugin"})
    assert not policy_home.exists()


def test_control_command_persists_without_starting_an_update(
    policy_home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(ui.engine_client, "accept_inherited_engine_command", lambda: "")
    monkeypatch.setattr(ui, "submit", lambda *args: pytest.fail("setting started an update"))
    revision = policy.inspect_policy().revision
    assert ui.main(["maintenance", "off", "--expected-policy", revision]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["ok"] and result["automatic"] is False
    assert "No update or recurring worker" in result["message"]
    assert manager_config.load_config().maintenance.automatic is False
