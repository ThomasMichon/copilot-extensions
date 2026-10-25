"""Scoped Dispatch stamps must not acquire the legacy command namespace."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from test_adopter_publication import _adopter, _command
from test_bridge_first_use_publication import _initialize_shell_profiles
from test_command_binding import (
    POWERSHELL, _snapshot,
    binding_fixture as binding_fixture,
)
from test_dispatch_first_use_publication import (
    _selected_cell, dispatch_release as dispatch_release,
)
from test_installer_publication import _policy
from test_native_publication import _completed_slot, _run

pytestmark = [
    pytest.mark.portfolio_tier("T3"),
    pytest.mark.timeout(60),
]

SHELLS = [
    pytest.param("bash", marks=pytest.mark.skipif(os.name == "nt", reason="Native POSIX installer")),
    pytest.param("powershell", marks=pytest.mark.skipif(
        POWERSHELL is None, reason="PowerShell is unavailable",
    )),
]


def _environment(fixture: dict, dispatch_release: Path) -> dict[str, str]:
    environment = _adopter(fixture, "agent-dispatch", dispatch_release)
    environment.pop("COPILOT_PLUGIN_INSTALL_SMOKE", None)
    environment["AGENT_DISPATCH_SUPERVISE_MACHINE"] = "fixture"
    if os.name == "nt":
        environment.pop("PSModulePath", None)
        environment["LOCALAPPDATA"] = str(fixture["home"] / "AppData/Local")
        environment["APPDATA"] = str(fixture["home"] / "AppData/Roaming")
    return environment


def _stamp(
    fixture: dict, environment: dict[str, str], shell: str, root: Path | str,
) -> subprocess.CompletedProcess:
    command = [*_command(fixture, shell, "install"), "stamp"]
    command += ["-InstallDir" if shell == "powershell" else "--install-dir", str(root)]
    result = _run(fixture, environment, command)
    assert result.returncode == 0, result.stdout + result.stderr
    return result


def _global_paths(fixture: dict, shell: str) -> list[Path]:
    names = (
        ("agent-dispatch.ps1", "agent-dispatch.cmd",
         "agent-dispatch-board.ps1", "agent-dispatch-board.cmd")
        if shell == "powershell" else ("agent-dispatch", "agent-dispatch-board")
    )
    return [fixture["home"] / ".local/bin" / name for name in names]


@pytest.mark.parametrize("scope", ["custom", "cell"])
@pytest.mark.parametrize("existing_global", [False, True])
def test_dispatch_scoped_stamp_preserves_global_ownership(
    binding_fixture: dict, dispatch_release: Path, scope: str, existing_global: bool,
) -> None:
    fixture = binding_fixture
    if os.name == "nt" and POWERSHELL is None:
        pytest.skip("Native PowerShell is required")
    environment = _environment(fixture, dispatch_release)
    shell_profiles = _initialize_shell_profiles(fixture, environment)
    shell = "powershell" if os.name == "nt" else "bash"
    global_paths = _global_paths(fixture, shell)
    if existing_global:
        _stamp(fixture, environment, shell, fixture["legacy"])
    before = {path: path.read_bytes() if path.exists() else None for path in global_paths}
    assert all(value is not None for value in before.values()) is existing_global

    if scope == "cell":
        root, arguments = _selected_cell(fixture, environment)
    else:
        root = fixture["home"] / "custom runtime"
        root.mkdir()
        arguments = {**fixture["arguments"], "legacy_root": root}
        environment["AGENT_DISPATCH_INSTALL_DIR"] = str(root)
    _policy(fixture, root, arguments, "standalone")
    stamped = _stamp(fixture, environment, shell, root)
    assert {path: path.read_bytes() if path.exists() else None for path in global_paths} == before
    assert "Scoped install keeps the global compatibility binstubs unchanged" in stamped.stdout
    assert (root / "payload-dir").is_file()
    for name in ("resolve-runtime.ps1", "resolve-runtime.sh", "runtime-gate.ps1", "runtime-gate.sh"):
        assert (root / "bin" / name).is_file()

    for runtime, marker in ((fixture["legacy"], "LEGACY"), (root, "SCOPED")):
        python = _completed_slot({**fixture, "legacy": runtime})
        site = python.parent.parent / (
            "Lib/site-packages" if os.name == "nt" else
            f"lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages"
        )
        package = site / "agent_dispatch"
        package.mkdir()
        (package / "__init__.py").write_text("", encoding="utf-8")
        for name, entry in (("__main__.py", "main"), ("board_cli.py", "board")):
            (package / name).write_text(
                "import json, os, sys\n"
                f"print(json.dumps({{'marker':{marker!r},'entry':{entry!r},"
                "'root':os.environ['AGENT_DISPATCH_INSTALL_DIR'],"
                "'arguments':sys.argv[1:]}))\n",
                encoding="utf-8",
            )
    suffix = ".cmd" if os.name == "nt" else ""
    payload_entry = fixture["payload"] / "bin" / f"agent-dispatch{suffix}"
    selected_command = (
        [environment["COMSPEC"], "/d", "/s", "/c", str(payload_entry), "version"]
        if os.name == "nt" else [str(payload_entry), "version"]
    )
    selected = _run(fixture, environment, selected_command)
    assert selected.returncode == 0, selected.stdout + selected.stderr
    assert json.loads(selected.stdout.splitlines()[-1]) == {
        "marker": "SCOPED", "entry": "main", "root": str(root), "arguments": ["version"],
    }
    ordinary = environment.copy()
    for name in (
        "COPILOT_EXTENSIONS_CONTEXT", "COPILOT_PLUGIN_ROOT", "AGENT_DISPATCH_INSTALL_DIR",
        "AGENT_DISPATCH_PAYLOAD_ROOT", "AGENT_DISPATCH_INSTALLATION_ID",
    ):
        ordinary.pop(name, None)
    home_before = _snapshot(fixture["home"])
    if existing_global:
        for path in global_paths:
            command = (
                [ordinary["COMSPEC"], "/d", "/s", "/c", str(path), "version"]
                if path.suffix == ".cmd" else
                [POWERSHELL, "-NoProfile", "-File", str(path), "version"]
                if path.suffix == ".ps1" else [str(path), "version"]
            )
            result = _run(fixture, ordinary, command)
            assert result.returncode == 0, result.stdout + result.stderr
            assert json.loads(result.stdout.splitlines()[-1]) == {
                "marker": "SCOPED" if scope == "cell" else "LEGACY",
                "entry": "board" if "board" in path.name else "main",
                "root": str(root if scope == "cell" else fixture["legacy"]),
                "arguments": ["version"],
            }
    home_after = _snapshot(fixture["home"])
    changed = {
        name for name in home_before.keys() | home_after.keys()
        if name not in shell_profiles and home_before.get(name) != home_after.get(name)
    }
    invoked_root = root if scope == "cell" else fixture["legacy"]
    prefix = invoked_root.relative_to(fixture["home"]).as_posix()
    assert changed <= {prefix, f"{prefix}/logs", f"{prefix}/logs/boot-trace.jsonl"}
    assert {path: path.read_bytes() if path.exists() else None for path in global_paths} == before


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("spelling", ["trailing", "dot"])
def test_dispatch_legacy_root_spellings_publish_globals(
    binding_fixture: dict, dispatch_release: Path, shell: str, spelling: str,
) -> None:
    fixture = binding_fixture
    environment = _environment(fixture, dispatch_release)
    root = str(fixture["legacy"]) + os.sep + ("." if spelling == "dot" else "")
    stamped = _stamp(fixture, environment, shell, root)
    assert "Scoped install keeps the global compatibility binstubs unchanged" not in stamped.stdout
    assert all(path.is_file() for path in _global_paths(fixture, shell)), stamped.stdout


@pytest.mark.skipif(os.name == "nt", reason="Distinct case-sensitive POSIX directories")
@pytest.mark.parametrize("shell", SHELLS)
def test_dispatch_case_distinct_root_preserves_globals(
    binding_fixture: dict, dispatch_release: Path, shell: str,
) -> None:
    fixture = binding_fixture
    environment = _environment(fixture, dispatch_release)
    _stamp(fixture, environment, shell, fixture["legacy"])
    globals_before = {path: path.read_bytes() for path in _global_paths(fixture, shell)}
    root = fixture["home"] / ".AGENT-DISPATCH"
    root.mkdir()
    assert not root.samefile(fixture["legacy"])
    stamped = _stamp(fixture, environment, shell, root)
    assert {path: path.read_bytes() for path in globals_before} == globals_before
    assert "Scoped install keeps the global compatibility binstubs unchanged" in stamped.stdout
    assert (root / "payload-dir").is_file()
