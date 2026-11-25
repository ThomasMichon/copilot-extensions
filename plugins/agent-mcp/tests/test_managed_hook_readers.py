"""Actual guidance wrappers use the prepared managed selector and capture owner."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import sys
import time
from collections.abc import Callable

import pytest

from agent_procutil.owned_budget import OwnedExecutionBudget
from agent_procutil.owned_process import run_caller_owned

from .managed_hook_fixture import packaged_hooks as packaged_hooks
from .managed_hook_fixture import prepared_helper as prepared_helper


@pytest.mark.parametrize("entry", [
    "powershell", "pwsh", "windows_bash",
    pytest.param("bash", marks=pytest.mark.skipif(
        sys.platform != "linux", reason="Linux native hook custody",
    )),
])
@pytest.mark.parametrize("scenario", [
    "packaged", "missing-uv", "unprepared", "missing-provider",
    "missing-request", "different-request", "unmaterialized", "incomplete-with-source",
])
def test_actual_managed_wrapper(
    tmp_path: Path, entry: str, scenario: str, packaged_hooks: Path,
    prepared_helper: tuple[Path, Path, Path],
    record_property: Callable[[str, object], None],
) -> None:
    shell: Path
    if entry == "windows_bash":
        if os.name != "nt" or scenario not in {"packaged", "unprepared"}:
            pytest.skip("Git Bash delegates the already-qualified native wrapper")
        git = shutil.which("git")
        candidate = Path(git).parents[1] / "bin/bash.exe" if git else None
        if candidate is None or not candidate.is_file():
            pytest.skip("Git Bash is unavailable")
        shell = candidate
    else:
        found = shutil.which(entry)
        if not found or entry == "bash" and os.name == "nt" or entry == "powershell" and os.name != "nt":
            pytest.skip("This control requires the addressed native shell")
        shell = Path(found)
    uv, selected, managed_root = prepared_helper
    payload = tmp_path / "source/plugins/agent-mcp"
    scripts = payload / "scripts"
    scripts.mkdir(parents=True)
    for name in ("write-session-guidance.ps1", "write-session-guidance.sh"):
        shutil.copy2(packaged_hooks / "scripts" / name, scripts / name)
    bootstrap = scripts / "_bootstrap_python"
    shutil.copytree(packaged_hooks / "scripts/_bootstrap_python", bootstrap)
    request = scripts / "bootstrap-python-version.txt"
    shutil.copy2(packaged_hooks / "scripts/bootstrap-python-version.txt", request)
    (scripts / "write_session_guidance.py").write_text(
        "import json,os,sys\nfrom pathlib import Path\n"
        "Path(os.environ['MCP_HOOK_PROOF']).write_text(json.dumps({"
        "'python':sys.executable,'input':sys.stdin.read()}),encoding='utf-8')\n"
        "sys.stdout.write('{}')\n", encoding="utf-8",
    )
    suffix = "sh" if entry == "bash" else "ps1"
    provider = bootstrap / ("bootstrap-uv." + suffix)
    if scenario == "missing-provider":
        provider.rename(provider.with_suffix(".unavailable"))
    if scenario == "missing-request":
        request.rename(request.with_suffix(".unavailable"))
    if scenario == "different-request":
        request.write_text("cpython@3.11.999\n", encoding="utf-8")
    if scenario in {"unmaterialized", "incomplete-with-source"}:
        bootstrap.rename(bootstrap.with_name("unavailable"))
        if scenario == "incomplete-with-source":
            neighbor = payload.parents[1] / "libs/installer-engine"
            neighbor.mkdir(parents=True)
            for name in ("bootstrap-uv.sh", "bootstrap-uv.ps1", "installer-instance-python-version.txt"):
                shutil.copy2(Path(__file__).resolve().parents[3] / "libs/installer-engine" / name, neighbor)
    commands = tmp_path / "commands"
    commands.mkdir()
    marker = tmp_path / "ambient-ran"
    for name in ("python", "python3", "py"):
        command = commands / (name + (".cmd" if os.name == "nt" else ""))
        command.write_text(
            '@echo off\n> "%AMBIENT_MARKER%" echo invoked\nexit /b 91\n'
            if os.name == "nt" else '#!/bin/sh\nprintf invoked > "$AMBIENT_MARKER"\nexit 91\n',
            encoding="utf-8",
        )
        command.chmod(0o700)
    if scenario != "missing-uv":
        shutil.copy2(uv, commands / uv.name)
    if os.name != "nt":
        utilities = ["dirname", "wc", "cat"]
        if entry == "bash":
            utilities.extend((
                "bash", "uname", "readlink", "mkfifo", "setsid", "mktemp", "rm", "rmdir", "sleep",
            ))
        for name in utilities:
            utility = shutil.which(name)
            assert utility, name
            (commands / name).symlink_to(utility)
    home = tmp_path / "home"
    home.mkdir()
    empty = tmp_path / "unprepared"
    empty.mkdir()
    proof = tmp_path / "writer.json"
    path = str(commands)
    if entry == "windows_bash":
        path += os.pathsep + str(shell.parents[1] / "usr/bin")
    env = {
        **os.environ, "PATH": path, "HOME": str(home), "USERPROFILE": str(home),
        "COPILOT_PLUGIN_ROOT": payload.as_posix(), "MCP_HOOK_PROOF": str(proof),
        "AMBIENT_MARKER": str(marker), "UV_OFFLINE": "1",
        "UV_PYTHON_INSTALL_DIR": str(empty if scenario == "unprepared" else managed_root),
        "UV_CACHE_DIR": str(tmp_path / "uv-cache"), "UV_CONFIG_FILE": "missing-relative.toml",
        "UV_NO_CONFIG": "false", "UV_PYTHON": "unrelated", "UV_NO_MANAGED_PYTHON": "1",
        "VIRTUAL_ENV": "unrelated", "CONDA_PREFIX": "unrelated",
        "PYTHONHOME": "unrelated", "PYTHONPATH": "unrelated",
    }
    env.pop("BASH_ENV", None)
    env.pop("ENV", None)
    command_line = [str(shell)]
    if entry in {"bash", "windows_bash"}:
        script = scripts / "write-session-guidance.sh"
    else:
        command_line += ["-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File"]
        script = scripts / "write-session-guidance.ps1"
    command_line.append(script.as_posix())
    text = '{"sessionId":"session-1","text":"snowman \u2603"}\n'
    successful = scenario == "packaged"
    samples = []
    for _ in range(2 if successful else 1):
        proof.unlink(missing_ok=True)
        begin = time.monotonic()
        result = run_caller_owned(
            command_line, env=env, cwd=tmp_path, input_text=text,
            timeout=45, max_stdout_bytes=65536, max_stderr_bytes=65536,
            budget=OwnedExecutionBudget.from_timeout(50, cleanup_reserve=5),
        )
        samples.append(time.monotonic() - begin)
        assert result.returncode == 0 and result.stdout == "{}" and not result.stderr, result
        assert not marker.exists() and not list(empty.iterdir())
        assert proof.exists() == successful
        if successful:
            data = json.loads(proof.read_text(encoding="utf-8"))
            assert Path(data["python"]).resolve() == selected
            assert data["input"] == text
    if successful:
        record_property("mcp_managed_hook_execution", json.dumps({
            "entry": entry, "samples_seconds": samples,
        }))
