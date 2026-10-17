"""Abandoned launch inputs are reaped without deleting active or foreign scopes."""

import os
import shlex
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from agent_containers.launch_input_cleanup import abandoned_cleanup_script


def _shell():
    if os.name == "nt":
        git = shutil.which("git")
        candidate = Path(git).parents[1] / "bin" / "bash.exe" if git else None
        if candidate and candidate.is_file():
            return str(candidate)
    elif shutil.which("sh"):
        return shutil.which("sh")
    pytest.skip("POSIX target shell unavailable")


def _posix(path):
    value = str(path).replace("\\", "/")
    return "/" + value[0].lower() + value[2:] if os.name == "nt" else value


@pytest.mark.guard
def test_reaps_uuid_orphans_preserves_fresh_active_and_foreign_inputs(tmp_path):
    old = time.time() - 7200
    directories = []
    for stem in ("a" * 32, "b" * 32, "c" * 32, "foreign"):
        owner = tmp_path / (stem + ".env")
        directory = tmp_path / (stem + ".env.d")
        directory.mkdir()
        owner.write_text("nonsecret general environment")
        for name in ("auth.env", "copilot", "result.json"):
            (directory / name).write_text("synthetic value")
        directories.append(directory)
        if stem != "b" * 32:
            for path in (owner, *directory.iterdir(), directory):
                os.utime(path, (old, old))
    # The active marker binds PID and process start time, not a reusable PID.
    active = _posix(directories[2] / "active.pid")
    prefix = (
        "start=$(sed 's/.*) //' /proc/$$/stat | awk '{print $20}'); "
        f'printf "%s %s\\n" "$$" "$start" > {shlex.quote(active)}; '
        f"touch -t 202001010000 {shlex.quote(_posix(directories[2]))}; "
    )
    result = subprocess.run(
        [_shell(), "-c", prefix + abandoned_cleanup_script(_posix(tmp_path))],
        capture_output=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert not directories[0].exists()
    assert not (tmp_path / ("a" * 32 + ".env")).exists()
    assert directories[1].exists() and directories[2].exists() and directories[3].exists()


@pytest.mark.guard
def test_file_absence_is_not_a_consumption_receipt(tmp_path, monkeypatch):
    from agent_containers import copilot_process_auth as auth

    directory = tmp_path / "auth.env.d"
    directory.mkdir()
    monkeypatch.setattr(auth.transport, "_run", lambda argv, **kwargs:
        subprocess.run([_shell(), "-c", argv[-1]], capture_output=True, text=True, timeout=5))
    with pytest.raises(RuntimeError, match="did not confirm consuming"):
        auth.wait_for_consumption("example-1", "vscode", _posix(tmp_path / "auth.env"), timeout=1)
    (directory / "consumed").write_text("1\n")
    auth.wait_for_consumption("example-1", "vscode", _posix(tmp_path / "auth.env"), timeout=1)


@pytest.mark.guard
def test_split_ram_auth_and_disk_wrapper_reaper_preserves_active_scope(tmp_path):
    ram = tmp_path / "ram"
    executables = tmp_path / "executables"
    ram.mkdir()
    executables.mkdir()
    old = time.time() - 7200
    for stem in ("a" * 32, "b" * 32, "c" * 32):
        auth_directory = ram / (stem + ".env.d")
        executable = executables / (stem + ".env.d")
        executable.mkdir()
        (executable / "copilot").write_text("nonsecret wrapper")
        if stem != "c" * 32:
            auth_directory.mkdir()
            (auth_directory / "auth.env").write_text("synthetic credential")
        for directory in (auth_directory, executable):
            if directory.exists():
                for path in (*directory.iterdir(), directory):
                    os.utime(path, (old, old))
    active = _posix(executables / ("b" * 32 + ".env.d") / "active.pid")
    prefix = (
        "start=$(sed 's/.*) //' /proc/$$/stat | awk '{print $20}'); "
        f'printf "%s %s\\n" "$$" "$start" > {shlex.quote(active)}; '
        f"touch -t 202001010000 {shlex.quote(_posix(executables / ('b' * 32 + '.env.d')))}; "
    )
    result = subprocess.run(
        [_shell(), "-c", prefix + abandoned_cleanup_script(_posix(ram), _posix(executables))],
        capture_output=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert not (ram / ("a" * 32 + ".env.d")).exists()
    assert not (executables / ("a" * 32 + ".env.d")).exists()
    assert (ram / ("b" * 32 + ".env.d") / "auth.env").exists()
    assert (executables / ("b" * 32 + ".env.d") / "copilot").exists()
    assert not (executables / ("c" * 32 + ".env.d")).exists()
