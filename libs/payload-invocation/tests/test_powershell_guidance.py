"""Execute a materialized adopter's actual hook without ambient Python."""
from __future__ import annotations

import json
import os
from pathlib import Path
import runpy
import shutil
import stat
import subprocess
import sys
import time
from collections.abc import Callable
from functools import cache

import pytest

from agent_procutil.owned_budget import OwnedExecutionBudget
from agent_procutil.owned_process import run_caller_owned

REPO = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="session")
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


def materialized_guidance(tmp_path: Path, plugin: str) -> Path:
    source = REPO / "plugins" / plugin
    payload = tmp_path / "package" / plugin
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


@pytest.fixture
def packaged_guidance(
    tmp_path: Path, guidance_templates: Callable[[str], Path],
) -> Path:
    payload = tmp_path / "package/agent-ssh"
    shutil.copytree(guidance_templates("agent-ssh"), payload)
    return payload


@pytest.fixture(scope="session")
def guidance_templates(tmp_path_factory: pytest.TempPathFactory) -> Callable[[str], Path]:
    root = tmp_path_factory.mktemp("guidance-materialized")

    @cache
    def template(plugin: str) -> Path:
        return materialized_guidance(root, plugin)

    return template


@pytest.mark.parametrize("entry", ["powershell", "pwsh"])
@pytest.mark.parametrize("scenario", [
    "prepared", "missing-uv", "unprepared", "missing-support", "missing-request", "oversized",
    "resumed-missing-uv", "resumed-unprepared", "resumed-missing-support",
    "resumed-missing-request", "resumed-writer-failure", "resumed-redirected",
    "resumed-hardlink", "resumed-invalid-session",
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
    resumed = scenario.startswith("resumed-")
    mode = scenario.removeprefix("resumed-")
    payload = packaged_guidance
    scripts = payload / "scripts"
    (scripts / "write_session_guidance.py").write_text(
        "import json,os,sys\nfrom pathlib import Path\n"
        "Path(os.environ['GUIDANCE_PROOF']).write_text(json.dumps({"
        "'python':sys.executable,'input':sys.stdin.read()}),encoding='utf-8')\n"
        "sys.stdout.write('{}')\n", encoding="utf-8",
    )
    if mode == "writer-failure":
        (scripts / "write_session_guidance.py").write_text("raise SystemExit(1)\n")
    if mode == "missing-support":
        bootstrap = scripts / "_bootstrap_python"
        bootstrap.rename(bootstrap.with_name("unavailable"))
    if mode == "missing-request":
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
    if mode not in {"missing-uv", "invalid-session", "hardlink"}:
        shutil.copy2(uv, commands / uv.name)
    home = tmp_path / "home"
    home.mkdir()
    stale = home / ".copilot/session-state/session-1/instructions/agent-ssh/session-guidance.instructions.md"
    other = home / ".copilot/session-state/session-2/instructions/agent-ssh/session-guidance.instructions.md"
    foreign = home / ".copilot/outside/instructions/agent-ssh/session-guidance.instructions.md"
    newline_target = None
    if resumed:
        for target in (stale, other, foreign):
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("stale snapshot", encoding="utf-8")
            target.chmod(0o600)
        if mode == "redirected":
            stale.unlink()
            stale.parent.rmdir()
            if os.name == "nt":
                import _winapi

                getattr(_winapi, "CreateJunction")(str(foreign.parent), str(stale.parent))
            else:
                stale.parent.symlink_to(foreign.parent, target_is_directory=True)
        if mode == "hardlink":
            stale.unlink()
            stale.hardlink_to(foreign)
        if mode == "invalid-session" and os.name != "nt":
            newline_target = home / ".copilot/session-state/session-1\n/instructions/agent-ssh/session-guidance.instructions.md"
            newline_target.parent.mkdir(parents=True)
            newline_target.write_text("invalid identifier snapshot", encoding="utf-8")
    empty = tmp_path / "unprepared"
    empty.mkdir()
    proof = tmp_path / "writer.json"
    env = {
        **os.environ, "PATH": str(commands), "HOME": str(home), "USERPROFILE": str(home),
        "COPILOT_PLUGIN_ROOT": str(payload), "GUIDANCE_PROOF": str(proof),
        "AMBIENT_MARKER": str(marker), "UV_OFFLINE": "1",
        "UV_PYTHON_INSTALL_DIR": str(empty if mode == "unprepared" else managed_root),
        "UV_CACHE_DIR": str(tmp_path / "uv-cache"), "UV_CONFIG_FILE": "missing-relative.toml",
        "UV_NO_CONFIG": "false", "UV_PYTHON": "unrelated", "UV_NO_MANAGED_PYTHON": "1",
        "VIRTUAL_ENV": "unrelated", "CONDA_PREFIX": "unrelated",
        "PYTHONHOME": "unrelated", "PYTHONPATH": "unrelated",
    }
    hooks = json.loads((payload / "hooks.json").read_text())["hooks"]["sessionStart"]
    hook_commands = [h["powershell"] for h in hooks if "write-session-guidance.ps1" in h["powershell"]]
    assert len(hook_commands) == 1
    session_id = "../outside" if mode == "invalid-session" else "session-1"
    text = "x" * 65537 if mode == "oversized" else json.dumps(
        {"sessionId": session_id, "text": "snowman \u2603"}, ensure_ascii=False,
    ) + "\n"
    successful = mode == "prepared"
    texts = [text] * (2 if successful else 1)
    if mode == "invalid-session":
        texts.extend([
            json.dumps({"sessionId": "session-1\n"}) + "\n",
            json.dumps({"SessionID": "session-1"}) + "\n",
        ])
    samples = []
    for text in texts:
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
        if resumed:
            if newline_target is not None:
                assert newline_target.read_text() == "invalid identifier snapshot"
            assert other.read_text(encoding="utf-8") == foreign.read_text(encoding="utf-8") == "stale snapshot"
            content = stale.read_text(encoding="utf-8")
            if mode in {"redirected", "invalid-session"}:
                assert content == "stale snapshot"
            else:
                assert "unavailable" in content and "stale snapshot" not in content
                assert not list(stale.parent.glob(".session-guidance-unavailable-*"))
                if os.name != "nt":
                    assert stat.S_IMODE(stale.stat().st_mode) == 0o600
    if successful:
        record_property("powershell_guidance_execution", json.dumps({
            "entry": entry, "samples_seconds": samples, "hook_timeout_seconds": 45,
        }))
    if resumed:
        record_property("guidance_resumed_execution", json.dumps({
            "entry": entry, "scenario": mode, "samples_seconds": samples,
        }))


@pytest.mark.parametrize("entry", ["powershell", "pwsh"])
def test_mcp_resumed_unavailable_marker_is_plugin_scoped(
    tmp_path: Path, entry: str, guidance_templates: Callable[[str], Path],
) -> None:
    shell = shutil.which(entry)
    if not shell or entry == "powershell" and os.name != "nt":
        pytest.skip("This control requires the addressed native shell")
    payload = tmp_path / "package/agent-mcp"
    shutil.copytree(guidance_templates("agent-mcp"), payload)
    home = tmp_path / "home"
    targets = {}
    for plugin in ("agent-mcp", "agent-ssh"):
        target = home / ".copilot/session-state/session-1/instructions" / plugin / "session-guidance.instructions.md"
        target.parent.mkdir(parents=True)
        target.write_text("stale snapshot", encoding="utf-8")
        target.chmod(0o600)
        targets[plugin] = target
    empty = tmp_path / "commands"
    empty.mkdir()
    hooks = json.loads((payload / "hooks.json").read_text())["hooks"]["sessionStart"]
    commands = [h["powershell"] for h in hooks if "write-session-guidance.ps1" in h["powershell"]]
    assert len(commands) == 1
    result = run_caller_owned(
        [shell, "-NoProfile", "-NonInteractive", "-Command", commands[0]],
        cwd=tmp_path, env={
            **os.environ, "PATH": str(empty), "HOME": str(home), "USERPROFILE": str(home),
            "COPILOT_PLUGIN_ROOT": str(payload), "UV_OFFLINE": "1",
        }, input_text='{"sessionId":"session-1"}',
        timeout=45, max_stdout_bytes=65536, max_stderr_bytes=65536,
        budget=OwnedExecutionBudget.from_timeout(50, cleanup_reserve=5),
    )
    assert result.returncode == 0 and result.stdout == "{}" and not result.stderr, result
    assert targets["agent-ssh"].read_text() == "stale snapshot"
    content = targets["agent-mcp"].read_text()
    assert "unavailable" in content and "stale snapshot" not in content
    if os.name != "nt":
        assert stat.S_IMODE(targets["agent-mcp"].stat().st_mode) == 0o600
