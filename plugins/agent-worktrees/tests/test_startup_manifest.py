import json
import shutil
from pathlib import Path

import pytest

from agent_worktrees import config, launch_seed_exec, session_shell, sessions, sessions_pane_args as transport
from test_session_shell import launch as launch


@pytest.fixture
def startup(launch, monkeypatch):
    record, runtime, _assets = launch
    scripts = runtime / "scripts"
    scripts.mkdir()
    canonical = Path(__file__).resolve().parents[1] / "scripts"
    for name in (
        "default-setup.ps1", "launch-command.ps1", "native-seed-boundary.ps1",
        "reconcile-machine-settings.ps1",
    ):
        shutil.copy2(canonical / name, scripts / name)
    monkeypatch.setattr(config, "tracking_dir", lambda: record.parent)
    monkeypatch.setattr(config, "install_dir", lambda: runtime)
    prepared = session_shell.prepare(
        record, scripts / "default-setup.ps1", installation_roots=(runtime,),
    )
    return record, runtime, prepared


def setup_command(prepared, *, wrapped=True):
    command = [
        "pwsh.exe", "-NoProfile", "-NoLogo", "-File", str(prepared.wrapper),
        "-RuntimePython", "installation-python.exe", "-NativeArgumentsJson", '{"args":[""]}',
    ]
    return [
        *command[:4], str(prepared.directory / "launch-command.ps1"), "--recovery", "--", *command,
    ] if wrapped else command


@pytest.mark.parametrize("wrapped", [False, True])
def test_final_serialization_retains_typed_setup_until_pane_build(startup, launch, wrapped):
    _record, runtime, prepared = startup
    _record, _runtime, pane_assets = launch
    command = setup_command(prepared, wrapped=wrapped)
    command[-2:-2] = [
        "-LaunchSeedPreparation", "seed", "-LaunchSeedNativeRoot", str(runtime),
        "-LaunchSeedId", "seed-a", "-LaunchAttemptId", "a" * 32,
        "-LaunchResolutionId", "b" * 32, "-LaunchParentId", "c" * 32,
    ]
    command[-1] = json.dumps({"args": ["", "two words", "2026-01-01", "'quote'", "--", "; $literal"]})
    original = list(command)
    assert Path(command[launch_seed_exec.native_setup_index(command)]) == prepared.wrapper
    pane = transport.file_mux_pane_cmd(str(pane_assets / "pane-wrapper.ps1"), ["-AwWt", "wt"], cmd=command)
    outer = Path(pane[-1][1:-1].replace("''", "'"))
    compact = json.loads(outer.read_text())["argv"][2:]
    assert compact[:4] == command[:4]
    assert compact[4:] == [
        str(prepared.directory / "launch-command.ps1"), "--startup-manifest", compact[-1],
    ]
    manifest = Path(compact[-1])
    assert manifest.parent == prepared.directory / "pane-args"
    assert not manifest.is_relative_to(runtime)
    document = json.loads(manifest.read_text())
    assert document["wrapper"] == compact[4]
    assert document["argv"] == (command[5:] if wrapped else ["--recovery", "--", *command])
    assert command == original
    transport.cleanup_mux_pane_args(pane)
    assert not manifest.exists()
    assert not outer.exists()


@pytest.mark.parametrize("change", ["installed", "old", "custom", "host", "options", "empty"])
def test_unqualified_startup_keeps_original_command(startup, change):
    _record, runtime, prepared = startup
    command = setup_command(prepared)
    if change == "installed":
        command[4] = str(runtime / "scripts" / "launch-command.ps1")
    elif change == "old":
        wrapper = prepared.directory / "launch-command.ps1"
        wrapper.write_text(wrapper.read_text().replace(session_shell.STARTUP_MANIFEST_MARKER, "# old"))
    elif change == "custom":
        command[4] = str(prepared.directory / "custom-setup.ps1")
    elif change == "host":
        command[0] = "powershell.exe"
    elif change == "options":
        command.insert(1, "-Sta")
    else:
        command = command[:5]
    assert session_shell.startup_command(command) == command
    assert not (prepared.directory / "pane-args").exists()


def test_failed_outer_publication_removes_its_startup_handoff(startup, launch, monkeypatch):
    _record, _runtime, prepared = startup
    _record, _runtime, pane_assets = launch
    write = transport._write_manifest
    manifests = []

    def fail_outer(wrapper, args):
        if manifests:
            raise OSError("outer publication failed")
        manifests.append(write(wrapper, args))
        return manifests[0]

    monkeypatch.setattr(transport, "_write_manifest", fail_outer)
    with pytest.raises(OSError, match="outer publication"):
        transport.file_mux_pane_cmd(
            str(pane_assets / "pane-wrapper.ps1"), ["-AwWt", "wt"], cmd=setup_command(prepared),
        )
    assert len(manifests) == 1
    assert not manifests[0].exists()


def test_indirect_startup_asset_refuses_before_serialization(startup, monkeypatch):
    _record, _runtime, prepared = startup
    plain = session_shell._plain

    def reject(path, **kwargs):
        if path.name == "launch-command.ps1":
            raise ValueError("linked startup wrapper")
        return plain(path, **kwargs)

    monkeypatch.setattr(session_shell, "_plain", reject)
    with pytest.raises(ValueError, match="linked startup wrapper"):
        session_shell.startup_command(setup_command(prepared))
    assert not (prepared.directory / "pane-args").exists()


def test_cleanup_refuses_another_shells_manifest(startup):
    record, runtime, prepared = startup
    compact = session_shell.startup_command(setup_command(prepared))
    other = session_shell.prepare(
        record, runtime / "scripts" / "default-setup.ps1", installation_roots=(runtime,),
    )
    transport._cleanup_startup_command([
        *compact[:-3], str(other.directory / "launch-command.ps1"), *compact[-2:],
    ])
    assert Path(compact[-1]).exists()
    transport._cleanup_startup_command(compact)
    assert not Path(compact[-1]).exists()


def test_uncertain_creation_retains_outer_and_startup_handoffs(startup, launch, monkeypatch):
    _record, _runtime, prepared = startup
    _record, _runtime, pane_assets = launch
    argv = transport.file_mux_pane_cmd(
        str(pane_assets / "pane-wrapper.ps1"), ["-AwWt", "wt"], cmd=setup_command(prepared),
    )
    outer = Path(argv[-1][1:-1].replace("''", "'"))
    inner = Path(json.loads(outer.read_text())["argv"][-1])

    def uncertain(arguments, **kwargs):
        arguments.extend(argv)
        raise sessions.mux_creation.CreationUncertain("dispatched; outcome unknown")

    monkeypatch.setattr(sessions.mux_creation, "run_or_join", uncertain)
    monkeypatch.setattr(sessions, "_mux_bin", lambda _mux: "psmux.exe")
    try:
        result = sessions.mux_new_session("wt", str(prepared.directory), ["program"], mux="psmux")
        assert not result["ok"]
        assert "outcome unknown" in result["error"]
        assert outer.exists() and inner.exists()
    finally:
        transport.cleanup_mux_pane_args(argv)
    assert not outer.exists() and not inner.exists()


def test_startup_handoff_uses_existing_registered_expiry(startup, monkeypatch):
    import os

    _record, _runtime, prepared = startup
    compact = session_shell.startup_command(setup_command(prepared))
    manifest = Path(compact[-1])
    monkeypatch.setattr(transport.time, "time", lambda: 200000.0)
    os.utime(manifest, (200000 - transport.PANE_ARGS_MAX_AGE, 200000 - transport.PANE_ARGS_MAX_AGE))
    transport.sweep_mux_pane_args()
    assert manifest.exists()
    os.utime(manifest, (0, 0))
    transport.sweep_mux_pane_args()
    assert not manifest.exists()
