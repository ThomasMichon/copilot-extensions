"""Snapshot-install regressions for agent-ssh's stamped first-use path."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest


_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_HARNESS_TIMEOUT_SECONDS = 30


def _isolated_install_env(home: Path) -> dict[str, str]:
    env = os.environ.copy()
    roots = {
        "HOME": home,
        "USERPROFILE": home,
        "APPDATA": home / "AppData" / "Roaming",
        "LOCALAPPDATA": home / "AppData" / "Local",
        "PROGRAMDATA": home / "ProgramData",
        "XDG_CONFIG_HOME": home / ".config",
        "XDG_CACHE_HOME": home / ".cache",
        "XDG_DATA_HOME": home / ".local" / "share",
        "XDG_STATE_HOME": home / ".local" / "state",
        "XDG_RUNTIME_DIR": home / "run",
        "TEMP": home / "tmp",
        "TMP": home / "tmp",
        "TMPDIR": home / "tmp",
        "COPILOT_HOME": home / ".copilot",
        "AGENT_HOME": home,
        "AGENT_SSH_HOME": home / ".agent-ssh",
    }
    for path in roots.values():
        path.mkdir(parents=True, exist_ok=True)
    env.update({name: str(path) for name, path in roots.items()})
    env["COPILOT_PLUGIN_INSTALL_STAGED"] = "1"
    for name in ("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV"):
        env.pop(name, None)
    return env


def _stage_payload(tmp_path: Path) -> Path:
    payload = tmp_path / "plugins" / "agent-ssh"
    payload.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        _PLUGIN_ROOT,
        payload,
        ignore=shutil.ignore_patterns(
            ".git",
            ".venv",
            "__pycache__",
            ".pytest_cache",
            "tests",
        ),
    )
    staged_libs = tmp_path / "libs"
    staged_libs.mkdir(parents=True, exist_ok=True)
    for lib in (
        "installer-engine",
        "agent-procutil",
        "ssh-manager",
        "venue-copilot",
        "zdd",
        "remote-login-shell",
    ):
        shutil.copytree(_PLUGIN_ROOT.parents[1] / "libs" / lib, staged_libs / lib)
    return payload


def _host_pip_index_url() -> str | None:
    candidates = (
        Path(r"C:\ProgramData\pip\pip.ini"),
        Path("/etc/pip.conf"),
        Path("/etc/xdg/pip/pip.conf"),
    )
    for candidate in candidates:
        try:
            text = candidate.read_text(encoding="utf-8")
        except OSError:
            continue
        match = re.search(r"(?m)^\s*index-url\s*=\s*(\S+)\s*$", text)
        if match:
            return match.group(1)
    return None


def _expected_version() -> str:
    version = next(
        line.split('"')[1]
        for line in (_PLUGIN_ROOT / "pyproject.toml").read_text(encoding="utf-8").splitlines()
        if line.startswith("version = ")
    )
    return version.replace("-dev", ".dev")


def _source_version() -> str:
    return next(
        line.split('"')[1]
        for line in (_PLUGIN_ROOT / "pyproject.toml").read_text(encoding="utf-8").splitlines()
        if line.startswith("version = ")
    )


def _shell_function_source(source: str, name: str, next_marker: str) -> str:
    start_marker = f"{name}() {{"
    assert source.count(start_marker) == 1, f"missing unique {start_marker!r}"
    start = source.index(start_marker)
    end = source.index(next_marker, start + len(start_marker))
    assert end > start, f"{next_marker!r} does not follow {start_marker!r}"
    return source[start:end]


def _engine_shell_function_source(name: str, next_marker: str) -> str:
    source = (_PLUGIN_ROOT.parents[1] / "libs" / "installer-engine" / "installer-engine.sh").read_text(
        encoding="utf-8"
    )
    return _shell_function_source(source, name, next_marker)


def _isolate_path_without_python3(env: dict[str, str]) -> dict[str, str]:
    stripped = []
    for part in env.get("PATH", "").split(os.pathsep):
        if not part:
            continue
        path = Path(part)
        if any((path / name).exists() for name in ("python3", "python3.exe")):
            continue
        stripped.append(part)
    env = {**env, "PATH": os.pathsep.join(stripped) or "/bin"}
    return env


@pytest.mark.skipif(
    os.name == "nt" or shutil.which("uv") is None,
    reason="POSIX snapshot coverage",
)
def test_stamp_supports_first_use_provision_from_snapshot_only(tmp_path: Path) -> None:
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash is unavailable")

    payload = _stage_payload(tmp_path)
    home = tmp_path / "home"
    env = _isolated_install_env(home)
    internal_index = _host_pip_index_url()
    if internal_index and not (env.get("UV_DEFAULT_INDEX") or env.get("UV_INDEX_URL")):
        env["UV_DEFAULT_INDEX"] = internal_index

    stamp = subprocess.run(
        [
            bash,
            str(payload / "scripts" / "install.sh"),
            "stamp",
            "--install-dir",
            str(home / ".agent-ssh"),
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert stamp.returncode == 0, stamp.stderr

    snapshot = Path((home / ".agent-ssh" / "payload-dir").read_text(encoding="utf-8").strip())
    assert (snapshot / "scripts" / "installer-engine.sh").is_file()
    assert (snapshot / "scripts" / "installer-engine.ps1").is_file()
    assert '. "$SCRIPT_DIR/installer-engine.sh"' in (
        snapshot / "scripts" / "install.sh"
    ).read_text(encoding="utf-8")
    assert ". (Join-Path $PSScriptRoot 'installer-engine.ps1')" in (
        snapshot / "scripts" / "install.ps1"
    ).read_text(encoding="utf-8")
    for lib in ("agent-procutil", "ssh-manager", "venue-copilot", "zdd", "remote-login-shell"):
        assert (snapshot / "libs" / lib / "pyproject.toml").is_file()

    shutil.rmtree(payload)
    shutil.rmtree(tmp_path / "libs")

    provision = subprocess.run(
        [
            bash,
            str(snapshot / "scripts" / "install.sh"),
            "provision",
            "--install-dir",
            str(home / ".agent-ssh"),
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=420,
        check=False,
    )
    assert provision.returncode == 0, provision.stderr

    invoke = subprocess.run(
        [bash, str(home / ".local" / "bin" / "agent-ssh"), "version"],
        env=env,
        capture_output=True,
        text=True,
        timeout=420,
        check=False,
    )
    assert invoke.returncode == 0, invoke.stderr
    assert f"agent-ssh {_expected_version()}" in invoke.stdout


@pytest.mark.skipif(
    os.name == "nt" or shutil.which("uv") is None,
    reason="POSIX snapshot coverage",
)
def test_stamp_bash_binstub_treats_shell_metacharacters_as_path_data(tmp_path: Path) -> None:
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash is unavailable")

    payload = _stage_payload(tmp_path)
    home = tmp_path / "home"
    marker = tmp_path / "should-not-run"
    install_dir = home / f".agent-ssh-$(touch {marker})"
    env = _isolated_install_env(home)

    stamp = subprocess.run(
        [
            bash,
            str(payload / "scripts" / "install.sh"),
            "stamp",
            "--install-dir",
            str(install_dir),
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert stamp.returncode == 0, stamp.stderr

    invoke = subprocess.run(
        [bash, str(home / ".local" / "bin" / "agent-ssh"), "version"],
        env={**env, "AGENT_SSH_NO_SELFPROVISION": "1"},
        capture_output=True,
        text=True,
        timeout=_HARNESS_TIMEOUT_SECONDS,
        check=False,
    )
    assert invoke.returncode == 1
    assert not marker.exists()


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv is required")
def test_powershell_stamp_posix_shell_shim_quotes_apostrophes_without_python3(
    tmp_path: Path,
) -> None:
    pwsh = shutil.which("pwsh") or shutil.which("powershell")
    bash = shutil.which("bash")
    if not pwsh or not bash:
        pytest.skip("pwsh/bash unavailable")
    if os.name == "nt":
        pytest.skip("POSIX shell-shim coverage")

    payload = _stage_payload(tmp_path)
    home = tmp_path / "home"
    env = _isolate_path_without_python3(_isolated_install_env(home))
    tool_bin = tmp_path / "tool-bin"
    tool_bin.mkdir()
    for name in ("sh", "mkdir", "chmod", "python"):
        target = shutil.which(name)
        if target:
            (tool_bin / name).symlink_to(target)
    env["PATH"] = str(tool_bin) + os.pathsep + env["PATH"]
    assert shutil.which("python3", path=env["PATH"]) is None
    install_dir = home / ".agent-ssh-o'hara"

    system_root = home / "systemroot"
    where_exe = system_root / "System32" / "where.exe"
    powershell_exe = system_root / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    where_exe.parent.mkdir(parents=True, exist_ok=True)
    powershell_exe.parent.mkdir(parents=True, exist_ok=True)
    where_exe.write_text(
        "#!/usr/bin/env sh\n"
        "if [ \"$1\" = pwsh ]; then\n"
        f"  printf '%s\\n' '{pwsh}'\n"
        "fi\n",
        encoding="utf-8",
    )
    where_exe.chmod(0o755)
    powershell_exe.write_text(
        "#!/usr/bin/env sh\n"
        f"exec '{pwsh}' \"$@\"\n",
        encoding="utf-8",
    )
    powershell_exe.chmod(0o755)
    env["SystemRoot"] = str(system_root)

    stamp = subprocess.run(
        [
            pwsh,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(payload / "scripts" / "install.ps1"),
            "stamp",
            "-InstallDir",
            str(install_dir),
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert stamp.returncode == 0, stamp.stderr

    invoke = subprocess.run(
        [bash, str(home / ".local" / "bin" / "agent-ssh"), "version"],
        env={**env, "AGENT_SSH_NO_SELFPROVISION": "1"},
        capture_output=True,
        text=True,
        timeout=_HARNESS_TIMEOUT_SECONDS,
        check=False,
    )
    assert invoke.returncode == 1
    assert "runtime not provisioned" in invoke.stderr


@pytest.mark.skipif(
    os.name == "nt" or shutil.which("bash") is None,
    reason="POSIX binstub generation coverage",
)
def test_shared_binstub_generator_quotes_snapshot_installer_path(tmp_path: Path) -> None:
    bash = shutil.which("bash")
    assert bash is not None
    write_binstub = _engine_shell_function_source(
        "write_simple_binstub",
        '\n    _ok "Binstub: $stub (self-provisioning)"',
    ) + '\n    _ok "Binstub: $stub (self-provisioning)"\n}'
    runtime_root = tmp_path / "root with space's"
    local_bin = tmp_path / "bin"
    install_bin = tmp_path / "install-bin"
    resolver = tmp_path / "resolve-runtime.sh"
    resolver.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    script = tmp_path / "generate.sh"
    script.write_text(
        "\n".join(
            [
                "set -euo pipefail",
                "_ok() { :; }",
                write_binstub,
                (
                    "write_simple_binstub test-cmd test_module "
                    f"\"{runtime_root}\" "
                    f"\"{local_bin}\" "
                    f"\"{install_bin}\" "
                    "\"scripts/my installer's.sh\" "
                    "TEST_NO_SELFPROVISION "
                    "\"\" "
                    f"\"{resolver}\""
                ),
                f"bash -n \"{local_bin / 'test-cmd'}\"",
            ]
        ),
        encoding="utf-8",
    )
    proc = subprocess.run(
        [bash, str(script)],
        capture_output=True,
        text=True,
        timeout=_HARNESS_TIMEOUT_SECONDS,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


@pytest.mark.skipif(
    os.name == "nt" or shutil.which("uv") is None,
    reason="POSIX snapshot coverage",
)
def test_stamp_fails_before_publish_when_required_library_missing(tmp_path: Path) -> None:
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash is unavailable")

    payload = _stage_payload(tmp_path)
    home = tmp_path / "home"
    env = _isolated_install_env(home)
    shutil.rmtree(tmp_path / "libs" / "ssh-manager")

    stamp = subprocess.run(
        [
            bash,
            str(payload / "scripts" / "install.sh"),
            "stamp",
            "--install-dir",
            str(home / ".agent-ssh"),
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert stamp.returncode != 0
    assert "Cannot locate required snapshot library: ssh-manager" in stamp.stderr
    assert not (home / ".agent-ssh" / "payload-dir").exists()


@pytest.mark.skipif(
    os.name == "nt" or shutil.which("uv") is None,
    reason="POSIX snapshot coverage",
)
def test_stamp_fails_before_publish_when_payload_copy_fails(tmp_path: Path) -> None:
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash is unavailable")

    payload = _stage_payload(tmp_path)
    home = tmp_path / "home"
    env = _isolated_install_env(home)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    cp_shim = fake_bin / "cp"
    cp_shim.write_text(
        "#!/usr/bin/env bash\n"
        "for arg in \"$@\"; do\n"
        "  if [[ \"$arg\" == */src ]]; then\n"
        "    printf 'fault-injected cp failure\\n' >&2\n"
        "    exit 1\n"
        "  fi\n"
        "done\n"
        "exec /bin/cp \"$@\"\n",
        encoding="utf-8",
    )
    cp_shim.chmod(0o755)
    env["PATH"] = str(fake_bin) + os.pathsep + env.get("PATH", "")

    stamp = subprocess.run(
        [
            bash,
            str(payload / "scripts" / "install.sh"),
            "stamp",
            "--install-dir",
            str(home / ".agent-ssh"),
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert stamp.returncode != 0
    assert "Failed to copy snapshot payload entry: src" in stamp.stderr
    assert not (home / ".agent-ssh" / "payload-dir").exists()


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv is required")
def test_powershell_stamp_fails_before_publish_when_required_library_missing(
    tmp_path: Path,
) -> None:
    pwsh = shutil.which("pwsh") or shutil.which("powershell")
    if not pwsh:
        pytest.skip("PowerShell is unavailable")

    payload = _stage_payload(tmp_path)
    home = tmp_path / "home"
    env = _isolated_install_env(home)
    shutil.rmtree(tmp_path / "libs" / "ssh-manager")

    stamp = subprocess.run(
        [
            pwsh,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(payload / "scripts" / "install.ps1"),
            "stamp",
            "-InstallDir",
            str(home / ".agent-ssh"),
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert stamp.returncode != 0
    assert "Cannot locate required snapshot library: ssh-manager" in stamp.stderr
    assert not (home / ".agent-ssh" / "payload-dir").exists()


@pytest.mark.skipif(
    os.name == "nt" or shutil.which("uv") is None,
    reason="POSIX snapshot coverage",
)
def test_stamp_does_not_reuse_snapshot_when_runtime_markers_disagree(tmp_path: Path) -> None:
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash is unavailable")

    payload = _stage_payload(tmp_path)
    home = tmp_path / "home"
    env = _isolated_install_env(home)
    env["COPILOT_PLUGIN_STAGED_FROM"] = str(
        home / ".copilot" / "installed-plugins" / "copilot-extensions" / "agent-ssh"
    )
    install_dir = home / ".agent-ssh"
    old_snapshot = install_dir / "snapshots" / "stale"
    (old_snapshot / "scripts").mkdir(parents=True, exist_ok=True)
    (old_snapshot / "libs" / "agent-procutil").mkdir(parents=True, exist_ok=True)
    (old_snapshot / "libs" / "ssh-manager").mkdir(parents=True, exist_ok=True)
    (old_snapshot / "libs" / "venue-copilot").mkdir(parents=True, exist_ok=True)
    (old_snapshot / "libs" / "zdd").mkdir(parents=True, exist_ok=True)
    (old_snapshot / "libs" / "remote-login-shell").mkdir(parents=True, exist_ok=True)
    for rel in (
        "scripts/installer-engine.sh",
        "scripts/installer-engine.ps1",
        "libs/agent-procutil/pyproject.toml",
        "libs/ssh-manager/pyproject.toml",
        "libs/venue-copilot/pyproject.toml",
        "libs/zdd/pyproject.toml",
        "libs/remote-login-shell/pyproject.toml",
    ):
        path = old_snapshot / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
    (old_snapshot / ".source-payload-path").write_text(env["COPILOT_PLUGIN_STAGED_FROM"], encoding="utf-8")
    (old_snapshot / ".snapshot-version").write_text("0.0.0-dev0", encoding="utf-8")
    install_dir.mkdir(parents=True, exist_ok=True)
    (install_dir / "payload-dir").write_text(str(old_snapshot), encoding="utf-8")
    (install_dir / "stamped-version").write_text(_source_version(), encoding="utf-8")

    stamp = subprocess.run(
        [
            bash,
            str(payload / "scripts" / "install.sh"),
            "stamp",
            "--install-dir",
            str(install_dir),
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert stamp.returncode == 0, stamp.stderr
    published = Path((install_dir / "payload-dir").read_text(encoding="utf-8").strip())
    assert published != old_snapshot
    assert (published / ".snapshot-version").read_text(encoding="utf-8").strip() == _source_version()


@pytest.mark.skipif(
    os.name == "nt" or shutil.which("uv") is None,
    reason="POSIX snapshot coverage",
)
def test_stamp_does_not_publish_older_snapshot_over_newer_marketplace_payload(
    tmp_path: Path,
) -> None:
    bash = shutil.which("bash")
    assert bash is not None

    payload = _stage_payload(tmp_path)
    home = tmp_path / "home"
    env = _isolated_install_env(home)
    source_path = str(
        home / ".copilot" / "installed-plugins" / "copilot-extensions" / "agent-ssh"
    )
    env["COPILOT_PLUGIN_STAGED_FROM"] = source_path
    install_dir = home / ".agent-ssh"
    newer_snapshot = install_dir / "snapshots" / "newer"
    for rel in (
        "scripts/installer-engine.sh",
        "scripts/installer-engine.ps1",
        "libs/agent-procutil/pyproject.toml",
        "libs/ssh-manager/pyproject.toml",
        "libs/venue-copilot/pyproject.toml",
        "libs/zdd/pyproject.toml",
        "libs/remote-login-shell/pyproject.toml",
    ):
        path = newer_snapshot / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
    (newer_snapshot / ".source-payload-path").write_text(source_path, encoding="utf-8")
    (newer_snapshot / ".snapshot-version").write_text("999.0.0", encoding="utf-8")
    install_dir.mkdir(parents=True, exist_ok=True)
    (install_dir / "payload-dir").write_text(str(newer_snapshot), encoding="utf-8")

    stamp = subprocess.run(
        [
            bash,
            str(payload / "scripts" / "install.sh"),
            "stamp",
            "--install-dir",
            str(install_dir),
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert stamp.returncode == 0, stamp.stderr
    assert Path((install_dir / "payload-dir").read_text(encoding="utf-8").strip()) == newer_snapshot
    assert "newer than" in stamp.stdout


@pytest.mark.skipif(
    os.name == "nt" or shutil.which("uv") is None or shutil.which("flock") is None,
    reason="POSIX snapshot publication lock coverage",
)
def test_stamp_waiting_on_publication_lock_does_not_overwrite_newer_snapshot(
    tmp_path: Path,
) -> None:
    bash = shutil.which("bash")
    assert bash is not None

    payload = _stage_payload(tmp_path)
    home = tmp_path / "home"
    env = _isolated_install_env(home)
    source_path = str(
        home / ".copilot" / "installed-plugins" / "copilot-extensions" / "agent-ssh"
    )
    env["COPILOT_PLUGIN_STAGED_FROM"] = source_path
    install_dir = home / ".agent-ssh"
    install_dir.mkdir(parents=True, exist_ok=True)
    lock_script = tmp_path / "hold-lock.sh"
    lock_script.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "lock=$1\n"
        "ready=$2\n"
        "exec 8>\"$lock\"\n"
        "flock 8\n"
        "printf locked > \"$ready\"\n"
        "sleep 5\n",
        encoding="utf-8",
    )
    lock_script.chmod(0o755)
    ready = tmp_path / "lock-ready"
    holder = subprocess.Popen(
        [bash, str(lock_script), str(install_dir / ".stamp-publication.lock"), str(ready)],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(50):
            if ready.exists():
                break
            import time
            time.sleep(0.1)
        assert ready.exists(), "lock holder never signaled readiness"

        stamp = subprocess.Popen(
            [
                bash,
                str(payload / "scripts" / "install.sh"),
                "stamp",
                "--install-dir",
                str(install_dir),
            ],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        newer_snapshot = install_dir / "snapshots" / "newer"
        for rel in (
            "scripts/installer-engine.sh",
            "scripts/installer-engine.ps1",
            "libs/agent-procutil/pyproject.toml",
            "libs/ssh-manager/pyproject.toml",
            "libs/venue-copilot/pyproject.toml",
            "libs/zdd/pyproject.toml",
            "libs/remote-login-shell/pyproject.toml",
        ):
            path = newer_snapshot / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("", encoding="utf-8")
        (newer_snapshot / ".source-payload-path").write_text(source_path, encoding="utf-8")
        (newer_snapshot / ".snapshot-version").write_text("999.0.0", encoding="utf-8")
        (install_dir / "payload-dir").write_text(str(newer_snapshot), encoding="utf-8")

        stdout, stderr = stamp.communicate(timeout=15)
        assert stamp.returncode == 0, stderr
        assert Path((install_dir / "payload-dir").read_text(encoding="utf-8").strip()) == newer_snapshot
        assert "newer than" in stdout
    finally:
        holder.terminate()
        holder.wait(timeout=10)


@pytest.mark.skipif(
    os.name == "nt" or shutil.which("bash") is None,
    reason="POSIX shell materializer coverage",
)
def test_snapshot_materializer_fails_when_copy_fails(tmp_path: Path) -> None:
    installer = (_PLUGIN_ROOT / "scripts" / "install.sh").read_text(encoding="utf-8")
    materialize = _shell_function_source(
        installer,
        "_materialize_snapshot_vendored_libs",
        "\n_materialize_snapshot_installer_engine()",
    )
    script = tmp_path / "materialize.sh"
    snapshot_dir = tmp_path / "snapshot"
    snapshot_dir.mkdir()
    script.write_text(
        "\n".join(
            [
                "set -euo pipefail",
                "_fail() { printf '%s\\n' \"$1\" >&2; }",
                "_resolve_vendored_lib() { printf '%s\\n' \"$TMP_ROOT/$1\"; }",
                "rm() { command rm \"$@\"; }",
                "cp() { [ \"$2\" = \"$TMP_ROOT/ssh-manager\" ] && return 1; command cp \"$@\"; }",
                materialize,
                f"TMP_ROOT='{tmp_path}'",
                "mkdir -p \"$TMP_ROOT/agent-procutil\" \"$TMP_ROOT/ssh-manager\" \"$TMP_ROOT/venue-copilot\" \"$TMP_ROOT/zdd\" \"$TMP_ROOT/remote-login-shell\"",
                "touch \"$TMP_ROOT/agent-procutil/pyproject.toml\" \"$TMP_ROOT/ssh-manager/pyproject.toml\" \"$TMP_ROOT/venue-copilot/pyproject.toml\" \"$TMP_ROOT/zdd/pyproject.toml\" \"$TMP_ROOT/remote-login-shell/pyproject.toml\"",
                f"_materialize_snapshot_vendored_libs '{snapshot_dir}'",
            ]
        ),
        encoding="utf-8",
    )
    proc = subprocess.run(
        ["bash", str(script)],
        capture_output=True,
        text=True,
        timeout=_HARNESS_TIMEOUT_SECONDS,
        check=False,
    )
    assert proc.returncode != 0
    assert "Failed to copy required snapshot library: ssh-manager" in proc.stderr


@pytest.mark.skipif(
    os.name == "nt" or shutil.which("bash") is None,
    reason="POSIX stamp publication lock coverage",
)
def test_stamp_publication_lock_no_flock_fails_closed_on_stale_owner(
    tmp_path: Path,
) -> None:
    bash = shutil.which("bash")
    assert bash is not None
    installer = (_PLUGIN_ROOT / "scripts" / "install.sh").read_text(encoding="utf-8")
    acquire = _shell_function_source(installer, "_acquire_stamp_publication_lock", "\n_release_stamp_publication_lock()")
    install_dir = tmp_path / "runtime"
    install_dir.mkdir()
    script = tmp_path / "worker.sh"
    script.write_text(
        "\n".join(
            [
                "set -euo pipefail",
                "_fail() { printf '%s\\n' \"$1\" >&2; }",
                acquire,
                f"INSTALL_DIR='{install_dir}'",
                "export COPILOT_EXT_NO_FLOCK=1",
                "_acquire_stamp_publication_lock",
            ]
        ),
        encoding="utf-8",
    )
    script.chmod(0o755)
    stale_dir = install_dir / ".stamp-publication.lock.d"
    stale_dir.mkdir()
    (stale_dir / "owner").write_text("999999", encoding="utf-8")
    proc = subprocess.run(
        [bash, str(script)],
        capture_output=True,
        text=True,
        timeout=_HARNESS_TIMEOUT_SECONDS,
        check=False,
    )
    assert proc.returncode != 0
    assert "refusing unsafe no-flock recovery" in proc.stderr
    assert stale_dir.exists()


@pytest.mark.skipif(
    os.name == "nt" or shutil.which("bash") is None,
    reason="POSIX stamp publication lock coverage",
)
def test_stamp_publication_lock_no_flock_fails_closed_without_owner_file(
    tmp_path: Path,
) -> None:
    bash = shutil.which("bash")
    assert bash is not None
    installer = (_PLUGIN_ROOT / "scripts" / "install.sh").read_text(encoding="utf-8")
    acquire = _shell_function_source(installer, "_acquire_stamp_publication_lock", "\n_release_stamp_publication_lock()")
    install_dir = tmp_path / "runtime"
    install_dir.mkdir()
    script = tmp_path / "worker.sh"
    script.write_text(
        "\n".join(
            [
                "set -euo pipefail",
                "_fail() { printf '%s\\n' \"$1\" >&2; }",
                acquire,
                f"INSTALL_DIR='{install_dir}'",
                "export COPILOT_EXT_NO_FLOCK=1",
                "_acquire_stamp_publication_lock",
            ]
        ),
        encoding="utf-8",
    )
    script.chmod(0o755)
    missing_owner_dir = install_dir / ".stamp-publication.lock.d"
    missing_owner_dir.mkdir()
    proc = subprocess.run(
        [bash, str(script)],
        capture_output=True,
        text=True,
        timeout=_HARNESS_TIMEOUT_SECONDS,
        check=False,
    )
    assert proc.returncode != 0
    assert "exists without an owner file" in proc.stderr
    assert missing_owner_dir.exists()


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv is required")
def test_stamp_supports_first_use_provision_from_snapshot_only_ps1(tmp_path: Path) -> None:
    pwsh = shutil.which("pwsh") or shutil.which("powershell")
    if not pwsh:
        pytest.skip("PowerShell is unavailable")

    payload = _stage_payload(tmp_path)
    home = tmp_path / "home"
    env = _isolated_install_env(home)
    internal_index = _host_pip_index_url()
    if internal_index and not (env.get("UV_DEFAULT_INDEX") or env.get("UV_INDEX_URL")):
        env["UV_DEFAULT_INDEX"] = internal_index

    if os.name != "nt":
        system_root = home / "systemroot"
        where_exe = system_root / "System32" / "where.exe"
        powershell_exe = system_root / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
        where_exe.parent.mkdir(parents=True, exist_ok=True)
        powershell_exe.parent.mkdir(parents=True, exist_ok=True)
        where_exe.write_text(
            "#!/usr/bin/env sh\n"
            "if [ \"$1\" = pwsh ]; then\n"
            f"  printf '%s\\n' '{pwsh}'\n"
            "fi\n",
            encoding="utf-8",
        )
        where_exe.chmod(0o755)
        powershell_exe.write_text(
            "#!/usr/bin/env sh\n"
            f"exec '{pwsh}' \"$@\"\n",
            encoding="utf-8",
        )
        powershell_exe.chmod(0o755)
        env["SystemRoot"] = str(system_root)

    stamp = subprocess.run(
        [
            pwsh,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(payload / "scripts" / "install.ps1"),
            "stamp",
            "-InstallDir",
            str(home / ".agent-ssh"),
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert stamp.returncode == 0, stamp.stderr

    snapshot = Path((home / ".agent-ssh" / "payload-dir").read_text(encoding="utf-8").strip())
    assert (snapshot / "scripts" / "installer-engine.sh").is_file()
    assert (snapshot / "scripts" / "installer-engine.ps1").is_file()
    for lib in ("agent-procutil", "ssh-manager", "venue-copilot", "zdd", "remote-login-shell"):
        assert (snapshot / "libs" / lib / "pyproject.toml").is_file()

    shutil.rmtree(payload)
    shutil.rmtree(tmp_path / "libs")

    invoke = subprocess.run(
        [
            pwsh,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(home / ".local" / "bin" / "agent-ssh.ps1"),
            "version",
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=420,
        check=False,
    )
    assert invoke.returncode == 0, invoke.stderr
    assert f"agent-ssh {_expected_version()}" in invoke.stdout
