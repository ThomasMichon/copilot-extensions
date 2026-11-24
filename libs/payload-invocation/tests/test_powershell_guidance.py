"""Execute a materialized adopter's actual hook without ambient Python."""
from __future__ import annotations

import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
import time
from collections.abc import Callable

import pytest

from agent_procutil.owned_budget import OwnedExecutionBudget
from agent_procutil.owned_process import run_caller_owned

REPO = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def prepared_guidance_python() -> tuple[Path, Path, Path]:
    uv = shutil.which("uv")
    if not uv:
        pytest.skip("This acquisition-free control requires prepared uv")
    flags = getattr(subprocess, "CREATE_NO_WINDOW") if os.name == "nt" else 0
    result = subprocess.run(
        [uv, "python", "find", "--no-project", "--system", "--managed-python",
         "--no-python-downloads", "cpython@3.11.18"],
        capture_output=True, text=True, timeout=15, creationflags=flags,
    )
    if result.returncode:
        pytest.skip("This acquisition-free control requires prepared CPython 3.11.18")
    root = subprocess.check_output(
        [uv, "python", "dir"], text=True, timeout=15, creationflags=flags,
    ).strip()
    return Path(uv), Path(result.stdout.strip()).resolve(), Path(root)


@pytest.fixture
def packaged_guidance(tmp_path: Path) -> Path:
    source = REPO / "plugins/agent-ssh"
    payload = tmp_path / "package/agent-ssh"
    shutil.copytree(source / "scripts", payload / "scripts")
    for name in ("plugin.json", "payload-invocation.json", "pyproject.toml", "hooks.json"):
        shutil.copy2(source / name, payload / name)
    previous_path = sys.path[:]
    try:
        owner = runpy.run_path(str(REPO / "tools/materialize_payload_invocation.py"))
        changes = owner["materialize"](payload, canonical_root=REPO, source_consumer=source)
    finally:
        sys.path[:] = previous_path
    assert changes and not any(line.startswith("SKIP") for line in changes), changes
    return payload


@pytest.mark.parametrize("entry", ["powershell", "pwsh"])
@pytest.mark.parametrize("scenario", [
    "prepared", "missing-uv", "unprepared", "missing-support", "missing-request", "oversized",
])
def test_powershell_guidance_actual_hook(
    tmp_path: Path, entry: str, scenario: str,
    packaged_guidance: Path,
    prepared_guidance_python: tuple[Path, Path, Path],
    record_property: Callable[[str, object], None],
) -> None:
    found = shutil.which(entry)
    if not found or entry == "powershell" and os.name != "nt":
        pytest.skip("This control requires the addressed native shell")
    uv, selected, managed_root = prepared_guidance_python
    payload = packaged_guidance
    scripts = payload / "scripts"
    (scripts / "write_session_guidance.py").write_text(
        "import json,os,sys\nfrom pathlib import Path\n"
        "Path(os.environ['GUIDANCE_PROOF']).write_text(json.dumps({"
        "'python':sys.executable,'input':sys.stdin.read()}),encoding='utf-8')\n"
        "sys.stdout.write('{}')\n", encoding="utf-8",
    )
    if scenario == "missing-support":
        bootstrap = scripts / "_bootstrap_python"
        bootstrap.rename(bootstrap.with_name("unavailable"))
    if scenario == "missing-request":
        (scripts / "bootstrap-python-version.txt").rename(scripts / "unavailable-request.txt")
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
    home = tmp_path / "home"
    home.mkdir()
    empty = tmp_path / "unprepared"
    empty.mkdir()
    proof = tmp_path / "writer.json"
    env = {
        **os.environ, "PATH": str(commands), "HOME": str(home), "USERPROFILE": str(home),
        "COPILOT_PLUGIN_ROOT": str(payload), "GUIDANCE_PROOF": str(proof),
        "AMBIENT_MARKER": str(marker), "UV_OFFLINE": "1",
        "UV_PYTHON_INSTALL_DIR": str(empty if scenario == "unprepared" else managed_root),
        "UV_CACHE_DIR": str(tmp_path / "uv-cache"), "UV_CONFIG_FILE": "missing-relative.toml",
        "UV_NO_CONFIG": "false", "UV_PYTHON": "unrelated", "UV_NO_MANAGED_PYTHON": "1",
        "VIRTUAL_ENV": "unrelated", "CONDA_PREFIX": "unrelated",
        "PYTHONHOME": "unrelated", "PYTHONPATH": "unrelated",
    }
    hooks = json.loads((payload / "hooks.json").read_text())["hooks"]["sessionStart"]
    hook_commands = [h["powershell"] for h in hooks if "write-session-guidance.ps1" in h["powershell"]]
    assert len(hook_commands) == 1
    text = "x" * 65537 if scenario == "oversized" else '{"text":"snowman \u2603"}\n'
    successful = scenario == "prepared"
    samples = []
    for _ in range(2 if successful else 1):
        proof.unlink(missing_ok=True)
        started = time.monotonic()
        result = run_caller_owned(
            [found, "-NoProfile", "-NonInteractive", "-Command", hook_commands[0]],
            env=env, cwd=tmp_path, input_text=text,
            timeout=45, max_stdout_bytes=65536, max_stderr_bytes=65536,
            budget=OwnedExecutionBudget.from_timeout(50, cleanup_reserve=5),
        )
        samples.append(time.monotonic() - started)
        assert result.returncode == 0 and result.stdout == "{}" and not result.stderr, result
        assert not marker.exists() and not list(empty.iterdir())
        assert proof.exists() == successful
        if successful:
            data = json.loads(proof.read_text(encoding="utf-8"))
            assert Path(data["python"]).resolve() == selected and data["input"] == text
    if successful:
        record_property("powershell_guidance_execution", json.dumps({
            "entry": entry, "samples_seconds": samples, "hook_timeout_seconds": 45,
        }))
