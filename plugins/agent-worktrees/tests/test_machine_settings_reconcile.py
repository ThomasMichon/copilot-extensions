"""Optional agent-machines reconciliation at Copilot launch."""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = PLUGIN_ROOT / "scripts"


def test_launch_layers_share_machine_settings_reconciler():
    ps_helper = (SCRIPTS / "reconcile-machine-settings.ps1").read_text(
        encoding="utf-8"
    )
    sh_helper = (SCRIPTS / "reconcile-machine-settings.sh").read_text(
        encoding="utf-8"
    )
    ps_launch = (PLUGIN_ROOT / "bin" / "launch-session.ps1").read_text(
        encoding="utf-8"
    )
    sh_launch = (PLUGIN_ROOT / "bin" / "launch-session.sh").read_text(
        encoding="utf-8"
    )
    ps_wrapper = (SCRIPTS / "launch-command.ps1").read_text(encoding="utf-8")
    sh_wrapper = (SCRIPTS / "launch-command.sh").read_text(encoding="utf-8")
    ps_setup = (SCRIPTS / "default-setup.ps1").read_text(encoding="utf-8")
    sh_setup = (SCRIPTS / "default-setup.sh").read_text(encoding="utf-8")

    for text in (ps_helper, sh_helper):
        assert "AGENT_WORKTREES_MACHINE_SETTINGS_RECONCILED" in text
        assert "restore" in text
        assert "--all-projects" in text
        assert "--only" in text
        assert "copilot.settings" in text
        assert "--apply" in text
        assert "--json" in text

    assert "reconcile-machine-settings.ps1" not in ps_launch
    assert "reconcile-machine-settings.ps1" in ps_setup
    assert "reconcile-machine-settings.sh" not in sh_launch
    assert "reconcile-machine-settings.sh" in sh_setup
    assert "reconcile-machine-settings.ps1" in ps_wrapper
    assert "reconcile-machine-settings.sh" in sh_wrapper
    assert "Remove-Item Env:AGENT_WORKTREES_MACHINE_SETTINGS_RECONCILED" in ps_setup
    assert "unset AGENT_WORKTREES_MACHINE_SETTINGS_RECONCILED" in sh_setup
    assert "-Recovery:$Recovery" in ps_setup
    assert "--recovery" in sh_setup


def test_all_installers_deploy_machine_settings_reconciler():
    installer_py = (
        PLUGIN_ROOT / "src" / "agent_worktrees" / "installer.py"
    ).read_text(encoding="utf-8")
    installer_ps = (SCRIPTS / "install.ps1").read_text(encoding="utf-8")
    installer_sh = (SCRIPTS / "install.sh").read_text(encoding="utf-8")

    for text in (installer_py, installer_ps, installer_sh):
        assert "launch-command.ps1" in text
        assert "launch-command.sh" in text
        assert "reconcile-machine-settings.ps1" in text
        assert "reconcile-machine-settings.sh" in text


def test_reconciler_noops_when_agent_machines_is_absent(tmp_path):
    env = os.environ.copy()
    env["PATH"] = str(tmp_path)

    if os.name == "nt":
        pwsh = shutil.which("pwsh")
        if not pwsh:
            pytest.skip("pwsh is unavailable")
        helper = SCRIPTS / "reconcile-machine-settings.ps1"
        command = (
            f". '{helper}'; "
            "Write-Output $env:AGENT_WORKTREES_MACHINE_SETTINGS_RECONCILED"
        )
        result = subprocess.run(
            [pwsh, "-NoProfile", "-Command", command],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
    else:
        bash = shutil.which("bash")
        if not bash:
            pytest.skip("bash is unavailable")
        helper = SCRIPTS / "reconcile-machine-settings.sh"
        result = subprocess.run(
            [
                bash,
                "-c",
                f". '{helper}'; "
                'printf "%s\\n" "$AGENT_WORKTREES_MACHINE_SETTINGS_RECONCILED"',
            ],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "1"


@pytest.mark.skipif(os.name != "nt", reason="PowerShell behavior is Windows-specific")
def test_powershell_launch_wrapper_reconciles_before_child(tmp_path):
    pwsh = shutil.which("pwsh")
    if not pwsh:
        pytest.skip("pwsh is unavailable")

    wrapper = SCRIPTS / "launch-command.ps1"
    calls = tmp_path / "calls.txt"
    child = tmp_path / "child.txt"
    fake = tmp_path / "agent-machines.cmd"
    target = tmp_path / "target.cmd"
    fake.write_text(
        "@echo off\r\n"
        'echo %*>>"%AGENT_MACHINES_MARKER%"\r\n'
        "exit /b 0\r\n",
        encoding="ascii",
    )
    target.write_text(
        "@echo off\r\n"
        'echo %AGENT_WORKTREES_MACHINE_SETTINGS_RECONCILED% %*>"%CHILD_MARKER%"\r\n',
        encoding="ascii",
    )
    env = os.environ.copy()
    env["PATH"] = str(tmp_path)
    env["AGENT_MACHINES_MARKER"] = str(calls)
    env["CHILD_MARKER"] = str(child)
    ordinary_arg = tmp_path / "other" / "default-setup.ps1"

    result = subprocess.run(
        [
            pwsh,
            "-NoProfile",
            "-File",
            str(wrapper),
            "--",
            str(target),
            "alpha",
            str(ordinary_arg),
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert calls.read_text(encoding="utf-8").strip() == (
        "restore --all-projects --only copilot.settings --apply --json"
    )
    child_output = child.read_text(encoding="utf-8").strip()
    assert child_output.startswith("alpha ")
    assert child_output.endswith("default-setup.ps1")

    calls.unlink()
    child.unlink()
    nested = tmp_path / "nested.cmd"
    final = tmp_path / "final.cmd"
    nested.write_text(
        "@echo off\r\n"
        '"%PWSH_PATH%" -NoProfile -File "%LAUNCH_WRAPPER%" -- '
        '"%FINAL_TARGET%" alpha\r\n',
        encoding="ascii",
    )
    final.write_text(
        "@echo off\r\n"
        'echo %AGENT_WORKTREES_MACHINE_SETTINGS_RECONCILED%%*>"%CHILD_MARKER%"\r\n',
        encoding="ascii",
    )
    env["PWSH_PATH"] = pwsh
    env["LAUNCH_WRAPPER"] = str(wrapper)
    env["FINAL_TARGET"] = str(final)
    nested_result = subprocess.run(
        [pwsh, "-NoProfile", "-File", str(wrapper), "--", str(nested)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert nested_result.returncode == 0, nested_result.stderr
    assert len(calls.read_text(encoding="utf-8").splitlines()) == 2
    assert child.read_text(encoding="utf-8").strip() == "alpha"


@pytest.mark.skipif(os.name != "nt", reason="PowerShell behavior is Windows-specific")
def test_powershell_reconciler_is_bounded_idempotent_and_recovery_safe(tmp_path):
    pwsh = shutil.which("pwsh")
    if not pwsh:
        pytest.skip("pwsh is unavailable")

    helper = SCRIPTS / "reconcile-machine-settings.ps1"
    marker = tmp_path / "calls.txt"
    fake = tmp_path / "agent-machines.cmd"
    fake.write_text(
        "@echo off\r\n"
        'echo %*>>"%AGENT_MACHINES_MARKER%"\r\n'
        "exit /b %AGENT_MACHINES_EXIT_CODE%\r\n",
        encoding="ascii",
    )
    env = os.environ.copy()
    env["PATH"] = str(tmp_path)
    env["AGENT_MACHINES_MARKER"] = str(marker)
    env["AGENT_MACHINES_EXIT_CODE"] = "0"

    command = (
        f". '{helper}'; . '{helper}'; "
        "Write-Output $env:AGENT_WORKTREES_MACHINE_SETTINGS_RECONCILED"
    )
    result = subprocess.run(
        [pwsh, "-NoProfile", "-Command", command],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "1"
    assert marker.read_text(encoding="utf-8").strip() == (
        "restore --all-projects --only copilot.settings --apply --json"
    )

    marker.unlink()
    env["AGENT_MACHINES_EXIT_CODE"] = "7"
    failed = subprocess.run(
        [pwsh, "-NoProfile", "-Command", f". '{helper}'"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert failed.returncode != 0
    assert "failed to reconcile Copilot settings" in failed.stderr
    assert marker.exists()

    marker.unlink()
    recovery = subprocess.run(
        [pwsh, "-NoProfile", "-Command", f". '{helper}' -Recovery"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert recovery.returncode == 0
    assert not marker.exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX shell behavior is non-Windows")
def test_posix_reconciler_is_bounded_idempotent_and_recovery_safe(tmp_path):
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("bash is unavailable")

    helper = SCRIPTS / "reconcile-machine-settings.sh"
    marker = tmp_path / "calls.txt"
    fake = tmp_path / "agent-machines"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        'printf "%s\\n" "$*" >> "$AGENT_MACHINES_MARKER"\n'
        'exit "${AGENT_MACHINES_EXIT_CODE:-0}"\n',
        encoding="ascii",
    )
    fake.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{tmp_path}{os.pathsep}{env['PATH']}"
    env["AGENT_MACHINES_MARKER"] = str(marker)
    env["AGENT_MACHINES_EXIT_CODE"] = "0"

    result = subprocess.run(
        [
            bash,
            "-c",
            f". '{helper}'; . '{helper}'; "
            'printf "%s\\n" "$AGENT_WORKTREES_MACHINE_SETTINGS_RECONCILED"',
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "1"
    assert marker.read_text(encoding="utf-8").strip() == (
        "restore --all-projects --only copilot.settings --apply --json"
    )

    marker.unlink()
    env["AGENT_MACHINES_EXIT_CODE"] = "7"
    failed = subprocess.run(
        [bash, "-c", f". '{helper}'"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert failed.returncode == 7
    assert "failed to reconcile Copilot settings" in failed.stderr
    assert marker.exists()

    marker.unlink()
    recovery = subprocess.run(
        [bash, "-c", f". '{helper}' --recovery"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert recovery.returncode == 0
    assert not marker.exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX shell behavior is non-Windows")
def test_posix_reconciler_can_execute_directly(tmp_path):
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("bash is unavailable")

    helper = SCRIPTS / "reconcile-machine-settings.sh"
    env = os.environ.copy()
    env["PATH"] = str(tmp_path)
    result = subprocess.run(
        [bash, str(helper)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "can only" not in result.stderr


@pytest.mark.skipif(os.name == "nt", reason="POSIX shell behavior is non-Windows")
def test_posix_launch_wrapper_reconciles_before_child(tmp_path):
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("bash is unavailable")

    wrapper = SCRIPTS / "launch-command.sh"
    calls = tmp_path / "calls.txt"
    child = tmp_path / "child.txt"
    fake = tmp_path / "agent-machines"
    target = tmp_path / "target"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        'printf "%s\\n" "$*" >> "$AGENT_MACHINES_MARKER"\n',
        encoding="ascii",
    )
    target.write_text(
        "#!/usr/bin/env bash\n"
        'printf "%s %s\\n" "$AGENT_WORKTREES_MACHINE_SETTINGS_RECONCILED" '
        '"$*" > "$CHILD_MARKER"\n',
        encoding="ascii",
    )
    fake.chmod(0o755)
    target.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{tmp_path}{os.pathsep}{env['PATH']}"
    env["AGENT_MACHINES_MARKER"] = str(calls)
    env["CHILD_MARKER"] = str(child)
    ordinary_arg = tmp_path / "other" / "default-setup.sh"

    result = subprocess.run(
        [bash, str(wrapper), "--", str(target), "alpha", str(ordinary_arg)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert calls.read_text(encoding="utf-8").strip() == (
        "restore --all-projects --only copilot.settings --apply --json"
    )
    child_output = child.read_text(encoding="utf-8").strip()
    assert child_output.startswith("alpha ")
    assert child_output.endswith("default-setup.sh")

    calls.unlink()
    child.unlink()
    nested = tmp_path / "nested"
    final = tmp_path / "final"
    nested.write_text(
        "#!/usr/bin/env bash\n"
        'exec "$BASH_PATH" "$LAUNCH_WRAPPER" -- "$FINAL_TARGET" alpha\n',
        encoding="ascii",
    )
    final.write_text(
        "#!/usr/bin/env bash\n"
        'printf "%s%s\\n" "${AGENT_WORKTREES_MACHINE_SETTINGS_RECONCILED:-}" '
        '"$*" > "$CHILD_MARKER"\n',
        encoding="ascii",
    )
    nested.chmod(0o755)
    final.chmod(0o755)
    env["BASH_PATH"] = bash
    env["LAUNCH_WRAPPER"] = str(wrapper)
    env["FINAL_TARGET"] = str(final)
    nested_result = subprocess.run(
        [bash, str(wrapper), "--", str(nested)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert nested_result.returncode == 0, nested_result.stderr
    assert len(calls.read_text(encoding="utf-8").splitlines()) == 2
    assert child.read_text(encoding="utf-8").strip() == "alpha"


def _fake_resolver_and_runtime(home: Path, activity_marker: Path) -> None:
    """Build a fake ``resolve-runtime.sh`` + AW_PY the launch-command wrapper
    can use to emit its Stage 3 (copilot_invocation_attempted) mark without a
    real agent-worktrees install."""
    runtime_bin = home / ".agent-worktrees" / "bin"
    runtime_bin.mkdir(parents=True)
    fake_py = runtime_bin / "fake-agent-worktrees-python"
    fake_py.write_text(
        "#!/usr/bin/env bash\n"
        'shift 2  # drop -I -m\n'
        'if [[ "$1" == "agent_worktrees" && "$2" == "get" && "$3" == "worktree-id" ]]; then\n'
        '    printf "wt-fake\\n"\n'
        '    exit 0\n'
        'fi\n'
        'if [[ "$1" == "agent_worktrees" && "$2" == "activity-log" ]]; then\n'
        f'    printf "%s\\n" "$*" >> "{activity_marker}"\n'
        '    exit 0\n'
        'fi\n'
        'exit 1\n',
        encoding="ascii",
    )
    fake_py.chmod(0o755)
    (runtime_bin / "resolve-runtime.sh").write_text(
        f'AW_PY="{fake_py}"\nexport AW_PY\n', encoding="ascii",
    )


def _fake_agent_machines(bin_dir: Path) -> None:
    """A no-op ``agent-machines`` so the wrapper's reconcile step succeeds
    without a real install (same pattern as the reconcile-before-child test
    above)."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    fake = bin_dir / "agent-machines"
    fake.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="ascii")
    fake.chmod(0o755)


@pytest.mark.skipif(os.name == "nt", reason="POSIX shell behavior is non-Windows")
def test_posix_launch_wrapper_emits_stage_3_for_non_default_setup_target(
    tmp_path, monkeypatch,
):
    """Stage 3 (copilot_invoked): the config-template/legacy-setup path never
    reaches default-setup.sh's own emitter, so launch-command.sh -- the one
    seam every resolved command passes through -- marks it with the coarser
    copilot_invocation_attempted event instead."""
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("bash is unavailable")

    wrapper = SCRIPTS / "launch-command.sh"
    home = tmp_path / "home"
    activity_marker = tmp_path / "activity.txt"
    _fake_resolver_and_runtime(home, activity_marker)
    _fake_agent_machines(tmp_path / "bin")
    target = tmp_path / "legacy-setup.sh"
    target.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="ascii")
    target.chmod(0o755)

    env = os.environ.copy()
    # A prior test in the same process may have left this set on the real
    # os.environ (cmd_launch sets it for a real cell/contextual launch and
    # relies on the child process's own lifetime to bound it) -- clear it so
    # this test deterministically exercises the legacy $HOME fallback.
    env.pop("AGENT_WORKTREES_LAUNCH_RUNTIME_ROOT", None)
    env["HOME"] = str(home)
    env["PATH"] = f"{tmp_path / 'bin'}{os.pathsep}{env['PATH']}"
    result = subprocess.run(
        [bash, str(wrapper), "--", str(target)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    deadline = time.monotonic() + 10.0
    while not activity_marker.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    logged = activity_marker.read_text(encoding="utf-8").strip()
    assert "copilot_invocation_attempted" in logged
    assert "--worktree-id wt-fake" in logged


@pytest.mark.skipif(os.name == "nt", reason="POSIX shell behavior is non-Windows")
def test_posix_launch_wrapper_skips_stage_3_for_default_setup_target(
    tmp_path,
):
    """default-setup.sh emits its own precise copilot_invoked -- the wrapper
    must not also emit the coarser attempted mark for that path."""
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("bash is unavailable")

    wrapper = SCRIPTS / "launch-command.sh"
    home = tmp_path / "home"
    activity_marker = tmp_path / "activity.txt"
    _fake_resolver_and_runtime(home, activity_marker)
    _fake_agent_machines(tmp_path / "bin")
    default_setup = tmp_path / "other" / "default-setup.sh"
    default_setup.parent.mkdir()
    default_setup.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="ascii")
    default_setup.chmod(0o755)

    env = os.environ.copy()
    env.pop("AGENT_WORKTREES_LAUNCH_RUNTIME_ROOT", None)
    env["HOME"] = str(home)
    env["PATH"] = f"{tmp_path / 'bin'}{os.pathsep}{env['PATH']}"
    # Match _build_launch_cmd's real shape for the normalized path: arg1 is
    # literally "bash", arg2 ends with "default-setup.sh" -- the exact pair
    # the wrapper's own detection checks for.
    result = subprocess.run(
        [bash, str(wrapper), "--", "bash", str(default_setup)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    time.sleep(0.2)
    assert not activity_marker.exists()
