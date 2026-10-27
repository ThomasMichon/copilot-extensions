from pathlib import Path
import json
import os
import shutil
import stat

import pytest

from agent_worktrees import config, launch_seed_exec, manager_launch_cli, session_shell, sessions, tracking


@pytest.fixture
def launch(tmp_path):
    state = tmp_path / "state" / "worktrees"
    state.mkdir(parents=True)
    record = tracking.WorktreeRecord(
        worktree_id="test-worktree", repo="example",
        worktree_path=str(tmp_path / "worktree"), branch="worktree/test",
        machine="test-host", platform="windows",
        started_at="2026-01-01T00:00:00+00:00",
        last_resumed_at="2026-01-01T00:00:00+00:00",
        resume_count=0, title=None, status="active", completed_at=None,
    )
    path = state / "test-worktree.yaml"
    tracking.save_record(record, path)
    runtime = tmp_path / "installation"
    assets = runtime / "versions" / "A" / "payload" / "bin"
    assets.mkdir(parents=True)
    for name in ("pane-wrapper.ps1", "pane-launch.ps1", "pane-wrapper.sh"):
        (assets / name).write_bytes((
            "# worktree-manager:pane-boundary-v1\noriginal " + name + "\n"
        ).encode())
    return path, runtime, assets


@pytest.mark.parametrize("name", ["pane-wrapper.ps1", "pane-wrapper.sh"])
def test_assets_survive_removal_and_do_not_follow_source_updates(launch, name):
    record, runtime, assets = launch
    expected = (assets / name).read_bytes()
    prepared = session_shell.prepare(record, assets / name, installation_roots=(runtime,))
    assert prepared.directory.is_relative_to(record.parent / "launch-attempts")
    assert prepared.wrapper.read_bytes() == expected
    assert not prepared.directory.is_relative_to(runtime)
    if name.endswith(".ps1"):
        assert (prepared.directory / "pane-launch.ps1").read_bytes() == (assets / "pane-launch.ps1").read_bytes()
    else:
        assert list(prepared.directory.iterdir()) == [prepared.wrapper]
    (assets / name).write_text("replacement", encoding="utf-8")
    assert prepared.wrapper.read_bytes() == expected
    shutil.rmtree(runtime)
    assert prepared.wrapper.read_bytes() == expected
    assert not stat.S_ISLNK(prepared.wrapper.lstat().st_mode)


def test_each_execution_gets_its_own_assets_even_with_one_attempt(launch):
    record, runtime, assets = launch
    arguments = {"installation_roots": (runtime,), "attempt_id": "a" * 32}
    first = session_shell.prepare(record, assets / "pane-wrapper.sh", **arguments)
    second = session_shell.prepare(record, assets / "pane-wrapper.sh", **arguments)
    assert first.directory != second.directory
    assert first.directory.parent == second.directory.parent
    assert first.directory.parent.name == "a" * 32


def test_refuses_storage_in_any_removed_installation(launch):
    record, runtime, assets = launch
    with pytest.raises(ValueError, match="removable installation"):
        session_shell.prepare(
            record, assets / "pane-wrapper.sh",
            installation_roots=(runtime, record.parent.parent),
        )
    assert not (record.parent / "launch-attempts").exists()


def test_missing_windows_launcher_does_not_publish_partial_assets(launch):
    record, runtime, assets = launch
    (assets / "pane-launch.ps1").unlink()
    with pytest.raises(FileNotFoundError):
        session_shell.prepare(record, assets / "pane-wrapper.ps1", installation_roots=(runtime,))
    assert not (record.parent / "launch-attempts").exists()


@pytest.mark.parametrize("argument", ["record", "wrapper", "root"])
def test_requires_explicit_absolute_paths(launch, argument):
    record, runtime, assets = launch
    arguments = [record, assets / "pane-wrapper.sh", (runtime,)]
    arguments[{"record": 0, "wrapper": 1, "root": 2}[argument]] = (
        (Path("relative"),) if argument == "root" else Path("relative")
    )
    with pytest.raises(ValueError, match="absolute"):
        session_shell.prepare(arguments[0], arguments[1], installation_roots=arguments[2])


@pytest.mark.parametrize("is_tmux", [True, False])
def test_real_pane_builder_uses_copied_assets_and_the_selected_mux(launch, monkeypatch, is_tmux):
    record, runtime, assets = launch
    manager = runtime.with_name("manager")
    monkeypatch.setattr(config, "tracking_dir", lambda: record.parent)
    monkeypatch.setattr(config, "project_name", lambda: "example")
    monkeypatch.setattr(config, "install_dir", lambda: runtime)
    monkeypatch.setattr(manager_launch_cli, "_worktree_manager_root", lambda: manager)
    name = "pane-wrapper.sh" if is_tmux else "pane-wrapper.ps1"
    selected_mux = str(runtime.with_name("mux with spaces") / ("tmux" if is_tmux else "psmux.exe"))
    command = sessions._mux_pane_cmd(
        "test-worktree", ["copilot", "two words", ""],
        is_tmux=is_tmux, pane_wrapper=str(assets / name),
        mux_bin=selected_mux, session_name="selected-session",
    )
    if is_tmux:
        wrapper = Path(command[command.index("bash") + 1])
        controls = command[command.index("bash") + 2:]
        expected = {
            "--aw-manager-root": str(manager), "--aw-project": "example",
            "--aw-mux-session": "selected-session", "--aw-mux-bin": selected_mux,
        }
    else:
        manifest = Path(command[-1][1:-1].replace("''", "'"))
        handoff = json.loads(manifest.read_text(encoding="utf-8"))
        wrapper, controls = Path(handoff["wrapper"]), handoff["argv"]
        expected = {
            "-AwManagerRoot": str(manager), "-AwProject": "example",
            "-AwMuxSession": "selected-session", "-AwPsmuxBin": selected_mux,
        }
    assert controls[-3:] == ["copilot", "two words", ""]
    for flag, value in expected.items():
        assert controls[controls.index(flag) + 1] == value
    assert wrapper.is_relative_to(record.parent / "launch-attempts")
    expected_bytes = (assets / name).read_bytes()
    shutil.rmtree(runtime)
    assert wrapper.read_bytes() == expected_bytes


@pytest.mark.parametrize("is_tmux", [True, False])
def test_old_wrapper_never_receives_new_control_arguments(launch, monkeypatch, is_tmux):
    record, runtime, assets = launch
    monkeypatch.setattr(config, "tracking_dir", lambda: record.parent)
    monkeypatch.setattr(config, "install_dir", lambda: runtime)
    wrapper = assets / ("pane-wrapper.sh" if is_tmux else "pane-wrapper.ps1")
    wrapper.write_text("# legacy wrapper\n", encoding="utf-8")
    command = sessions._mux_pane_cmd(
        "test-worktree", ["copilot"], is_tmux=is_tmux, pane_wrapper=str(wrapper),
    )
    if is_tmux:
        assert Path(command[command.index("bash") + 1]) == wrapper
        arguments = command
    else:
        manifest = Path(command[-1][1:-1].replace("''", "'"))
        handoff = json.loads(manifest.read_text(encoding="utf-8"))
        assert Path(handoff["wrapper"]) == wrapper
        arguments = handoff["argv"]
    assert "-AwManagerRoot" not in arguments and "--aw-manager-root" not in arguments
    assert not (record.parent / "launch-attempts").exists()


def test_pane_builder_reports_preparation_failure_without_a_spawn_plan(launch, monkeypatch):
    record, _runtime, assets = launch
    monkeypatch.setattr(config, "tracking_dir", lambda: record.parent)
    monkeypatch.setattr(config, "install_dir", lambda: record.parent)
    with pytest.raises(RuntimeError, match="launch-owned pane assets"):
        sessions._mux_pane_cmd(
            "test-worktree", ["copilot"], is_tmux=True,
            pane_wrapper=str(assets / "pane-wrapper.sh"), mux_bin="tmux",
        )
    assert not (record.parent / "launch-attempts").exists()


@pytest.mark.parametrize("prompt,receipt", [
    (None, None), (None, "receipt"), ("", "receipt"), ("quoted 'two words'\n--", "receipt"),
])
def test_prompt_controls_preserve_literal_transport(prompt, receipt):
    import base64

    from agent_worktrees.sessions_pane_args import prompt_controls

    controls = prompt_controls(prompt, receipt)
    expected = []
    if prompt is not None:
        expected += ["--aw-prompt-b64", base64.b64encode(prompt.encode()).decode("ascii")]
    if receipt:
        expected += ["--aw-prompt-receipt-b64", base64.b64encode(receipt.encode()).decode("ascii")]
    assert controls == expected


@pytest.mark.parametrize("receipt", [None, ""])
def test_prompt_controls_require_receipt_before_launch(receipt):
    from agent_worktrees.sessions_pane_args import prompt_controls

    with pytest.raises(RuntimeError, match="requires a receipt"):
        prompt_controls("prompt", receipt)


@pytest.mark.skipif(os.name != "nt", reason="Native Windows setup plan")
@pytest.mark.parametrize("admission", [False, True])
@pytest.mark.parametrize("wrapped", [False, True])
@pytest.mark.parametrize("peer_ready", [False, True])
def test_normalized_setup_is_its_native_lifetime_owner_with_launch_owned_assets(
    launch, monkeypatch, admission, wrapped, peer_ready,
):
    record, runtime, assets = launch
    scripts = runtime / "scripts"
    scripts.mkdir()
    canonical = Path(__file__).resolve().parents[1] / "scripts"
    for name in ("default-setup.ps1", "launch-command.ps1", "native-seed-boundary.ps1", "reconcile-machine-settings.ps1"):
        shutil.copy2(canonical / name, scripts / name)
    monkeypatch.setattr(launch_seed_exec.installer, "install_dir", lambda: runtime)
    monkeypatch.setattr(manager_launch_cli, "_worktree_manager_root", lambda: runtime.with_name("manager"))
    (assets / "pane-wrapper.ps1").write_text(
        (session_shell.NATIVE_CONSOLE_MARKER if peer_ready else "# old peer") + "\n", encoding="utf-8",
    )
    monkeypatch.setattr(manager_launch_cli, "_usable_worktree_manager_launcher_dir", lambda: assets)
    command = [
        "pwsh.exe", "-NoProfile", "-NoLogo", "-File", str(scripts / "default-setup.ps1"),
        "-NativeArgumentsJson", json.dumps({"args": ["", "--model", "fixture", "--", "literal"]}),
    ]
    if wrapped:
        command = [
            "pwsh.exe", "-NoProfile", "-NoLogo", "-File", str(scripts / "launch-command.ps1"), "--",
            *command,
        ]
    prepared = launch_seed_exec.deferred_command(
        command, record, seed_id=None if admission else "seed-a", admission_only=admission,
    )
    if not peer_ready:
        assert prepared[1:3] == ["-m", "agent_worktrees.launch_seed_exec"]
        assert prepared[-len(command):] == command
        assert not (record.parent / "launch-attempts").exists()
        return
    assert "agent_worktrees.launch_seed_exec" not in prepared
    index = launch_seed_exec.native_setup_index(prepared)
    assert index == (10 if wrapped else 4)
    copied = Path(prepared[index])
    assert copied.is_relative_to(record.parent / "launch-attempts")
    assert prepared[prepared.index("-LaunchSeedPreparation") + 1] == ("admission" if admission else "seed")
    assert prepared[prepared.index("-LaunchSeedNativeRoot") + 1] == str(runtime)
    assert prepared[-2:] == command[-2:]
    if wrapped:
        assert Path(prepared[4]) == copied.with_name("launch-command.ps1")
        assert prepared[5] == "--"
    expected = {path.name: path.read_bytes() for path in copied.parent.iterdir()}
    shutil.rmtree(runtime)
    assert {path.name: path.read_bytes() for path in copied.parent.iterdir()} == expected


def test_native_pane_copies_engine_owned_console_helper_with_canonical_wrapper(launch, monkeypatch):
    record, runtime, assets = launch
    scripts = runtime / "scripts"
    scripts.mkdir()
    helper = scripts / "native-seed-boundary.ps1"
    helper.write_text("# current engine fixture\n", encoding="utf-8")
    wrapper = assets / "pane-wrapper.ps1"
    wrapper.write_text(
        "# worktree-manager:pane-boundary-v1\n" + session_shell.NATIVE_CONSOLE_MARKER + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(launch_seed_exec.installer, "install_dir", lambda: runtime)
    prepared = session_shell.prepare(record, wrapper, installation_roots=(runtime,))
    copied = prepared.directory / helper.name
    assert copied.read_bytes() == helper.read_bytes()
    shutil.rmtree(runtime)
    assert copied.read_text(encoding="utf-8") == "# current engine fixture\n"


@pytest.mark.skipif(os.name != "nt", reason="Native Windows setup plan")
def test_old_normalized_setup_keeps_legacy_provider_instead_of_silent_native_activation(launch, monkeypatch):
    record, runtime, _assets = launch
    scripts = runtime / "scripts"
    scripts.mkdir()
    setup = scripts / "default-setup.ps1"
    setup.write_text(launch_seed_exec.BOUNDARY_MARKER + "\n", encoding="utf-8")
    monkeypatch.setattr(launch_seed_exec.installer, "install_dir", lambda: runtime)
    command = ["pwsh.exe", "-NoProfile", "-File", str(setup)]
    prepared = launch_seed_exec.deferred_command(command, record, seed_id="seed-a")
    assert prepared[1:3] == ["-m", "agent_worktrees.launch_seed_exec"]
    assert prepared[-len(command):] == command
    assert not (record.parent / "launch-attempts").exists()


def test_preview_never_materializes_native_setup_assets(launch, monkeypatch):
    record, _runtime, _assets = launch
    monkeypatch.setattr(
        launch_seed_exec, "_native_setup_command",
        lambda *args, **kwargs: pytest.fail("A preview must not prepare assets"),
    )
    prepared = launch_seed_exec.deferred_command(
        ["copilot"], record, admission_only=True, prepare_native=False,
    )
    assert prepared[1:3] == ["-m", "agent_worktrees.launch_seed_exec"]
    assert not (record.parent / "launch-attempts").exists()


@pytest.mark.skipif(os.name != "nt", reason="Native Windows setup plan")
def test_windows_powershell_keeps_legacy_host_semantics(launch, monkeypatch):
    record, runtime, _assets = launch
    scripts = runtime / "scripts"
    scripts.mkdir()
    setup = scripts / "default-setup.ps1"
    setup.write_text(launch_seed_exec.NATIVE_SETUP_MARKER + "\n", encoding="utf-8")
    monkeypatch.setattr(launch_seed_exec.installer, "install_dir", lambda: runtime)
    command = ["powershell.exe", "-NoProfile", "-File", str(setup)]
    prepared = launch_seed_exec.deferred_command(command, record, admission_only=True)
    assert prepared[1:3] == ["-m", "agent_worktrees.launch_seed_exec"]
    assert prepared[-len(command):] == command
    assert not (record.parent / "launch-attempts").exists()
