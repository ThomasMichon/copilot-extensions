"""Real construction and native helper callbacks retain outer cell management."""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from test_command_binding import ROOT, _snapshot, _write, binding_fixture as binding_fixture
from test_installer_publication import _cell, _policy, backend as backend

pytestmark = [
    pytest.mark.portfolio_tier("T3"),
    pytest.mark.effect("filesystem"),
    pytest.mark.effect("process"),
    pytest.mark.effect("network"),
]


@pytest.mark.timeout(280)
@pytest.mark.parametrize("shell", [
    pytest.param("pwsh", marks=pytest.mark.skipif(shutil.which("pwsh") is None, reason="No pwsh")),
    pytest.param("powershell.exe", marks=pytest.mark.skipif(
        os.name != "nt" or shutil.which("powershell.exe") is None, reason="No Windows PowerShell",
    )),
    "bash",
])
def test_real_managed_cell_repair_and_callbacks(
    backend, tmp_path: Path, tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch, shell: str,
) -> None:
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("Real construction requires uv")
    git = shutil.which("git")
    assert git is not None
    executable = shutil.which(shell)
    if shell == "bash" and os.name == "nt":
        candidate = Path(git).resolve().parent.parent / "bin/bash.exe"
        executable = str(candidate) if candidate.is_file() else None
    if executable is None:
        pytest.skip(f"Native shell unavailable: {shell}")
    source = tmp_path / "source"
    frozen = backend.engine.freeze_inputs(ROOT / "plugins/agent-worktrees", source)
    plugin = frozen.plugin
    _write(source / ".plugin/marketplace.json", {
        "name": "Fixture", "plugins": [{"name": "agent-worktrees", "source": "plugins/agent-worktrees"}],
    })
    # Cell/version paths must leave room for native dependency script names.
    home = tmp_path_factory.mktemp("c") / "h-\u5b89"
    home.mkdir()
    if os.name != "nt":
        # Only OS-profile lookup is isolated. Policy, custody and publication are real.
        helper = plugin / "scripts/installation-context/_installation_context_resolution.py"
        with helper.open("a", encoding="utf-8") as stream:
            stream.write(
                "\n_fixture_current_environment = _current_environment\n"
                "def _current_environment(**arguments):\n"
                "    return _fixture_current_environment(**{**arguments, 'os_profile': Path(os.environ['HOME'])})\n"
            )
        native = plugin / "scripts/installation-context/installation-context.ps1"
        native.write_text(native.read_text(encoding="utf-8").replace(
            "$passwdHome = [CePosixAccount]::Home()", "$passwdHome = $env:HOME",
        ), encoding="utf-8")
    foreign = tmp_path / "foreign-repository"
    foreign.mkdir()
    clean_git_environment = {
        key: value for key, value in os.environ.items() if not key.startswith("GIT_")
    }
    subprocess.run(
        [git, "-C", str(foreign), "init", "-q"], env=clean_git_environment,
        check=True, capture_output=True, timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    foreign_before = _snapshot(foreign)
    for key, value in {
        "GIT_DIR": str(foreign / ".git"), "GIT_WORK_TREE": str(foreign),
        "GIT_INDEX_FILE": str(foreign / "foreign-index"),
        "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.bare", "GIT_CONFIG_VALUE_0": "true",
    }.items():
        monkeypatch.setenv(key, value)
    environment = {
        **{key: value for key, value in os.environ.items() if not key.startswith("GIT_")},
        "HOME": str(home), "USERPROFILE": str(home), "AGENT_HOME": str(home),
        "PSModuleAnalysisCachePath": os.devnull, "PYTHONDONTWRITEBYTECODE": "1",
    }
    assert not any(key.startswith("GIT_") for key in environment)
    for key in (
        "COPILOT_EXTENSIONS_CONTEXT", "COPILOT_EXTENSIONS_COMMAND_LIFECYCLE",
        "COPILOT_EXTENSIONS_RUNTIME_PUBLICATION", "COPILOT_PLUGIN_ROOT",
        "COPILOT_PLUGIN_STAGED_FROM", "COPILOT_PLUGIN_INSTALL_STAGED",
        "COPILOT_PLUGIN_INSTALL_SMOKE", "AGENT_WORKTREES_PAYLOAD_ROOT",
        "AGENT_WORKTREES_RUNTIME_INPUTS",
    ):
        environment.pop(key, None)
    if os.name != "nt":
        directory = home / "native-bin"
        directory.mkdir()
        getent = directory / "getent"
        getent.write_text(
            '#!/bin/sh\nprintf "fixture:x:%s:%s:fixture:%s:/bin/sh\\n" "$(id -u)" "$(id -g)" "$HOME"\n',
            encoding="utf-8",
        )
        getent.chmod(0o755)
        environment["PATH"] = str(directory) + os.pathsep + environment["PATH"]
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    spec = importlib.util.spec_from_file_location(
        "_managed_cell_fixture", plugin / "scripts/installation-context/installation_context.py",
    )
    assert spec is not None and spec.loader is not None
    primitive = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, primitive)
    spec.loader.exec_module(primitive)
    current = primitive._current_environment
    monkeypatch.setattr(primitive, "_current_environment", lambda **kw: current(
        **{**kw, "os_profile": home},
    ))
    if primitive._is_wsl_guest():
        environment["WSL_DISTRO_NAME"] = "Fixture Guest"
        primitive.register_guest_environment(
            "Fixture Guest", environment=environment, os_profile=home,
        )
    fixture = {
        "payload": plugin, "home": home, "legacy": home / ".agent-worktrees",
        "primitive": primitive, "environment": environment,
        "arguments": {
            "payload_root": plugin, "plugin_id": "agent-worktrees",
            "legacy_root": home / ".agent-worktrees", "environment": environment,
        },
    }
    # The source gets ordinary attributable Git metadata without inheriting hooks or filters.
    options = [
        git, "-C", str(source), "-c", "core.hooksPath=" + str(tmp_path / "empty-hooks"),
        "-c", "core.autocrlf=false", "-c", "commit.gpgsign=false",
        "-c", "user.name=Runtime fixture", "-c", "user.email=fixture@example.invalid",
    ]
    environment.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
    for arguments in (["init", "-q"], ["add", "--all"], ["commit", "-qm", "Frozen runtime fixture"]):
        subprocess.run(
            [*options, *arguments], check=True, capture_output=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), env=environment, timeout=30,
        )
    root, arguments = _cell(fixture, environment)
    _policy(fixture, root, arguments, "managed")
    # Lease and native child load independent copies of the canonical primitive.
    arguments = {**arguments, "environment": fixture["environment"]}
    version = tomllib.loads((plugin / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    broken = root / "versions" / version
    broken.mkdir(parents=True)
    (broken / ".install-complete.json").write_text("broken predecessor", encoding="utf-8")
    (root / "current-version").write_text(version, encoding="utf-8")
    (root / "last-known-good").write_text(version, encoding="utf-8")
    if shell == "bash" and os.name == "nt":
        command = [
            executable, "-c",
            'exec bash "$1" repair --install-dir "$(cygpath -u "$2")" '
            '--runtime-publication-python "$(cygpath -u "$3")"',
            "--", (plugin / "scripts/install.sh").as_posix(), str(root), sys.executable,
        ]
    elif shell == "bash":
        command = [
            executable, str(plugin / "scripts/install.sh"), "repair",
            "--install-dir", str(root), "--runtime-publication-python", sys.executable,
        ]
    else:
        command = [
            executable, "-NoProfile", "-NonInteractive", "-File", str(plugin / "scripts/install.ps1"),
            "repair", "-InstallDir", str(root), "-BootstrapPython", sys.executable,
        ]
    with primitive.runtime_publication_lease(**arguments) as patch:
        result = subprocess.run(
            command, env={**environment, **patch}, cwd=fixture["home"], capture_output=True,
            text=True, encoding="utf-8", timeout=240,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        assert result.returncode == 0, result.stdout + result.stderr
        selected = (root / "current-version").read_text().strip()
        assert selected.startswith(version + "+repair.")
        manifest = json.loads((root / "deploy-manifest.json").read_text(encoding="utf-8"))
        assert manifest["runtime_slot"] == selected
        assert manifest["runtime_generation"]
        slot = root / "versions" / selected
        completion = json.loads((slot / ".install-complete.json").read_text(encoding="utf-8"))
        assert completion["payload_hash"]
        assert (root / "bin/resolve-runtime.ps1").is_file()
        assert (root / "bin/resolve-runtime.sh").is_file()
        assert (broken / ".install-complete.json").read_text() == "broken predecessor"
        assert not (fixture["home"] / ".local/bin/agent-worktrees").exists()
        borrowed = primitive.borrow_runtime_publication(**{
            **arguments, "environment": {**fixture["environment"], **patch},
        })
        assert all(lock._owner()["pid"] == os.getpid() for lock in borrowed)
        assert not list((root / ".installer-inputs").glob("helper-publication-*"))
    assert _snapshot(foreign) == foreign_before
