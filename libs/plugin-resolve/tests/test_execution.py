"""Executable and installer lookup retain the actual execution-directory context."""

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from plugin_resolve.execution import (
    BATCH_EXECUTABLE_ENV, execution_environment, execution_executable, windows_batch_command,
)
from plugin_resolve.preparation import prepare
from plugin_resolve.reflection import build_prepare_script, parse_prepared
from plugin_resolve.registration import atomic_json
from test_plugin_preparation import init_checkout


def test_preparation_path_is_anchored_without_mutating_the_productive_environment(tmp_path):
    cwd = tmp_path / "checkout"
    other = tmp_path / "other"
    environment = {"PATH": os.pathsep.join(["bin", "", str(other), "."]), "HOME": "unchanged"}
    original = dict(environment)
    prepared = execution_environment(environment, cwd)
    assert prepared["PATH"].split(os.pathsep) == [str(cwd / "bin"), str(cwd), str(other), str(cwd)]
    assert prepared["HOME"] == "unchanged" and environment == original


def test_missing_optional_command_never_falls_back_to_the_daemon_directory(tmp_path, monkeypatch):
    cwd, daemon = tmp_path / "checkout", tmp_path / "daemon"
    daemon.mkdir()
    name = "copilot.cmd" if os.name == "nt" else "copilot"
    wrong = daemon / name
    wrong.write_text("wrong")
    wrong.chmod(0o700)
    monkeypatch.chdir(daemon)
    environment = {"PATH": "bin", "NoDefaultCurrentDirectoryInExePath": "1"}
    missing = execution_executable("copilot", environment, cwd, required=False)
    assert Path(missing).is_absolute() and Path(missing).parent == cwd / "bin"
    assert not Path(missing).exists() and Path.cwd() == daemon
    with pytest.raises(FileNotFoundError, match="execution checkout"):
        execution_executable("copilot", environment, cwd)


@pytest.mark.skipif(os.name != "nt", reason="Windows quoted PATH entries")
@pytest.mark.parametrize("relative", [False, True])
def test_quoted_path_retains_execution_directory_and_runs_selected_command(tmp_path, relative):
    cwd = tmp_path / "checkout"
    directory = cwd / "space tools"
    directory.mkdir(parents=True)
    executable = directory / "selected.cmd"
    executable.write_text("@echo selected\n")
    entry = os.path.relpath(directory, cwd) if relative else str(directory)
    environment = {**os.environ, "PATH": '"' + entry + '"', "PATHEXT": ".CMD"}
    original = dict(environment)
    prepared = execution_environment(environment, cwd)
    assert prepared["PATH"] == str(directory)
    selected = execution_executable("selected", environment, cwd)
    assert Path(selected) == executable
    result = subprocess.run(
        windows_batch_command([selected], prepared), env=prepared, cwd=cwd,
        capture_output=True, text=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode == 0 and result.stdout.strip() == "selected", result.stderr
    assert environment == original


@pytest.mark.skipif(os.name == "nt", reason="POSIX literal quote characters in PATH")
def test_posix_quoted_directory_name_stays_literal(tmp_path):
    cwd = tmp_path / "checkout"
    entry = '"literal"'
    assert execution_environment({"PATH": entry}, cwd)["PATH"] == str(cwd / entry)


@pytest.mark.skipif(os.name != "nt", reason="Windows PATHEXT lookup")
def test_child_pathext_and_no_default_directory_are_not_taken_from_the_host(tmp_path, monkeypatch):
    cwd, daemon = tmp_path / "checkout", tmp_path / "daemon"
    (cwd / "bin").mkdir(parents=True)
    daemon.mkdir()
    (cwd / "bin" / "tool.cmd").write_text("selected")
    (daemon / "tool.exe").write_text("wrong")
    monkeypatch.chdir(daemon)
    environment = {"PATH": "bin", "PATHEXT": ".CMD;.EXE", "NoDefaultCurrentDirectoryInExePath": "1"}
    assert Path(execution_executable("tool", environment, cwd)) == cwd / "bin" / "tool.cmd"
    explicit = cwd / "bin" / "tool.exe"
    explicit.write_text("explicit")
    assert Path(execution_executable("tool.exe", {**environment, "PATHEXT": ".CMD"}, cwd)) == explicit
    with pytest.raises(ValueError, match="PATHEXT"):
        execution_executable("tool", {**environment, "PATHEXT": ";;"}, cwd)


def test_batch_transport_rejects_invalid_paths_before_mutating_child_environment():
    environment = {"COMSPEC": "cmd.exe"}
    with pytest.raises(ValueError, match="invalid characters"):
        windows_batch_command(['bad"path.cmd'], environment)
    assert environment == {"COMSPEC": "cmd.exe"}


@pytest.mark.skipif(os.name != "nt", reason="Windows batch transport")
@pytest.mark.parametrize("folder", ["space folder", "R&D", "%CPE_PROBE%", "bang!"])
def test_batch_executable_path_is_literal_data_not_cmd_syntax(tmp_path, folder):
    directory = tmp_path / folder
    directory.mkdir()
    executable = directory / "probe.cmd"
    executable.write_text("@echo execution\n")
    environment = {**os.environ, "CPE_PROBE": "missing"}
    command = windows_batch_command([str(executable)], environment)
    result = subprocess.run(
        command, cwd=tmp_path, env=environment, capture_output=True, text=True, timeout=10,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode == 0 and result.stdout.strip() == "execution", result.stderr
    assert environment[BATCH_EXECUTABLE_ENV] == '"' + str(executable) + '"'


@pytest.mark.parametrize("emitted", [False, True])
@pytest.mark.parametrize("quoted", [
    False, pytest.param(True, marks=pytest.mark.skipif(os.name != "nt", reason="Windows PATH quotes")),
])
def test_default_installer_uses_original_execution_cwd_before_neutral_installation(
    tmp_path, monkeypatch, emitted, quoted,
):
    repo, daemon, home = (tmp_path / name for name in ("checkout", "daemon", "home"))
    init_checkout(repo)
    execution = repo / "subdirectory"
    tools = execution / "bin"
    tools.mkdir(parents=True)
    daemon.mkdir()
    home.mkdir()
    fixture = Path(__file__).parent / "fixtures" / "venue_installer.py"
    installer = tools / ("selected-installer.cmd" if os.name == "nt" else "selected-installer")
    installer.write_text(
        f'@"{sys.executable}" "{fixture}" %*\n' if os.name == "nt" else
        f"#!/bin/sh\nexec {shlex.quote(sys.executable)} {shlex.quote(str(fixture))} \"$@\"\n",
    )
    installer.chmod(0o700)
    preferences = home / ".copilot" / "settings.json"
    preferences.parent.mkdir()
    atomic_json(preferences, {"enabledPlugins": {"unrelated@catalog": True}})
    before = preferences.read_bytes()
    calls = tmp_path / "installer-calls.jsonl"
    environment = {
        **os.environ, "HOME": str(home), "USERPROFILE": str(home),
        "COPILOT_HOME": str(preferences.parent), "VENUE_CALL_LOG": str(calls),
        "COPILOT_PATH": installer.name if quoted else os.path.relpath(installer, execution),
        "PATH": os.pathsep.join(['"bin"' if quoted else "bin", os.environ.get("PATH", "")]),
    }
    request = {
        "repo": str(execution),
        "plugins": [{
            "source": "sample@remote", "enable": True,
            "marketplace": {"source": {"source": "github", "repo": "example/synthetic"}},
        }],
    }
    # A regression must fail before it can invoke the host's real Copilot.
    guard = (
        "import subprocess,pathlib\n"
        "original_run=subprocess.run\n"
        "def guarded_run(argv,*args,**kwargs):\n"
        "    selected=list(argv)\n"
        "    if '%COPILOT_VENUE_BATCH_EXECUTABLE%' in argv:\n"
        "        selected.append(kwargs['env']['COPILOT_VENUE_BATCH_EXECUTABLE'].strip('\"'))\n"
        "    assert not any(pathlib.Path(str(arg)).stem.lower()=='copilot' for arg in selected), selected\n"
        "    return original_run(argv,*args,**kwargs)\n"
        "subprocess.run=guarded_run\n"
    )
    monkeypatch.chdir(daemon)
    if emitted:
        script = build_prepare_script(
            {"plugins": request["plugins"]}, str(execution), include_prepared_context=True,
        )
        python = script.split("python3 - <<'VENUE_PLUGIN_PREPARE'\n", 1)[1].rsplit(
            "VENUE_PLUGIN_PREPARE", 1,
        )[0]
        result = subprocess.run(
            [sys.executable, "-"], input=guard + python, cwd=daemon, env=environment,
            capture_output=True, text=True, timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert len(parse_prepared(result.stdout)) == 1
    else:
        original_run = subprocess.run
        def guarded_run(argv, *args, **kwargs):
            selected = list(argv)
            if "%" + BATCH_EXECUTABLE_ENV + "%" in argv:
                selected.append(kwargs["env"][BATCH_EXECUTABLE_ENV].strip('"'))
            assert not any(Path(str(arg)).stem.lower() == "copilot" for arg in selected), selected
            return original_run(argv, *args, **kwargs)
        monkeypatch.setattr(subprocess, "run", guarded_run)
        assert "sample@remote" in prepare(request, environment=environment)
    recorded = [json.loads(line) for line in calls.read_text().splitlines()]
    assert len(recorded) == 1 and recorded[0]["argv"][:2] == ["plugin", "install"]
    assert not Path(recorded[0]["cwd"]).is_relative_to(repo)
    assert preferences.read_bytes() == before and Path.cwd() == daemon
