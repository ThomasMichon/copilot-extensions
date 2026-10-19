from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from stamp_fixture import no_shared_write_umask  # noqa: F401


PLUGIN = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(os.name == "nt", reason="POSIX executable-mode contract")
def test_identical_stamp_repairs_launcher_without_republishing_metadata(tmp_path: Path) -> None:
    import importlib.util
    import stat

    spec = importlib.util.spec_from_file_location("session_stamp", PLUGIN / "scripts/session-stamp.py")
    assert spec and spec.loader
    stamp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(stamp)
    launcher = tmp_path / "agent-index"
    data = b"#!/bin/sh\nprintf callable\n"
    stamp.publish(launcher, data, executable=True)
    launcher.chmod(0o644)
    metadata = tmp_path / "stamped-version"
    stamp.publish(metadata, b"9.9.9\n")
    metadata.chmod(0o640)
    before = metadata.stat()
    stamp.publish(launcher, data, executable=True)
    stamp.publish(metadata, b"9.9.9\n")
    assert subprocess.run([str(launcher)], capture_output=True, check=True).stdout == b"callable"
    assert stat.S_IMODE(launcher.stat().st_mode) == 0o755
    after = metadata.stat()
    assert (after.st_mode, after.st_ino, after.st_mtime_ns) == (
        before.st_mode, before.st_ino, before.st_mtime_ns,
    )
    link = tmp_path / "linked-launcher"
    link.symlink_to(launcher)
    with pytest.raises(ValueError, match="linked stamp"):
        stamp.publish(link, data, executable=True)


def test_session_entrypoints_never_invoke_installers_or_service_ensure() -> None:
    bootstrap_sh = (PLUGIN / "scripts" / "bootstrap-check.sh").read_text(
        encoding="utf-8"
    )
    bootstrap_ps = (PLUGIN / "scripts" / "bootstrap-check.ps1").read_text(
        encoding="utf-8"
    )
    ensure_sh = (PLUGIN / "scripts" / "ensure-service.sh").read_text(
        encoding="utf-8"
    )
    ensure_ps = (PLUGIN / "scripts" / "ensure-service.ps1").read_text(
        encoding="utf-8"
    )

    for source in (ensure_sh, ensure_ps):
        assert source.rstrip().endswith("exit 0")
        assert "runtime-gate" not in source
        assert "install." not in source
    for source in (bootstrap_sh, bootstrap_ps):
        assert "__session-stamp" in source
        assert "install." not in source


@pytest.mark.parametrize("shell", ["bash", "powershell"])
def test_bootstrap_check_emits_exact_empty_object(
    tmp_path: Path, shell: str
) -> None:
    suffix = "sh" if shell == "bash" else "ps1"
    result = subprocess.run(
        _shell_command(shell, PLUGIN / "scripts" / f"bootstrap-check.{suffix}"),
        env={
            **os.environ,
            "HOME": str(tmp_path),
            "USERPROFILE": str(tmp_path),
            "BOOTSTRAP_KILLSWITCH_STATE_FILE": str(tmp_path / "bootstrap-killswitch.json"),
            "PYTHONUTF8": "1",
        },
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "{}"


@pytest.mark.parametrize("shell", ["bash", "powershell"])
@pytest.mark.parametrize("role", [None, "corpus", "client", "host"])
def test_actual_session_hook_stamps_only_metadata(
    tmp_path: Path, shell: str, role: str | None,
) -> None:
    import json
    from test_runtime_gate import _fixture as gate_fixture

    gate, environment = gate_fixture(tmp_path / "fixture", shell)
    scripts = gate.parent
    payload = scripts.parent
    for name in ("session-stamp.py", "stamp_paths.py", "stamp_repair.py", "versioned_runtime.py",
                 "versioned_runtime_leases.py", "versioned_runtime_construction.py",
                 "versioned_runtime_source.py",
                 "bootstrap-killswitch-guard.sh", "bootstrap-killswitch-guard.ps1"):
        shutil.copy2(PLUGIN / "scripts" / name, scripts / name)
    (payload / "plugin.json").write_text('{"name":"agent-index","version":"9.9.9"}')
    suffix = "sh" if shell == "bash" else "ps1"
    shutil.copy2(PLUGIN / "scripts" / f"bootstrap-check.{suffix}", scripts)
    repo = tmp_path / "fixture"
    config = repo / ".agent-index/config.yaml"
    if role is None:
        config.unlink()
    elif role != "corpus":
        machine = "test-machine" if role == "host" else "remote-machine"
        config.write_text(f"indexer:\n  machine: {machine}\n")
    environment["AGENT_INDEX_MACHINE"] = "test-machine"
    environment["AGENT_INDEX_REPO"] = str(repo)
    environment["HOME"] = environment["USERPROFILE"] = str(tmp_path / "home")
    environment["BOOTSTRAP_KILLSWITCH_STATE_FILE"] = str(
        tmp_path / "home/.copilot-extensions/bootstrap-killswitch.json"
    )
    Path(environment["HOME"]).mkdir()
    environment.pop("TEST_PYTHON", None)
    environment["COPILOT_PLUGIN_ROOT"] = str(payload)
    environment["AGENT_INDEX_PAYLOAD_ROOT"] = str(payload)
    forbidden = tmp_path / "forbidden-tools"
    forbidden.mkdir()
    marker = tmp_path / "forbidden-tool-called"
    for name in ("uv", "pip", "pip3", "systemctl", "schtasks", "schtasks.exe"):
        tool = forbidden / name
        tool.write_text(f"#!/bin/sh\nprintf called > '{marker}'\nexit 99\n")
        tool.chmod(0o755)
    environment["PATH"] = str(forbidden) + os.pathsep + environment["PATH"]
    # The real mode boundary executes a fixture-only read adapter. No production
    # profile, service query, package tool or installer is available to it.
    for name in ("install.sh", "install.ps1", "ensure-service.sh", "ensure-service.ps1"):
        (scripts / name).write_text("FORBIDDEN INSTALLER/SERVICE INVOCATION")
    hook = json.loads((PLUGIN / "hooks.json").read_text())["hooks"]["sessionStart"][1]
    executable = _shell_command(shell, scripts / f"bootstrap-check.{suffix}")[0]
    command = ([executable, "-c", hook["bash"]] if shell == "bash" else
               [executable, "-NoProfile", "-Command", hook["powershell"]])
    result = subprocess.run(command, cwd=repo, env=environment,
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "{}"
    assert not marker.exists()
    root = Path(environment["AGENT_INDEX_HOME"])
    if role is None:
        assert not root.exists()
    else:
        expected = {"payload-dir", "stamped-version", ".publication.lock"}
        assert set(p.name for p in root.iterdir()) == expected
        assert (root / "stamped-version").read_text().strip() == "9.9.9"
        assert not list(root.rglob("pyvenv.cfg"))
        assert not list(root.rglob("deploy-manifest.json"))
    assert not (Path(environment["HOME"]) / ".config/systemd").exists()
    assert not (Path(environment["HOME"]) / ".cache/huggingface").exists()


def _shell_command(shell: str, script: Path) -> list[str]:
    if shell == "bash":
        if os.name == "nt":
            pytest.skip("POSIX ensure-service test")
        executable = shutil.which("bash")
        if executable is None:
            pytest.skip("bash is unavailable")
        return [executable, str(script)]
    executable = shutil.which("powershell.exe") or shutil.which("pwsh")
    if executable is None:
        pytest.skip("PowerShell is unavailable")
    return [
        executable,
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script),
    ]


def _fixture(
    tmp_path: Path,
    shell: str,
    role: str | None,
) -> tuple[Path, Path, dict[str, str]]:
    payload = tmp_path / f"payload-{shell}"
    scripts = payload / "scripts"
    scripts.mkdir(parents=True)
    suffix = "ps1" if shell == "powershell" else "sh"
    ensure = scripts / f"ensure-service.{suffix}"
    shutil.copy2(PLUGIN / "scripts" / ensure.name, ensure)
    marker = tmp_path / f"{shell}-runtime-gate-called"
    runtime_gate = scripts / f"runtime-gate.{suffix}"
    if shell == "powershell":
        runtime_gate.write_text(
            "[IO.File]::WriteAllText($env:HOOK_MARKER, 'called')\nexit 0\n",
            encoding="utf-8-sig",
        )
    else:
        runtime_gate.write_text(
            "#!/usr/bin/env bash\nprintf called > \"$HOOK_MARKER\"\nexit 0\n",
            encoding="utf-8",
            newline="\n",
        )
    (scripts / "resolve-activation-role.py").write_text(
        "from pathlib import Path\n"
        "import sys\n"
        "path = Path(sys.argv[sys.argv.index('--config') + 1])\n"
        "print(path.read_text(encoding='utf-8').strip())\n",
        encoding="utf-8",
    )
    repo = tmp_path / f"repo-{shell}"
    repo.mkdir()
    subprocess.run(
        ["git", "init", "--quiet", str(repo)],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if role is not None:
        config = repo / ".agent-index" / "config.yaml"
        config.parent.mkdir(parents=True)
        config.write_text(role + "\n", encoding="utf-8")
    profile = tmp_path / f"profile-{shell}"
    profile.mkdir()
    environment = {
        **os.environ,
        "HOME": str(profile),
        "USERPROFILE": str(profile),
        "AGENT_INDEX_MACHINE": "test-machine",
        "HOOK_MARKER": str(marker),
        "PYTHONUTF8": "1",
    }
    environment.pop("COPILOT_EXTENSIONS_CONTEXT", None)
    environment.pop("AGENT_INDEX_ROLE", None)
    return ensure, repo, environment


@pytest.mark.parametrize("shell", ["bash", "powershell"])
@pytest.mark.parametrize("role", [None, "client"])
def test_repo_admission_precedes_namespaced_service_ensure(
    tmp_path: Path,
    shell: str,
    role: str | None,
) -> None:
    script, repo, environment = _fixture(tmp_path, shell, role)

    result = subprocess.run(
        _shell_command(shell, script),
        cwd=repo,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=20,
    )

    assert result.returncode == 0, result.stderr
    assert not Path(environment["HOOK_MARKER"]).exists()


@pytest.mark.parametrize("shell", ["bash", "powershell"])
def test_designated_host_session_never_kicks_service_ensure(
    tmp_path: Path,
    shell: str,
) -> None:
    script, repo, environment = _fixture(tmp_path, shell, "host")

    result = subprocess.run(
        _shell_command(shell, script),
        cwd=repo,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=20,
    )

    assert result.returncode == 0, result.stderr
    assert not Path(environment["HOOK_MARKER"]).exists()
