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


def test_posix_setup_assets_include_literal_reader_and_survive_installation_removal(launch):
    record, runtime, _assets = launch
    scripts = runtime / "scripts"
    scripts.mkdir()
    canonical = Path(__file__).resolve().parents[1] / "scripts"
    names = (
        "default-setup.sh", "launch-command.sh", "native-seed-reply.sh", "native-seed-boundary.sh",
        "reconcile-machine-settings.sh",
    )
    for name in names:
        shutil.copy2(canonical / name, scripts / name)
    prepared = session_shell.prepare(
        record, scripts / "default-setup.sh", installation_roots=(runtime,),
    )
    expected = {name: (scripts / name).read_bytes() for name in names}
    assert {path.name: path.read_bytes() for path in prepared.directory.iterdir()} == expected
    shutil.rmtree(runtime)
    assert {path.name: path.read_bytes() for path in prepared.directory.iterdir()} == expected
    assert all(not path.is_symlink() for path in prepared.directory.iterdir())


@pytest.mark.parametrize("missing", ["native-seed-reply.sh", "native-seed-boundary.sh"])
def test_missing_posix_helper_does_not_publish_partial_setup_assets(launch, missing):
    record, runtime, _assets = launch
    scripts = runtime / "scripts"
    scripts.mkdir()
    for name in (
        "default-setup.sh", "launch-command.sh", "reconcile-machine-settings.sh",
        *({"native-seed-reply.sh", "native-seed-boundary.sh"} - {missing}),
    ):
        (scripts / name).write_text("# fixture\n", encoding="utf-8")
    with pytest.raises(FileNotFoundError):
        session_shell.prepare(record, scripts / "default-setup.sh", installation_roots=(runtime,))
    assert not (record.parent / "launch-attempts").exists()


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
        assert manifest.parent == wrapper.parent / "pane-args"
        assert not manifest.is_relative_to(runtime)
    assert controls[-3:] == ["copilot", "two words", ""]
    for flag, value in expected.items():
        assert controls[controls.index(flag) + 1] == value
    assert wrapper.is_relative_to(record.parent / "launch-attempts")
    expected_bytes = (assets / name).read_bytes()
    shutil.rmtree(runtime)
    assert wrapper.read_bytes() == expected_bytes
    if not is_tmux:
        assert json.loads(manifest.read_text("utf-8")) == handoff
        sessions.cleanup_mux_pane_args(command)
        assert not manifest.exists()


@pytest.mark.parametrize("age,expired", [(60, False), (86400, False), (86401, True)])
def test_launch_owned_manifest_expiry_preserves_startup_grace(launch, monkeypatch, age, expired):
    from agent_worktrees import sessions_pane_args as transport

    record, runtime, assets = launch
    monkeypatch.setattr(config, "tracking_dir", lambda: record.parent)
    monkeypatch.setattr(config, "install_dir", lambda: runtime)
    prepared = session_shell.prepare(record, assets / "pane-wrapper.ps1", installation_roots=(runtime,))
    command = transport.file_mux_pane_cmd(str(prepared.wrapper), ["program", ""])
    manifest = Path(command[-1][1:-1].replace("''", "'"))
    now = 200000.0
    monkeypatch.setattr(transport.time, "time", lambda: now)
    os.utime(manifest, (now - age, now - age))
    foreign = manifest.with_name("other.json")
    foreign.write_text("keep")
    os.utime(foreign, (0, 0))
    shutil.rmtree(runtime)
    transport.sweep_mux_pane_args()
    assert manifest.exists() == (not expired)
    assert foreign.read_text() == "keep"
    transport.cleanup_mux_pane_args(command)
    assert not manifest.exists()


def test_launch_owned_cleanup_does_not_cross_another_shell(launch, monkeypatch):
    from agent_worktrees import sessions_pane_args as transport

    record, runtime, assets = launch
    monkeypatch.setattr(config, "tracking_dir", lambda: record.parent)
    monkeypatch.setattr(config, "install_dir", lambda: runtime)
    first = session_shell.prepare(record, assets / "pane-wrapper.ps1", installation_roots=(runtime,))
    second = session_shell.prepare(record, assets / "pane-wrapper.ps1", installation_roots=(runtime,))
    monkeypatch.setattr(
        transport, "_launch_args_roots", lambda: pytest.fail("producer enumerated other executions"),
    )
    command = transport.file_mux_pane_cmd(str(first.wrapper), ["program"])
    manifest = Path(command[-1][1:-1].replace("''", "'"))
    foreign_command = [
        *command[:-3], "'" + str(second.directory / "pane-launch.ps1") + "'", *command[-2:],
    ]
    transport.cleanup_mux_pane_args(foreign_command)
    assert manifest.exists()
    transport.cleanup_mux_pane_args(command)
    assert not manifest.exists()


def test_legacy_producer_does_not_publish_in_unowned_wrapper_directory(launch, monkeypatch):
    from agent_worktrees import sessions_pane_args as transport

    record, runtime, assets = launch
    monkeypatch.setattr(config, "tracking_dir", lambda: record.parent)
    monkeypatch.setattr(config, "install_dir", lambda: runtime)
    command = transport.file_mux_pane_cmd(str(assets / "pane-wrapper.ps1"), ["program"])
    manifest = Path(command[-1][1:-1].replace("''", "'"))
    assert manifest.parent == runtime / "pane-args"
    assert not (assets / "pane-args").exists()
    transport.cleanup_mux_pane_args(command)


@pytest.mark.parametrize("ancestor", [0, 1, 2, 3])
def test_launch_owned_handoffs_refuse_indirect_ancestors(launch, monkeypatch, ancestor):
    from agent_worktrees import sessions_pane_args as transport

    record, runtime, assets = launch
    monkeypatch.setattr(config, "tracking_dir", lambda: record.parent)
    monkeypatch.setattr(config, "install_dir", lambda: runtime)
    prepared = session_shell.prepare(record, assets / "pane-wrapper.ps1", installation_roots=(runtime,))
    command = transport.file_mux_pane_cmd(str(prepared.wrapper), ["program"])
    manifest = Path(command[-1][1:-1].replace("''", "'"))
    os.utime(manifest, (0, 0))
    blocked = [manifest.parent, *manifest.parent.parents][ancestor]
    original = transport._is_link_or_junction
    monkeypatch.setattr(transport, "_is_link_or_junction", lambda path: path == blocked or original(path))
    with pytest.raises(RuntimeError, match="link or junction"):
        transport.file_mux_pane_cmd(str(prepared.wrapper), ["program"])
    transport.sweep_mux_pane_args()
    transport.cleanup_mux_pane_args(command)
    assert manifest.exists()


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
@pytest.mark.parametrize("wrapped", [False, True])
@pytest.mark.parametrize("recovery", [False, True])
def test_mux_setup_copy_preserves_separate_admission_and_literal_native_arguments(
    launch, monkeypatch, wrapped, recovery,
):
    record, runtime, assets = launch
    scripts = runtime / "scripts"
    scripts.mkdir()
    canonical = Path(__file__).resolve().parents[1] / "scripts"
    names = ("default-setup.ps1", "launch-command.ps1", "native-seed-boundary.ps1",
             "reconcile-machine-settings.ps1")
    for name in names:
        shutil.copy2(canonical / name, scripts / name)
    monkeypatch.setattr(launch_seed_exec.installer, "install_dir", lambda: runtime)
    monkeypatch.setattr(manager_launch_cli, "_worktree_manager_root", lambda: runtime.with_name("manager"))
    monkeypatch.setattr(manager_launch_cli, "_usable_worktree_manager_launcher_dir", lambda: assets)
    (assets / "pane-wrapper.ps1").write_text(session_shell.NATIVE_CONSOLE_MARKER + "\n", encoding="utf-8")
    native = json.dumps({"args": ["", "two words", "2026-01-01", "--resume=original", "--", "'literal'"]})
    command = [
        "pwsh.exe", "-NoProfile", "-NoLogo", "-File", str(scripts / "default-setup.ps1"),
        "-NativeArgumentsJson", native,
    ]
    if wrapped:
        command = [
            "pwsh.exe", "-NoProfile", "-NoLogo", "-File", str(scripts / "launch-command.ps1"),
            *(["--recovery"] if recovery else []), "--", *command,
        ]
    original = list(command)
    prepared = launch_seed_exec.copied_setup_command(command, record)
    index = (11 if recovery else 10) if wrapped else 4
    copied = Path(prepared[index])
    assert copied.is_relative_to(record.parent / "launch-attempts")
    assert prepared[index + 1:] == command[index + 1:]
    assert prepared[-1] == native
    assert command == original
    assert "-LaunchSeedPreparation" not in prepared
    assert "-LaunchSeedRecord" not in prepared
    assert "agent_worktrees.launch_seed_exec" not in prepared
    if wrapped:
        assert Path(prepared[4]) == copied.with_name("launch-command.ps1")
        assert prepared[5:index - 4] == command[5:index - 4]
    expected = {name: (scripts / name).read_bytes() for name in names}
    shutil.rmtree(runtime)
    assert {path.name: path.read_bytes() for path in copied.parent.iterdir()} == expected


def test_mux_setup_copy_leaves_custom_commands_unchanged(launch):
    record, _runtime, _assets = launch
    command = ["copilot", "--resume=original", "--", "literal"]
    assert launch_seed_exec.copied_setup_command(command, record) == command
    assert not (record.parent / "launch-attempts").exists()


@pytest.mark.skipif(os.name != "nt", reason="Native Windows setup plan")
@pytest.mark.parametrize("healthy", [False, True])
def test_copy_then_actual_pane_construction_probes_manager_only_once(launch, monkeypatch, healthy):
    import subprocess
    from types import SimpleNamespace

    record, runtime, _assets = launch
    scripts = runtime / "scripts"
    scripts.mkdir()
    canonical = Path(__file__).resolve().parents[1] / "scripts"
    for name in ("default-setup.ps1", "launch-command.ps1", "native-seed-boundary.ps1",
                 "reconcile-machine-settings.ps1"):
        shutil.copy2(canonical / name, scripts / name)
    (runtime / "current-version").write_text("A", encoding="utf-8")
    slot = runtime / "versions" / "A"
    bootstrap = slot / ".runtime-bootstrap" / "runtime_launcher.py"
    bootstrap.parent.mkdir()
    bootstrap.touch()
    python = slot / "Scripts" / "python.exe"
    python.parent.mkdir()
    python.touch()
    monkeypatch.setattr(manager_launch_cli, "_core", lambda: SimpleNamespace())
    monkeypatch.setattr(manager_launch_cli, "_worktree_manager_root", lambda: runtime)
    monkeypatch.setattr(launch_seed_exec.installer, "install_dir", lambda: runtime)
    monkeypatch.setattr(config, "install_dir", lambda: runtime)
    monkeypatch.setattr(config, "tracking_dir", lambda: record.parent)
    monkeypatch.setattr(config, "project_name", lambda: "example")
    monkeypatch.setattr(sessions, "_LEGACY_BIN_DIR", str(runtime / "missing-legacy-bin"))
    probes = []

    def probe(command):
        probes.append(command)
        return (
            ((0, 6, 44, 1), subprocess.CompletedProcess(command, 0, "worktree-manager 0.6.44-dev1"))
            if healthy else (None, None)
        )

    monkeypatch.setattr(manager_launch_cli, "_probe_worktree_manager_version", probe)
    command = [
        "pwsh.exe", "-NoProfile", "-NoLogo", "-File", str(scripts / "launch-command.ps1"), "--",
        "pwsh.exe", "-NoProfile", "-NoLogo", "-File", str(scripts / "default-setup.ps1"),
        "-NativeArgumentsJson", '{"args":[]}',
    ]
    copied = launch_seed_exec.copied_setup_command(command, record)
    assert probes == []
    assert Path(copied[4]).is_relative_to(record.parent / "launch-attempts")
    sessions._mux_pane_cmd("test-worktree", copied, is_tmux=False, mux_bin="psmux.exe")
    assert len(probes) == 1
    assert probes[0][0] == str(python)


@pytest.mark.skipif(os.name != "nt" or shutil.which("pwsh") is None, reason="Native PowerShell required")
def test_copied_mux_setup_runs_and_returns_after_disposable_source_removal(launch, monkeypatch):
    from agent_procutil.owned_process import run_caller_owned

    record, runtime, assets = launch
    scripts = runtime / "scripts"
    scripts.mkdir()
    canonical = Path(__file__).resolve().parents[1] / "scripts"
    for name in ("default-setup.ps1", "launch-command.ps1", "native-seed-boundary.ps1",
                 "reconcile-machine-settings.ps1"):
        shutil.copy2(canonical / name, scripts / name)
    monkeypatch.setattr(launch_seed_exec.installer, "install_dir", lambda: runtime)
    monkeypatch.setattr(manager_launch_cli, "_worktree_manager_root", lambda: runtime.with_name("manager"))
    monkeypatch.setattr(manager_launch_cli, "_usable_worktree_manager_launcher_dir", lambda: assets)
    (assets / "pane-wrapper.ps1").write_text(session_shell.NATIVE_CONSOLE_MARKER + "\n", encoding="utf-8")
    backend = runtime.with_name("backend.ps1")
    backend.write_text(
        "$ErrorActionPreference = 'Stop'\n"
        f"Remove-Item -LiteralPath '{str(runtime).replace(chr(39), chr(39) * 2)}' -Recurse -Force\n"
        "[Console]::WriteLine((ConvertTo-Json -InputObject @($args) -Compress))\n"
        "exit 7\n",
        encoding="utf-8",
    )
    literal = ["two words", "2026-01-01", "single ' quote", 'double " quote', "; $literal", "--", "--allow-all"]
    pwsh = shutil.which("pwsh")
    command = [
        pwsh, "-NoProfile", "-NoLogo", "-File", str(scripts / "launch-command.ps1"), "--",
        pwsh, "-NoProfile", "-NoLogo", "-File", str(scripts / "default-setup.ps1"),
        "-CopilotPath", str(backend), "-RuntimePython", str(runtime / "missing-python.exe"),
        "-NativeArgumentsJson", json.dumps({"args": literal}),
    ]
    copied = launch_seed_exec.copied_setup_command(command, record)
    environment = dict(os.environ)
    environment.update(
        PATH=str(Path(pwsh).parent),
        USERPROFILE=str(runtime.with_name("isolated-home")),
        AGENT_WORKTREES_LAUNCH_RUNTIME_ROOT=str(runtime),
        AGENT_WORKTREES_MACHINE_SETTINGS_RECONCILED="1",
    )
    result = run_caller_owned(
        copied, cwd=record.parent, env=environment, timeout=30,
        max_stdout_bytes=1024 * 1024, max_stderr_bytes=1024 * 1024,
    )
    assert result.returncode == 7, result.stderr
    assert json.loads(result.stdout.splitlines()[-1]) == literal
    assert not result.stderr
    assert not runtime.exists()
    assert Path(copied[4]).is_file()


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
