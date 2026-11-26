"""Actual Index gates retain prepared bootstrap and application bindings."""
from __future__ import annotations

from collections.abc import Callable
import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
import time

import pytest

from test_install_engine_adoption import PLUGIN
from test_managed_bootstrap_reader_sh import prepared_helper as prepared_helper
from test_managed_bootstrap_reader_sh import find_prepared_helper
from test_runtime_gate import _configure_repository_role, _fixture, _qualify_probe

SCENARIOS = (
    "current", "lkg", "newest", "namespaced", "setup", "readiness",
    "missing-marker", "missing-selector", "missing-uv", "missing-provider",
    "missing-capture", "unprepared", "provisioned", "admission-missing-uv",
)


@pytest.fixture(scope="module")
def application_python(prepared_helper: tuple[Path, Path, Path]) -> Path:
    uv, _, store = prepared_helper
    result = subprocess.run(
        [str(uv), "python", "find", "--no-project", "--system", "--managed-python",
         "--no-python-downloads", "cpython@3.10.20"],
        env={**os.environ, "UV_PYTHON_INSTALL_DIR": str(store)},
        capture_output=True, text=True, timeout=15,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.returncode:
        pytest.skip("This acquisition-free control requires prepared CPython 3.10.20")
    return Path(result.stdout.strip()).resolve()


@pytest.mark.parametrize("host", ["bash", "pwsh", "powershell"])
@pytest.mark.parametrize("scenario", SCENARIOS)
def test_command_gate_retains_prepared_bindings_without_ambient_fallback(
    tmp_path: Path, host: str, scenario: str, application_python: Path,
    prepared_helper: tuple[Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    record_property: Callable[[str, object], None],
) -> None:
    if (host == "bash" and os.name == "nt") or (host == "powershell" and os.name != "nt"):
        pytest.skip("This control requires the addressed native shell")
    executable = shutil.which(host)
    if not executable:
        pytest.skip(f"{host} unavailable")
    uv, bootstrap, store = prepared_helper
    assert application_python != bootstrap
    if scenario == "current":
        monkeypatch.delenv("UV_PYTHON_INSTALL_DIR", raising=False)
        monkeypatch.setattr(sys, "_base_executable", str(bootstrap))
        assert find_prepared_helper() == prepared_helper
    gate, env = _fixture(
        tmp_path, "bash" if host == "bash" else "pwsh", native_configuration_owner=False,
    )
    _configure_repository_role(tmp_path, env, "client")
    scripts = gate.parent
    outputs = runpy.run_path(str(PLUGIN.parents[1] / "libs/payload-invocation/bootstrap_python.py"))[
        "expected_files"
    ](
        scripts.parent / "payload-invocation.json",
        {"bootstrapPython": "3.11.18", "bootstrapLauncher": "uv"},
    )
    for path, content in outputs.items():
        path.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))
    suffix = "sh" if host == "bash" else "ps1"
    shutil.copy2(PLUGIN / "scripts" / f"resolve-runtime.{suffix}", scripts / f"resolve-runtime.{suffix}")
    root = tmp_path / "selected application's \u03bb root"
    slot = root / "versions/1.2.3"
    env.update(
        UV_OFFLINE="1", UV_NO_CONFIG="1", UV_PYTHON_DOWNLOADS="never",
        UV_PYTHON_INSTALL_DIR=str(store), UV_CACHE_DIR=str(tmp_path / "uv-cache"),
    )
    built = subprocess.run(
        [str(uv), "venv", "--no-project", "--no-python-downloads",
         "--python", str(application_python), str(slot)],
        env=env, capture_output=True, text=True, timeout=20,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert built.returncode == 0, built.stdout + built.stderr
    python = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    site = slot / ("Lib/site-packages" if os.name == "nt" else "lib/python3.10/site-packages")
    package = site / "agent_index"
    package.mkdir()
    (package / "__init__.py").write_text(
        "import os\nfrom pathlib import Path\n"
        "Path(os.environ['ORIGIN_MARKER']).touch()\n", encoding="utf-8",
    )
    (package / "__main__.py").write_text(
        "import json,os,sys\n"
        "if os.environ.get('INHERITED_FD_TARGET'):\n"
        " for fd in range(5,10): os.write(fd,('application-fd-%s\\n'%fd).encode())\n"
        "print(json.dumps({'executable':sys.executable,'prefix':sys.prefix,"
        "'version':list(sys.version_info[:3]),'argv':sys.argv[1:],'stdin':sys.stdin.read(),"
        "'isolated':sys.flags.isolated,'bytecode':sys.dont_write_bytecode,"
        "'selector':os.environ.get('UV_PYTHON')}))\n"
        "print('application stderr',file=sys.stderr)\nraise SystemExit(23)\n",
        encoding="utf-8",
    )
    _qualify_probe(slot / "share/copilot-extensions/agent_index/readiness-isolation.json")
    (slot / ".install-complete.json").write_text(json.dumps({
        "version": "1.2.3", "completed_at": "fixture", "pid": 0,
    }), encoding="utf-8")
    if scenario != "newest":
        (root / ("last-known-good" if scenario == "lkg" else "current-version")).write_text(
            "1.2.3\n", encoding="utf-8",
        )
    validation = tmp_path / "validation.json"
    (scripts / "cell-runtime.py").write_text(
        "import json,os,sys\nfrom pathlib import Path\n"
        "Path(os.environ['VALIDATION_MARKER']).write_text(json.dumps({"
        "'executable':sys.executable,'isolated':sys.flags.isolated}),encoding='utf-8')\n"
        "print(json.dumps({'status':'ready','runtimeVersion':'1.2.3',"
        "'interpreter':os.environ['TEST_PYTHON']}))\n", encoding="utf-8",
    )
    installer = scripts / f"install.{suffix}"
    installer.write_text(
        '#!/bin/sh\nprintf called > "$INSTALLER_MARKER"\nexit 99\n'
        if host == "bash" else
        "[IO.File]::WriteAllText($env:INSTALLER_MARKER,'called')\nexit 99\n",
        encoding="utf-8",
    )
    if scenario == "provisioned":
        resolver = scripts / f"resolve-runtime.{suffix}"
        resolver.rename(scripts / f"completed-runtime.{suffix}")
        resolver.write_text(
            'AGENT_RT_PY=""\nif [ -f "$INSTALLER_MARKER" ]; then\n'
            '. "$SCRIPT_DIR/completed-runtime.sh"\nfi\n'
            if host == "bash" else
            "$AgentRtPy=$null\nif (Test-Path -LiteralPath $env:INSTALLER_MARKER) {\n"
            ". (Join-Path $PSScriptRoot 'completed-runtime.ps1')\n}\n",
            encoding="utf-8",
        )
        (scripts / "installation-context" / f"legacy-entrypoint-probe.{suffix}").write_text(
            "exit 0\n", encoding="utf-8",
        )
        installer.write_text(
            '#!/bin/sh\nprintf called > "$INSTALLER_MARKER"\nexit 0\n'
            if host == "bash" else
            "[IO.File]::WriteAllText($env:INSTALLER_MARKER,'called')\nexit 0\n",
            encoding="utf-8",
        )
    tools = tmp_path / "private tools"
    tools.mkdir()
    for name in ("bash", "dirname", "basename", "wc", "cat", "git", "id", "cut",
                 "awk", "sed", "grep", "tr", "date", "head", "sort", "find",
                 "mktemp", "mkfifo", "sleep", "rm", "rmdir", "getent", "mkdir", "flock", "dscl"):
        binary = shutil.which(name)
        if binary and os.name != "nt":
            (tools / name).symlink_to(binary)
    for name in ("python", "python3", "py"):
        poison = tools / (name + (".cmd" if os.name == "nt" else ""))
        poison.write_text(
            '@echo off\n> "%AMBIENT_MARKER%" echo called\nexit /b 91\n'
            if os.name == "nt" else
            '#!/bin/sh\nprintf called > "$AMBIENT_MARKER"\nexit 91\n',
            encoding="utf-8",
        )
        poison.chmod(0o700)
    if scenario not in {"missing-uv", "admission-missing-uv"}:
        shutil.copy2(uv, tools / uv.name)
    if scenario == "missing-marker":
        (scripts / "bootstrap-python-launcher.txt").unlink()
    elif scenario == "missing-selector":
        (scripts / "_bootstrap_python" / f"select.{suffix}").unlink()
    elif scenario in {"missing-provider", "missing-capture", "admission-missing-uv"}:
        # Isolate application admission from the already-qualified bootstrap selection.
        selector = scripts / "_bootstrap_python" / f"select.{suffix}"
        selector.write_text(
            "#!/bin/sh\nprintf '%s\\n' '" + str(bootstrap).replace("'", "'\\''") + "'\n"
            if host == "bash" else "'" + str(bootstrap).replace("'", "''") + "'\n",
            encoding="utf-8",
        )
        missing = (
            scripts / "_bootstrap_python" / f"bootstrap-uv.{suffix}"
            if scenario == "missing-provider" else
            scripts / "native_capture.sh" if host == "bash" else
            scripts / "_bootstrap_python" / (
                "bootstrap-capture.ps1" if os.name == "nt" else "versioned_runtime_native.ps1"
            )
        )
        if scenario != "admission-missing-uv":
            missing.rename(missing.with_suffix(".unavailable"))
    empty = tmp_path / "unprepared store"
    empty.mkdir()
    git = shutil.which("git")
    assert git
    command_paths = [str(tools)]
    if os.name == "nt":
        command_paths.append(str(Path(git).parent))
    if host != "bash":
        command_paths.append(str(Path(executable).parent))
    env.update(
        PATH=os.pathsep.join(command_paths),
        AGENT_INDEX_HOME=str(root), AGENT_INDEX_REPO=str(tmp_path),
        TEST_PYTHON=str(python), TEST_CELL_ROOT=str(root), AGENT_INDEX_ROLE="client",
        ORIGIN_MARKER=str(tmp_path / "origin-ran"), AMBIENT_MARKER=str(tmp_path / "ambient-ran"),
        VALIDATION_MARKER=str(validation), INSTALLER_MARKER=str(tmp_path / "installer-ran"),
        UV_PYTHON="unrelated-python", UV_NO_MANAGED_PYTHON="1",
        UV_CONFIG_FILE="missing-relative.toml", UV_NO_CONFIG="false",
        VIRTUAL_ENV="unrelated", CONDA_PREFIX="unrelated", PYTHONPATH="unrelated", PYTHONHOME="unrelated",
        PYTHONDONTWRITEBYTECODE="1",
    )
    if scenario == "unprepared":
        env["UV_PYTHON_INSTALL_DIR"] = str(empty)
    if scenario == "namespaced":
        context = root / "install.json"
        context.write_text("{}\n", encoding="utf-8")
        env["COPILOT_EXTENSIONS_CONTEXT"] = str(context)
        env["TEST_INSTALLATION_STATUS"] = json.dumps({
            "status": "ready", "reason": "namespaced-active",
            "actualMode": "namespaced", "desiredMode": "namespaced",
            "context": str(context), "marketplaceId": "example--1234",
            "policy": {"state": "valid", "enabled": True},
        })
    arguments = ["search", "space value", 'quote"value', "", "end\\", "\u03bb"]
    if scenario in {"setup", "provisioned"}:
        arguments = ["setup", "--single", "--yes"]
    elif scenario == "readiness":
        arguments = ["installer-readiness"]
    command = [executable, str(gate), *arguments]
    inherited_fds = tmp_path / "inherited-fds"
    if host == "bash" and scenario == "current":
        env["INHERITED_FD_TARGET"] = str(inherited_fds)
        command = [
            executable, "-c",
            'exec 5>>"$INHERITED_FD_TARGET" 6>>"$INHERITED_FD_TARGET" '
            '7>>"$INHERITED_FD_TARGET" 8>>"$INHERITED_FD_TARGET" 9>>"$INHERITED_FD_TARGET"\n'
            '"$@"\nstatus=$?\n'
            "printf 'caller-fd-5\\n' >&5\nprintf 'caller-fd-6\\n' >&6\n"
            "printf 'caller-fd-7\\n' >&7\nprintf 'caller-fd-8\\n' >&8\n"
            "printf 'caller-fd-9\\n' >&9\n"
            'exit "$status"\n',
            "index-command-inherited-fds", *command,
        ]
    if host != "bash":
        quoted = ["'" + value.replace("'", "''") + "'" for value in arguments]
        harness = tmp_path / "invoke.ps1"
        harness.write_text(
            ("$PSNativeCommandArgumentPassing='Legacy'\n" if scenario == "current" else "")
            + "$invocation=@(" + ",".join(quoted) + ")\n"
            + "& '" + str(gate).replace("'", "''") + "' @invocation\n"
            "exit $LASTEXITCODE\n", encoding="utf-8-sig",
        )
        command = [executable, "-NoProfile", "-NonInteractive", "-File", str(harness)]
    samples = []
    successful = scenario in {
        "current", "lkg", "newest", "namespaced", "setup", "readiness", "missing-marker", "provisioned",
    }
    for _cycle in range(2 if successful else 1):
        started = time.monotonic()
        result = subprocess.run(
            command, env=env, cwd=tmp_path, input="foreground input\n",
            capture_output=True, text=True, encoding="utf-8", timeout=45,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        samples.append(time.monotonic() - started)
        assert not Path(env["AMBIENT_MARKER"]).exists(), result.stdout + result.stderr
        assert Path(env["INSTALLER_MARKER"]).exists() is (scenario == "provisioned"), result.stdout + result.stderr
        assert not list(empty.iterdir())
        if not successful:
            assert result.returncode != 0 and not result.stdout, result.stdout + result.stderr
            assert not Path(env["ORIGIN_MARKER"]).exists(), result.stdout + result.stderr
            if scenario in {"missing-provider", "missing-capture", "admission-missing-uv"}:
                assert result.returncode == 126, result.stderr
            continue
        assert result.returncode == 23, result.stdout + result.stderr
        details = json.loads(result.stdout)
        assert details == {
            "executable": str(python), "prefix": str(slot), "version": [3, 10, 20],
            "argv": arguments, "stdin": "foreground input\n",
            "isolated": 1, "bytecode": scenario == "readiness", "selector": "unrelated-python",
        }, details
        assert "application stderr" in result.stderr, result.stderr
        assert Path(env["ORIGIN_MARKER"]).is_file()
        if host == "bash" and scenario == "current":
            assert inherited_fds.read_text(encoding="utf-8").splitlines() == (
                [f"application-fd-{fd}" for fd in range(5, 10)]
                + [f"caller-fd-{fd}" for fd in range(5, 10)]
            ) * (_cycle + 1)
        if scenario == "readiness":
            assert not list(slot.rglob("*.pyc"))
    if scenario == "namespaced":
        assert json.loads(validation.read_text(encoding="utf-8")) == {
            "executable": str(bootstrap), "isolated": 1,
        }
    record_property("index_command_gate_performance", json.dumps({
        "shell": host, "scenario": scenario, "status": "measured",
        "samples_seconds": samples, "completed": len(samples), "network": "disabled",
        "bootstrap_python": "3.11.18", "application_python": "3.10.20",
    }))
