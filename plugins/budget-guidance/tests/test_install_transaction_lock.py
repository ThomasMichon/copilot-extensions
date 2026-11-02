"""Source-free and source-qualified writers use the same transaction authority."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from test_source_install_transactions import SCRIPTS, _function, _host, _payload, _setup


def _native_environment(root: Path) -> dict[str, str]:
    home = root / "home"
    home.mkdir()
    paths = [str(Path(sys.executable).parent)]
    if os.name == "nt":
        paths.extend(os.environ.get("PATH", "").split(os.pathsep))
    else:
        # WSL's inherited Windows PATH makes repeated missing-command probes expensive.
        for name in ("bash", "pwsh", "powershell", "git"):
            executable = shutil.which(name)
            if executable:
                paths.append(str(Path(executable).parent))
        paths.extend(os.defpath.split(os.pathsep))
    env = {
        **os.environ,
        "HOME": str(home),
        "USERPROFILE": str(home),
        "PATH": os.pathsep.join(dict.fromkeys(paths)),
        "COPILOT_EXTENSIONS_TEST_CONTAINED": "1",
        "COMPUTERNAME": "TEST-HOST",
        "COPILOT_PLUGIN_INSTALL_LOCK_TIMEOUT_SEC": "1",
    }
    for name in (
        "COPILOT_PLUGIN_INSTALL_SMOKE",
        "COPILOT_PLUGIN_INSTALL_STAGED",
        "COPILOT_PLUGIN_STAGED_FROM",
        "COPILOT_EXTENSIONS_HUMAN_UTF8",
    ):
        env.pop(name, None)
    return env


def _native_command(shell: str, payload: Path, root: Path, action: str) -> list[str]:
    host = _host(shell)
    if shell == "bash":
        return [
            host,
            str(payload / "scripts" / "install.sh"),
            action,
            "--install-dir",
            str(root),
        ]
    return [
        host,
        "-NoProfile",
        "-NonInteractive",
        "-File",
        str(payload / "scripts" / "install.ps1"),
        "-Action",
        action,
        "-InstallDir",
        str(root),
    ]


def test_all_budget_mutating_actions_share_the_install_transaction_gate() -> None:
    powershell = (SCRIPTS / "install.ps1").read_text()
    shell = (SCRIPTS / "install.sh").read_text()
    assert (
        "$needsInstallLock = $Action -in @('install', 'update', 'stamp', 'provision', 'uninstall')"
        in powershell
    )
    assert "if ($needsInstallLock -and -not (Enter-InstallLock))" in powershell
    gate = re.search(
        r'(?m)^if \[\[ "\$ACTION" == install .* "\$ACTION" == uninstall \]\]; then\n'
        r"    _enter_install_lock",
        shell,
    )
    assert gate, "Source-free and source writers must enter the same unconditional lock gate"
    assert ".install.lock.d" not in shell
    assert "trap '_exit_install_lock; exit 130' INT" in shell
    assert "trap '_exit_install_lock; exit 143' TERM" in shell


@pytest.mark.parametrize("shell", ["pwsh", "powershell", "bash"])
@pytest.mark.parametrize("source_contender", [False, True])
def test_source_and_source_free_writers_contend_before_snapshot_publication(
    tmp_path: Path,
    shell: str,
    source_contender: bool,
) -> None:
    if shell == "bash" and os.name == "nt":
        pytest.skip("Full POSIX transaction requires real PID symlink and flock authority")
    holder_payload = _payload(tmp_path / "holder")
    contender_payload = _payload(tmp_path / "contender")
    if source_contender:
        (holder_payload / "runtime-source.json").unlink()
    else:
        (contender_payload / "runtime-source.json").unlink()
    env = _native_environment(tmp_path)
    root = tmp_path / "install"
    root.mkdir()
    previous = {"payload-dir": b"previous-payload", "stamped-version": b"previous-version"}
    for name, content in previous.items():
        (root / name).write_bytes(content)
    text = _setup(tmp_path, holder_payload, shell)
    if shell == "bash":
        names = ("_install_lock_timeout", "_enter_install_lock", "_exit_install_lock")
        text += '\n__install_lock_link=""\n__install_lock_fd=""\n'
        text += "\n".join(_function(name, shell) for name in names)
        text += """
_initialize_source_runtime
_enter_install_lock
trap '_exit_install_lock' EXIT
printf 'READY\\n'
IFS= read -r release
"""
    else:
        names = ("Get-InstallLockTimeoutSec", "Enter-InstallLock", "Exit-InstallLock")
        text += "\n$script:InstallLockHandle = $null\n"
        text += "\n".join(_function(name, shell) for name in names)
        text += """
Initialize-SourceRuntime
if (-not (Enter-InstallLock)) { throw 'Holder did not acquire the lock' }
try {
    [Console]::Out.WriteLine('READY')
    [void][Console]::ReadLine()
} finally { Exit-InstallLock }
"""
    harness = tmp_path / ("holder.sh" if shell == "bash" else "holder.ps1")
    harness.write_text(text, encoding="utf-8", newline="\n")
    host = _host(shell)
    command = (
        [host, str(harness)]
        if shell == "bash"
        else [
            host,
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(harness),
        ]
    )
    with subprocess.Popen(  # noqa: S603 - trusted native host and isolated lock-holder fixture
        command,
        cwd=tmp_path,
        env=env,
        text=True,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    ) as holder:
        try:
            assert holder.stdout is not None
            ready = holder.stdout.readline()
            if ready.strip() != "READY":
                assert holder.stderr is not None
                pytest.fail(f"Lock-holder initialization failed: {ready}{holder.stderr.read()}")
            contender = subprocess.run(  # noqa: S603 - isolated native installer fixture
                _native_command(shell, contender_payload, root, "stamp"),
                cwd=tmp_path,
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            detail = contender.stdout + contender.stderr
            assert contender.returncode != 0, detail
            assert "refusing to proceed unlocked" in detail, detail
            for name, content in previous.items():
                assert (root / name).read_bytes() == content
            assert not (root / "snapshots").exists()
        finally:
            output, errors = holder.communicate("release\n", timeout=15)
        assert holder.returncode == 0, output + errors
    retry = subprocess.run(  # noqa: S603 - isolated native installer fixture
        _native_command(shell, contender_payload, root, "stamp"),
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert retry.returncode == 0, retry.stdout + retry.stderr
    assert (root / "payload-dir").read_bytes() != previous["payload-dir"]


@pytest.mark.parametrize("shell", ["pwsh", "powershell", "bash"])
@pytest.mark.parametrize("ownership", ["owned", "missing", "foreign"])
def test_source_free_uninstall_preserves_transaction_and_staging_authority(
    tmp_path: Path,
    shell: str,
    ownership: str,
) -> None:
    if shell == "bash" and os.name == "nt":
        pytest.skip("Full POSIX transaction requires real PID symlink and flock authority")
    if shell != "bash" and os.name != "nt":
        pytest.skip("Finite POSIX uninstall requires native Bash custody")
    payload = _payload(tmp_path)
    (payload / "runtime-source.json").unlink()
    env = _native_environment(tmp_path)
    root = tmp_path / "install"
    root.mkdir()
    lock = root / ".install.lock"
    lock.write_bytes(b"existing-lock")
    identity = lock.stat().st_ino
    (root / "current-version").write_text("1.2.2")
    (root / "config.json").write_text("{}")
    manifest = root / "deploy-manifest.json"
    if ownership != "missing":
        manifest.write_text(json.dumps({
            "schema_version": 3,
            "service": "budget-guidance" if ownership == "owned" else "other-plugin",
            "source": {"plugin": "budget-guidance", "kind": "local"},
        }))
    stage = root / ".install-stage" / "owned"
    stage.mkdir(parents=True)
    (stage / "source.txt").write_bytes(b"staged-owner")
    result = subprocess.run(  # noqa: S603 - isolated native uninstall fixture
        _native_command(shell, payload, root, "uninstall"),
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert lock.stat().st_ino == identity
    if ownership == "owned":
        assert result.returncode == 0, result.stdout + result.stderr
        assert not (root / "current-version").exists()
        assert not manifest.exists()
    else:
        assert result.returncode != 0, result.stdout + result.stderr
        expected = "payload-dir" if ownership == "missing" else "Foreign or invalid deployment manifest"
        assert expected in result.stdout + result.stderr
        assert (root / "current-version").read_text() == "1.2.2"
        assert manifest.exists() == (ownership == "foreign")
    assert (root / "config.json").read_text() == "{}"
    assert (stage / "source.txt").read_bytes() == b"staged-owner"
