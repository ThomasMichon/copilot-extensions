from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tools import plugin_test_containment
from tools.plugin_test_containment import Limits, isolated_environment


def test_tree_size_counts_nested_files_without_repeated_path_stats(tmp_path, monkeypatch):
    (tmp_path / "nested").mkdir()
    (tmp_path / "a").write_bytes(b"abc")
    (tmp_path / "nested" / ".hidden").write_bytes(b"12345")

    def repeated_walk(*args, **kwargs):
        raise AssertionError("The monitor must retain scandir's cached file metadata")

    monkeypatch.setattr(Path, "rglob", repeated_walk)
    assert plugin_test_containment._tree_size(tmp_path) == 8
    assert plugin_test_containment._tree_size(tmp_path / "absent") == 0


def test_tree_size_does_not_follow_file_or_directory_links(tmp_path):
    root = tmp_path / "sandbox"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "data").write_bytes(b"not-counted")
    (root / "data").write_bytes(b"counted")
    try:
        (root / "linked-file").symlink_to(outside / "data")
        (root / "linked-directory").symlink_to(outside, target_is_directory=True)
        (root / "loop").symlink_to(root, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"Symbolic link creation is unavailable: {error}")
    assert plugin_test_containment._tree_size(root) == len(b"counted")


def test_native_temp_budget_still_terminates_the_process_tree(tmp_path, capfd):
    environment = isolated_environment(os.environ, tmp_path)
    code = (
        "from pathlib import Path; import time; "
        "Path('allocation').write_bytes(b'x' * (2 * 1024 * 1024)); "
        "time.sleep(60)"
    )
    result = plugin_test_containment.run_contained(
        [sys.executable, "-c", code], cwd=tmp_path, env=environment,
        sandbox=tmp_path, limits=Limits(
            wall_seconds=10, max_processes=8, max_memory_mb=256, max_temp_mb=1,
        ),
    )
    assert result == 124
    assert "temporary-storage limit exceeded" in capfd.readouterr().err


@pytest.mark.parametrize("module_path", [None, "host-modules"])
@pytest.mark.parametrize(
    ("allow_explicit_tiers", "allow_host_state"),
    [(False, False), (True, False), (True, True)],
)
def test_powershell_module_path_requires_explicit_host_state(
    tmp_path, module_path, allow_explicit_tiers, allow_host_state,
):
    base = {"PATH": "native-applications"}
    if module_path is not None:
        base["PSModulePath"] = module_path
    before = base.copy()
    env = isolated_environment(
        base, tmp_path,
        allow_explicit_tiers=allow_explicit_tiers, allow_host_state=allow_host_state,
    )
    assert base == before
    assert env["PATH"] == before["PATH"]
    if allow_host_state:
        assert env.get("PSModulePath") == module_path
    else:
        assert env["PSModulePath"] == str(tmp_path / "powershell-modules")
        assert Path(env["PSModulePath"]).is_dir()


@pytest.mark.skipif(os.name != "nt", reason="native Windows PowerShell module discovery")
@pytest.mark.parametrize("host", ["powershell.exe", "pwsh"])
@pytest.mark.parametrize("allow_host_state", [False, True])
def test_native_powershell_module_discovery_is_host_detached(tmp_path, host, allow_host_state):
    executable = shutil.which(host)
    if executable is None:
        pytest.skip(f"{host} unavailable")
    host_modules = tmp_path / "host-modules"
    module = host_modules / "ContainmentProbe"
    module.mkdir(parents=True)
    (module / "ContainmentProbe.psd1").write_text(
        "@{ RootModule='ContainmentProbe.psm1'; ModuleVersion='1.0'; "
        "FunctionsToExport=@('Get-ContainmentProbe') }\n", encoding="utf-8",
    )
    (module / "ContainmentProbe.psm1").write_text(
        "function Get-ContainmentProbe { 'host-module' }\n", encoding="utf-8",
    )
    parent_module_path = os.environ.get("PSModulePath")
    base = {**os.environ, "PSModulePath": str(host_modules)}
    sandbox = tmp_path / "sandbox"
    env = isolated_environment(
        base, sandbox, allow_explicit_tiers=allow_host_state, allow_host_state=allow_host_state,
    )
    marker = sandbox / "core-command-marker"
    runner = tmp_path / "modules.ps1"
    runner.write_text(
        "$ErrorActionPreference = 'Stop'\n"
        "$PSModuleAutoLoadingPreference = 'All'\n"
        "Set-Content -LiteralPath $args[0] -Value 'core-commands-available'\n"
        "if (-not (Test-Path -LiteralPath $args[0])) { throw 'core-command-failed' }\n"
        "if (-not (Get-Command $args[1] -CommandType Application)) { throw 'native-app-missing' }\n"
        "$probe = Get-Command Get-ContainmentProbe -ErrorAction SilentlyContinue\n"
        "if ($probe) { & $probe } else { Write-Output 'host-module-unavailable' }\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [executable, "-NoProfile", "-NonInteractive", "-File", str(runner),
         str(marker), Path(executable).name],
        env=env, capture_output=True, text=True, timeout=15,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert marker.read_text().strip() == "core-commands-available"
    assert result.stdout.strip() == (
        "host-module" if allow_host_state else "host-module-unavailable"
    )
    assert env["PSModulePath"] == str(host_modules if allow_host_state else sandbox / "powershell-modules")
    assert base["PSModulePath"] == str(host_modules)
    assert os.environ.get("PSModulePath") == parent_module_path


def test_run_contained_detects_and_fails_on_persistent_environment_drift(
    tmp_path, capfd, monkeypatch
):
    before = plugin_test_containment._WindowsEnvironmentSnapshot(
        user={"Path": ("before", 1)},
        machine={},
    )
    after = plugin_test_containment._WindowsEnvironmentSnapshot(
        user={"Path": ("after", 1)},
        machine={},
    )
    snapshots = iter((before, after))
    monkeypatch.setattr(
        plugin_test_containment,
        "_read_registry_environment",
        lambda: next(snapshots),
    )
    env = isolated_environment(os.environ, tmp_path)

    rc = plugin_test_containment.run_contained(
        [sys.executable, "-c", "raise SystemExit(0)"],
        cwd=tmp_path,
        env=env,
        sandbox=tmp_path,
        limits=Limits(
            wall_seconds=10,
            max_processes=8,
            max_memory_mb=256,
            max_temp_mb=32,
        ),
    )

    assert rc == 125
    assert "detected without rollback: User:Path" in capfd.readouterr().err
