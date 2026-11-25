"""Actual quiet hooks inspect prepared managed Python without ambient fallback."""
from __future__ import annotations

import json
import os
from pathlib import Path
import runpy
import shutil
import sys
import time
from collections.abc import Callable

import pytest

from agent_procutil.owned_budget import OwnedExecutionBudget
from agent_procutil.owned_process import run_caller_owned

from .managed_hook_fixture import prepared_helper as prepared_helper

PLUGIN = Path(__file__).resolve().parents[1]
REPO = PLUGIN.parents[1]


@pytest.fixture(scope="module")
def packaged_hooks(tmp_path_factory: pytest.TempPathFactory) -> Path:
    payload = tmp_path_factory.mktemp("logger-hook-package") / "agent-logger"
    shutil.copytree(PLUGIN / "scripts", payload / "scripts")
    for name in ("plugin.json", "payload-invocation.json", "pyproject.toml"):
        shutil.copy2(PLUGIN / name, payload / name)
    shutil.copytree(PLUGIN / "references", payload / "references")
    previous_path = sys.path[:]
    try:
        owner = runpy.run_path(str(REPO / "tools/materialize_main.py"))
        changes = owner["materialize_installer_engine_ref_into"](
            source_consumer_dir=PLUGIN, dest_consumer_dir=payload, canonical_root=REPO,
        )
        invocation = runpy.run_path(str(REPO / "tools/materialize_payload_invocation.py"))
        changes += invocation["materialize"](
            payload, canonical_root=REPO, source_consumer=PLUGIN,
        )
    finally:
        sys.path[:] = previous_path
    assert changes and not any(line.startswith("SKIP") for line in changes), changes
    return payload


@pytest.mark.parametrize("entry", ["guidance-bash", "provider-bash", "guidance-powershell", "guidance-pwsh"])
@pytest.mark.parametrize("scenario", [
    "raw", "packaged", "missing-uv", "unprepared", "missing-provider",
    "missing-request", "different-request", "incomplete-with-source",
])
def test_actual_hook_managed_selection(
    tmp_path: Path, entry: str, scenario: str, packaged_hooks: Path,
    prepared_helper: tuple[Path, Path, Path],
    record_property: Callable[[str, object], None],
) -> None:
    shell_name = entry.split("-", 1)[1]
    shell = shutil.which(shell_name)
    if not shell or shell_name == "bash" and os.name == "nt" or shell_name == "powershell" and os.name != "nt":
        pytest.skip("This control requires the addressed native shell")
    uv, selected, managed_root = prepared_helper
    raw = scenario == "raw"
    payload = tmp_path / "source" / "plugins" / "agent-logger"
    scripts = payload / "scripts"
    scripts.mkdir(parents=True)
    source_scripts = PLUGIN / "scripts" if raw else packaged_hooks / "scripts"
    for name in (
        "hook-bootstrap-python.sh", "hook-bootstrap-python.ps1",
        "write-session-guidance.sh", "write-session-guidance.ps1",
        "register-cold-store-provider.sh", "install.sh", "install.ps1",
    ):
        shutil.copy2(source_scripts / name, scripts / name)
    if raw:
        engine = payload.parents[1] / "libs" / "installer-engine"
        engine.mkdir(parents=True)
        for name in (
            "bootstrap-uv.sh", "bootstrap-uv.ps1", "bootstrap-capture.ps1",
            "bootstrap-capture.exe", "installer-instance-python-version.txt",
        ):
            shutil.copy2(REPO / "libs/installer-engine" / name, engine / name)
        native = engine.parent / "versioned-runtime"
        native.mkdir()
        shutil.copy2(REPO / "libs/versioned-runtime/versioned_runtime_native.ps1", native)
    else:
        shutil.copytree(source_scripts / "_bootstrap_python", scripts / "_bootstrap_python")
        shutil.copy2(source_scripts / "bootstrap-python-version.txt", scripts)
    shutil.copy2(PLUGIN / "plugin.json", payload)
    (payload / "references").mkdir()
    shutil.copy2(PLUGIN / "references/cold-store-provider.json", payload / "references")
    (payload / "bin").mkdir()
    (payload / "bin/agent-logger").write_text("#!/bin/sh\nexit 91\n", encoding="utf-8")
    (payload / "bin/agent-logger").chmod(0o700)
    (scripts / "write_session_guidance.py").write_text(
        "import json,os,sys\nfrom pathlib import Path\n"
        "Path(os.environ['LOGGER_HOOK_PROOF']).write_text(json.dumps({"
        "'python':sys.executable,'input':sys.stdin.read()}),encoding='utf-8')\n"
        "sys.stdout.write('{}')\n", encoding="utf-8",
    )
    suffix = "sh" if shell_name == "bash" else "ps1"
    provider = scripts / "_bootstrap_python" / ("bootstrap-uv." + suffix)
    request = scripts / "bootstrap-python-version.txt"
    if scenario == "missing-provider":
        provider.rename(provider.with_suffix(".unavailable"))
    if scenario == "missing-request":
        request.rename(request.with_suffix(".unavailable"))
    if scenario == "different-request":
        request.write_text("cpython@3.11.999\n", encoding="utf-8")
    if scenario == "incomplete-with-source":
        provider.rename(provider.with_suffix(".unavailable"))
        request.rename(request.with_suffix(".unavailable"))
        neighbor = payload.parents[1] / "libs/installer-engine"
        neighbor.mkdir(parents=True)
        for name in ("bootstrap-uv.sh", "bootstrap-uv.ps1", "installer-instance-python-version.txt"):
            shutil.copy2(REPO / "libs/installer-engine" / name, neighbor)
    commands = tmp_path / "commands"
    commands.mkdir()
    ambient = tmp_path / "ambient-ran"
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
        for name in ("dirname", "wc", "cat", "mkdir"):
            utility = shutil.which(name)
            assert utility, name
            (commands / name).symlink_to(utility)
    home = tmp_path / "home"
    home.mkdir()
    empty = tmp_path / "unprepared"
    empty.mkdir()
    proof = tmp_path / "writer.json"
    registry = tmp_path / "registry"
    env = {
        **os.environ, "PATH": str(commands), "HOME": str(home), "USERPROFILE": str(home),
        "COPILOT_PLUGIN_ROOT": str(payload), "LOGGER_HOOK_PROOF": str(proof),
        "AGENT_BRIDGE_COLD_STORE_PROVIDERS_DIR": str(registry),
        "AMBIENT_MARKER": str(ambient), "UV_OFFLINE": "1",
        "UV_PYTHON_INSTALL_DIR": str(empty if scenario == "unprepared" else managed_root),
        "UV_CACHE_DIR": str(tmp_path / "uv-cache"), "UV_CONFIG_FILE": "missing-relative.toml",
        "UV_NO_CONFIG": "false", "UV_PYTHON": "unrelated", "UV_NO_MANAGED_PYTHON": "1",
        "VIRTUAL_ENV": "unrelated", "CONDA_PREFIX": "unrelated",
        "PYTHONHOME": "unrelated", "PYTHONPATH": "unrelated",
    }
    env.pop("BASH_ENV", None)
    env.pop("ENV", None)
    script = scripts / (
        "register-cold-store-provider.sh" if entry == "provider-bash"
        else "write-session-guidance." + suffix
    )
    command_line = [shell]
    if shell_name != "bash":
        command_line += ["-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File"]
    command_line.append(str(script))
    payload_text = '{"sessionId":"session-1","text":"snowman \u2603"}\n'
    successful = scenario in {"raw", "packaged"}
    samples = []
    for _ in range(2 if successful else 1):
        begin = time.monotonic()
        limit = 10 if entry == "provider-bash" else 45
        result = run_caller_owned(
            command_line, env=env, cwd=tmp_path, input_text=payload_text,
            timeout=limit, max_stdout_bytes=65536, max_stderr_bytes=65536,
            budget=OwnedExecutionBudget.from_timeout(limit + 5, cleanup_reserve=5),
        )
        samples.append(time.monotonic() - begin)
        assert result.returncode == 0 and result.stdout == "{}" and not result.stderr, result
        assert not ambient.exists() and not list(empty.iterdir())
        if entry == "provider-bash":
            output = registry / "agent-logger.json"
            assert output.exists() == successful
            if successful:
                data = json.loads(output.read_text(encoding="utf-8"))
                assert data["plugin_root"] == str(payload.resolve())
                assert len(data["command"]) == 1
                assert Path(data["command"][0]).resolve() == (payload / "bin/agent-logger").resolve()
        else:
            assert proof.exists() == successful
            if successful:
                data = json.loads(proof.read_text(encoding="utf-8"))
                assert Path(data["python"]).resolve() == selected
                assert data["input"] == payload_text
    if successful:
        record_property("logger_managed_hook_performance", json.dumps({
            "entry": entry, "scenario": scenario, "samples_seconds": samples,
            "network": "disabled", "selection": "prepared managed bootstrap",
        }))


@pytest.mark.skipif(os.name != "nt", reason="Native Windows Bash handoff")
@pytest.mark.parametrize("entry", ["guidance", "provider"])
def test_windows_bash_enters_native_hook(
    tmp_path: Path, entry: str, packaged_hooks: Path,
    request: pytest.FixtureRequest,
) -> None:
    from .test_cold_store_provider_manifest import _BASH

    if not _BASH:
        pytest.skip("Git Bash is unavailable")
    prepared: tuple[Path, Path, Path] | None = None
    if entry == "guidance":
        prepared = request.getfixturevalue("prepared_helper")
    payload = tmp_path / "payload"
    shutil.copytree(packaged_hooks, payload)
    (payload / "scripts/write_session_guidance.py").write_text(
        "import json,os,sys\nfrom pathlib import Path\n"
        "Path(os.environ['LOGGER_HOOK_PROOF']).write_text(json.dumps({"
        "'python':sys.executable,'input':sys.stdin.read()}),encoding='utf-8')\n"
        "sys.stdout.write('{}')\n", encoding="utf-8",
    )
    commands = tmp_path / "commands"
    commands.mkdir()
    ambient = tmp_path / "ambient-ran"
    for name in ("python", "python3", "py"):
        (commands / (name + ".cmd")).write_text(
            '@echo off\n> "%AMBIENT_MARKER%" echo invoked\nexit /b 91\n', encoding="utf-8",
        )
    system = Path(os.environ["SystemRoot"]) / "System32"
    git_tools = Path(_BASH).parents[1] / "usr" / "bin"
    home = tmp_path / "home"
    home.mkdir()
    empty = tmp_path / "unprepared"
    empty.mkdir()
    proof = tmp_path / "writer.json"
    registry = tmp_path / "registry"
    env = {
        **os.environ, "HOME": str(home), "USERPROFILE": str(home),
        "PATH": os.pathsep.join(map(str, (
            commands, *(() if prepared is None else (prepared[0].parent,)), system, git_tools,
        ))),
        "COPILOT_PLUGIN_ROOT": str(payload), "LOGGER_HOOK_PROOF": str(proof),
        "AGENT_BRIDGE_COLD_STORE_PROVIDERS_DIR": str(registry),
        "AMBIENT_MARKER": str(ambient), "UV_OFFLINE": "1",
        "UV_PYTHON_INSTALL_DIR": str(empty if prepared is None else prepared[2]),
        "UV_CACHE_DIR": str(tmp_path / "uv-cache"), "UV_CONFIG_FILE": "missing-relative.toml",
        "UV_NO_CONFIG": "false", "UV_PYTHON": "unrelated", "UV_NO_MANAGED_PYTHON": "1",
        "PYTHONHOME": "unrelated", "PYTHONPATH": "unrelated", "VIRTUAL_ENV": "unrelated",
    }
    env.pop("BASH_ENV", None)
    env.pop("ENV", None)
    stem = "register-cold-store-provider" if entry == "provider" else "write-session-guidance"
    text = '{"sessionId":"session-1","text":"snowman \u2603"}\n'
    limit = 10 if entry == "provider" else 45
    for _ in range(2):
        result = run_caller_owned(
            [_BASH, str(payload / "scripts" / (stem + ".sh"))],
            timeout=limit, env=env, cwd=tmp_path, input_text=text,
            max_stdout_bytes=65536, max_stderr_bytes=65536,
            budget=OwnedExecutionBudget.from_timeout(limit + 5, cleanup_reserve=5),
        )
        assert result.returncode == 0 and result.stdout == "{}" and not result.stderr, result
        assert not ambient.exists() and not list(empty.iterdir())
        if entry == "provider":
            data = json.loads((registry / "agent-logger.json").read_text(encoding="utf-8"))
            assert Path(data["plugin_root"]).resolve() == payload.resolve()
            assert data["command"] == [str(payload / "bin/agent-logger.cmd")]
        else:
            data = json.loads(proof.read_text(encoding="utf-8"))
            assert prepared is not None
            assert Path(data["python"]).resolve() == prepared[1] and data["input"] == text
