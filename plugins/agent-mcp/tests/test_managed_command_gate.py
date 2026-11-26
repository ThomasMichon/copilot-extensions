"""Real prepared-only UV admission before MCP foreground application entry."""
from __future__ import annotations

from collections.abc import Callable
import hashlib
import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
import time

import pytest

from .managed_hook_fixture import HEADLESS, PLUGIN, REPO
from .managed_hook_fixture import packaged_hooks as packaged_hooks
from .managed_hook_fixture import prepared_helper as prepared_helper
from .test_payload_invocation import _fake_runtime_gate_payload

SCENARIOS = (
    "current", "lkg", "newest", "namespaced", "readiness",
    "missing-uv", "missing-provider", "missing-capture",
)


def _snapshot(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob("*") if path.is_file()
    }


@pytest.fixture(scope="module")
def application_python(prepared_helper: tuple[Path, Path, Path]) -> Path:
    uv, _, store = prepared_helper
    result = subprocess.run(
        [str(uv), "python", "find", "--no-project", "--system", "--managed-python",
         "--no-python-downloads", "cpython@3.10.20"],
        env={**os.environ, "UV_PYTHON_INSTALL_DIR": str(store)},
        capture_output=True, text=True, timeout=15, creationflags=HEADLESS,
    )
    if result.returncode:
        pytest.skip("This acquisition-free control requires prepared CPython 3.10.20")
    return Path(result.stdout.strip()).resolve()


@pytest.mark.parametrize("host", ["bash", "pwsh", "powershell"])
@pytest.mark.parametrize("materialized", [False, True], ids=["raw", "materialized"])
@pytest.mark.parametrize("scenario", SCENARIOS)
def test_ready_command_admits_exact_prepared_application(
    tmp_path: Path, host: str, materialized: bool, scenario: str,
    prepared_helper: tuple[Path, Path, Path], application_python: Path,
    packaged_hooks: Path, record_property: Callable[[str, object], None],
) -> None:
    if (host == "bash") == (os.name == "nt"):
        pytest.skip("This control requires the addressed native shell")
    shell = shutil.which(host)
    if not shell:
        pytest.skip(f"{host} unavailable")
    uv, bootstrap, store = prepared_helper
    assert bootstrap != application_python
    home = tmp_path / "home"
    home.mkdir()
    legacy = home / ".agent-mcp"
    root = tmp_path / "selected application's \u03bb root" if scenario == "namespaced" else legacy
    payload = _fake_runtime_gate_payload(
        tmp_path / "source/plugins/agent-mcp",
        windows=host != "bash", runtime_root=root,
    )
    scripts = payload / "scripts"
    suffix = "sh" if host == "bash" else "ps1"
    shutil.copy2(PLUGIN / "scripts" / f"resolve-runtime.{suffix}", scripts)
    shutil.copytree(packaged_hooks / "scripts/_bootstrap_python", scripts / "_bootstrap_python",
                    dirs_exist_ok=True)
    shutil.copytree(PLUGIN / "bin", payload / "bin")
    for name in ("versioned_runtime_probe.py", "readiness-isolation.json"):
        source = PLUGIN / ("scripts" if name.endswith(".py") else "") / name
        shutil.copy2(source, scripts / name if name.endswith(".py") else payload / name)
    if materialized:
        previous = sys.path[:]
        try:
            owner = runpy.run_path(str(REPO / "tools/materialize_installer_engine.py"))
            for source, target in owner["derived_file_pairs"](
                REPO, payload.parents[1], copied_consumer=(PLUGIN, payload),
            ):
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
        finally:
            sys.path[:] = previous
    else:
        # Source layout uses the canonical capture, not a local fake or fallback copy.
        capture = payload.parents[1] / "libs/agent-procutil/src/agent_procutil/native_capture.sh"
        capture.parent.mkdir(parents=True)
        shutil.copy2(REPO / "libs/agent-procutil/src/agent_procutil/native_capture.sh", capture)
        (scripts / "native_capture.sh").unlink(missing_ok=True)
    provider = scripts / "_bootstrap_python" / f"bootstrap-uv.{suffix}"
    invocation = '    exec "$@"\n' if host == "bash" else "    $result = & $InvokeCaptured @options\n"
    observation = (
        '    printf "%s\\n" "${*:2}" >> "$MCP_UV_LOG"\n' if host == "bash" else
        '    [IO.File]::AppendAllText($env:MCP_UV_LOG, ($Arguments -join " ") + "`n")\n'
    )
    original = provider.read_text(encoding="utf-8")
    assert original.count(invocation) == 1
    provider.write_text(original.replace(invocation, observation + invocation), encoding="utf-8")
    tools = tmp_path / "private tools"
    tools.mkdir()
    if os.name != "nt":
        for name in ("bash", "dirname", "awk", "tr", "sed", "date", "sort", "find",
                     "mktemp", "mkfifo", "sleep", "rm", "rmdir", "mkdir", "cat",
                     "grep", "cut", "flock", "ln", "readlink"):
            executable = shutil.which(name)
            if name == "flock" and sys.platform == "darwin":
                continue
            assert executable, name
            (tools / name).symlink_to(executable)
    for name in ("python", "python3", "py"):
        poison = tools / (name + ".cmd" if os.name == "nt" else name)
        poison.write_text(
            '@echo off\n> "%AMBIENT_MARKER%" echo called\nexit /b 91\n' if os.name == "nt" else
            '#!/bin/sh\nprintf called > "$AMBIENT_MARKER"\nexit 91\n',
            encoding="utf-8",
        )
        poison.chmod(0o700)
    if scenario != "missing-uv":
        shutil.copy2(uv, tools / uv.name)
    env = {
        **os.environ, "HOME": str(home), "USERPROFILE": str(home), "PATH": str(tools),
        "UV_PYTHON_INSTALL_DIR": str(store), "UV_CACHE_DIR": str(tmp_path / "uv-cache"),
        "UV_OFFLINE": "1", "UV_NO_CONFIG": "1", "UV_PYTHON_DOWNLOADS": "never",
    }
    for name in ("BASH_ENV", "ENV", "COPILOT_EXTENSIONS_CONTEXT"):
        env.pop(name, None)
    slot = root / "versions/1.2.3"
    built = subprocess.run(
        [str(uv), "venv", "--no-project", "--no-python-downloads",
         "--python", str(application_python), str(slot)],
        env=env, capture_output=True, text=True, timeout=20, creationflags=HEADLESS,
    )
    assert built.returncode == 0, built.stdout + built.stderr
    python = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    site = slot / ("Lib/site-packages" if os.name == "nt" else "lib/python3.10/site-packages")
    package = site / "agent_mcp"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "__main__.py").write_text(
        "import json,os,sys\nfrom pathlib import Path\n"
        "if os.environ.get('INHERITED_FD_TARGET'):\n"
        " for fd in range(5,10): os.write(fd,('application-fd-%s\\n'%fd).encode())\n"
        "value={'python':sys.executable,'prefix':sys.prefix,'version':list(sys.version_info[:3]),"
        "'argv':sys.argv[1:],'stdin':sys.stdin.read(),'cwd':os.getcwd(),"
        "'root':os.environ.get('AGENT_MCP_HOME'),'context':os.environ.get('COPILOT_EXTENSIONS_CONTEXT'),"
        "'selector':os.environ.get('UV_PYTHON'),'isolated':sys.flags.isolated,"
        "'bytecode':sys.dont_write_bytecode}\n"
        "Path(os.environ['APPLICATION_PROOF']).write_text(json.dumps(value),encoding='utf-8')\n"
        "print('application stdout')\nprint('application stderr',file=sys.stderr)\nraise SystemExit(37)\n",
        encoding="utf-8",
    )
    descriptor = slot / "share/copilot-extensions/agent_mcp/readiness-isolation.json"
    descriptor.parent.mkdir(parents=True)
    shutil.copy2(PLUGIN / "readiness-isolation.json", descriptor)
    (slot / ".install-complete.json").write_text(json.dumps({
        "version": "1.2.3", "completed_at": "fixture", "pid": 0,
    }), encoding="utf-8")
    if scenario != "newest":
        (root / ("last-known-good" if scenario == "lkg" else "current-version")).write_text(
            "1.2.3\n", encoding="utf-8",
        )
    if scenario == "lkg":
        (root / "current-version").write_text("incomplete\n", encoding="utf-8")
    context = tmp_path / "home/contexts/a/b/c/d/e" if scenario == "namespaced" else None
    decision = {
        "status": "ready", "reason": "namespaced-active",
        "actualMode": "namespaced" if context else "legacy",
        "desiredMode": "namespaced" if context else "legacy",
        "runtimeRoot": str(root), "context": str(context) if context else "",
        "installGeneration": 7,
    }
    uv_log, proof = tmp_path / "uv-calls", tmp_path / "application.json"
    env.update(
        AGENT_MCP_HOME=str(legacy), AGENT_MCP_PAYLOAD_ROOT=str(payload),
        TEST_STATUS_JSON=json.dumps(decision), TEST_VALIDATE_JSON=json.dumps({"generation": 7}),
        APPLICATION_PROOF=str(proof), MCP_UV_LOG=str(uv_log), AMBIENT_MARKER=str(tmp_path / "ambient-ran"),
        UV_PYTHON="unrelated-python", UV_NO_MANAGED_PYTHON="1",
        UV_CONFIG_FILE="missing-relative.toml", UV_NO_CONFIG="false",
        VIRTUAL_ENV="unrelated", CONDA_PREFIX="unrelated", PYTHONPATH="unrelated", PYTHONHOME="unrelated",
        PYTHONDONTWRITEBYTECODE="1",
    )
    if context:
        env["COPILOT_EXTENSIONS_CONTEXT"] = str(context)
    if scenario == "missing-provider":
        provider.rename(provider.with_suffix(".unavailable"))
    elif scenario == "missing-capture":
        capture = (
            scripts / "native_capture.sh" if materialized else
            payload.parents[1] / "libs/agent-procutil/src/agent_procutil/native_capture.sh"
        ) if host == "bash" else scripts / "_bootstrap_python" / (
            "bootstrap-capture.ps1" if os.name == "nt" else "versioned_runtime_native.ps1"
        )
        capture.rename(capture.with_suffix(".unavailable"))
    arguments = ["installer-readiness"] if scenario == "readiness" else [
        "call", "space value", 'quote"value', "", "end\\", "\u03bb",
    ]
    command = [shell, str(payload / "bin/agent-mcp"), *arguments]
    descriptors = tmp_path / "inherited-fds"
    if host == "bash" and scenario == "current":
        env["INHERITED_FD_TARGET"] = str(descriptors)
        command = [
            shell, "-c",
            'exec 5>>"$INHERITED_FD_TARGET" 6>>"$INHERITED_FD_TARGET" '
            '7>>"$INHERITED_FD_TARGET" 8>>"$INHERITED_FD_TARGET" 9>>"$INHERITED_FD_TARGET"\n'
            '"$@"\ncode=$?\n'
            "printf 'caller-fd-5\\n' >&5\nprintf 'caller-fd-6\\n' >&6\n"
            "printf 'caller-fd-7\\n' >&7\nprintf 'caller-fd-8\\n' >&8\n"
            'printf "caller-fd-9\\n" >&9\nexit "$code"\n',
            "mcp-inherited-descriptors", *command,
        ]
    elif host != "bash":
        harness = tmp_path / "invoke.ps1"
        quoted = ",".join("'" + value.replace("'", "''") + "'" for value in arguments)
        harness.write_text(
            "$PSNativeCommandArgumentPassing='Legacy'\n"
            f"$forward=@({quoted})\n& '{str(payload / 'bin/agent-mcp.ps1').replace(chr(39), chr(39)*2)}' @forward\n"
            "exit $LASTEXITCODE\n", encoding="utf-8-sig",
        )
        command = [shell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(harness)]
    original_slot, original_payload = _snapshot(slot), _snapshot(payload)
    samples = []
    refused = scenario.startswith("missing-")
    expected_lookup = f"python find --no-project --managed-python --no-python-downloads {python}"
    for cycle in range(1 if refused else 2):
        started = time.monotonic()
        result = subprocess.run(
            command, env=env, cwd=tmp_path, input="foreground input\n",
            capture_output=True, text=True, encoding="utf-8", timeout=35, creationflags=HEADLESS,
        )
        samples.append(time.monotonic() - started)
        assert not Path(env["AMBIENT_MARKER"]).exists()
        assert "installer should not run" not in result.stderr, result.stdout + result.stderr
        assert _snapshot(slot) == original_slot and _snapshot(payload) == original_payload
        assert result.returncode == (126 if refused else 37), result.stdout + result.stderr
        calls = uv_log.read_text(encoding="utf-8").splitlines() if uv_log.exists() else []
        assert calls == ([] if refused else [expected_lookup] * (cycle + 1)), result.stdout + result.stderr
        if refused:
            assert result.returncode == 126 and not result.stdout, result.stdout + result.stderr
            assert not proof.exists()
            continue
        assert result.returncode == 37, result.stdout + result.stderr
        assert result.stdout.strip() == "application stdout" and "application stderr" in result.stderr
        actual = json.loads(proof.read_text(encoding="utf-8"))
        assert actual == {
            "python": str(python), "prefix": str(slot), "version": [3, 10, 20],
            "argv": arguments, "stdin": "foreground input\n", "cwd": str(tmp_path),
            "root": str(root), "context": str(context) if context else None,
            "selector": "unrelated-python", "isolated": int(scenario == "readiness"),
            "bytecode": True,
        }
        if host == "bash" and scenario == "current":
            assert descriptors.read_text(encoding="utf-8").splitlines() == (
                [f"application-fd-{fd}" for fd in range(5, 10)]
                + [f"caller-fd-{fd}" for fd in range(5, 10)]
            ) * (cycle + 1)
    record_property("mcp_command_admission", json.dumps({
        "shell": host, "scenario": scenario, "materialized": materialized,
        "samples_seconds": samples, "network": "disabled", "runtime_python": "3.10.20",
        "bootstrap_python": "3.11.18", "application_uv_lookups": len(calls),
    }))
