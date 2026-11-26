"""Real prepared UV admission without machine, health-resource or scheduler effects."""
from __future__ import annotations

from collections.abc import Callable
import json
import os
from pathlib import Path
import runpy
import shlex
import shutil
import subprocess
import sys
import time

import pytest

from .managed_command_fixture import (
    HEADLESS, PLUGIN, REPO, bash_host, packaged_command, prepare_application, prepared_python, snapshot,
)

__all__ = ["packaged_command", "prepared_python"]


@pytest.mark.parametrize("host", ["bash", "pwsh", "powershell"])
@pytest.mark.parametrize("scenario", [
    "module", "configuration", "health-run", "health-prepare", "health-job",
    "installer-readiness", "invocation-contract", "lkg", "newest", "namespaced",
    "missing-uv", "missing-provider", "missing-capture",
])
@pytest.mark.parametrize("materialized", [False, True], ids=["raw", "materialized"])
def test_ready_command_admits_exact_prepared_application(
    tmp_path: Path, host: str, scenario: str, materialized: bool,
    prepared_python: tuple[Path, Path, Path], packaged_command: Path,
    record_property: Callable[[str, object], None],
) -> None:
    if host == "powershell" and os.name != "nt":
        pytest.skip("This control requires the addressed native shell")
    shell = bash_host() if host == "bash" else shutil.which(host)
    if not shell:
        pytest.skip(f"{host} unavailable")
    uv, application, store = prepared_python
    home = tmp_path / "home"
    home.mkdir()
    root = tmp_path / "selected application's \u03bb root" if scenario == "namespaced" else home / ".agent-machines"
    payload = tmp_path / "source/plugins/agent-machines"
    shutil.copytree(packaged_command, payload)
    scripts = payload / "scripts"
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
        capture = payload.parents[1] / "libs/agent-procutil/src/agent_procutil/native_capture.sh"
        capture.parent.mkdir(parents=True)
        shutil.copy2(REPO / "libs/agent-procutil/src/agent_procutil/native_capture.sh", capture)
        (scripts / "native_capture.sh").unlink(missing_ok=True)
    (scripts / "installation-context/installation-context.sh").write_text(
        'printf "%s\\n" "$MACHINES_TEST_STATUS"\n', encoding="utf-8",
    )
    (scripts / "installation-context/installation-context.ps1").write_text(
        "Write-Output $env:MACHINES_TEST_STATUS\nexit 0\n", encoding="utf-8",
    )
    (scripts / "init.sh").write_text("echo 'installer should not run' >&2\nexit 98\n", encoding="utf-8")
    (scripts / "init.ps1").write_text("throw 'installer should not run'\n", encoding="utf-8")
    suffix = "sh" if host == "bash" else "ps1"
    provider = scripts / "_bootstrap_python" / f"bootstrap-uv.{suffix}"
    invocation = '    exec "$@"\n' if host == "bash" else "    $result = & $InvokeCaptured @options\n"
    observation = (
        '    printf "%s\\n" "${*:2}" >> "$MACHINES_UV_LOG"\n' if host == "bash" else
        '    [IO.File]::AppendAllText($env:MACHINES_UV_LOG, ($Arguments -join " ") + "`n")\n'
    )
    original = provider.read_text(encoding="utf-8")
    assert original.count(invocation) == 1
    provider.write_text(original.replace(invocation, observation + invocation), encoding="utf-8")
    tools = tmp_path / "private tools"
    tools.mkdir()
    if os.name != "nt":
        for name in ("bash", "dirname", "awk", "tr", "sed", "date", "sort", "find",
                     "mktemp", "mkfifo", "sleep", "rm", "rmdir", "mkdir", "cat",
                     "grep", "cut", "ln", "readlink", "cksum", "id",
                     *(("dscl",) if sys.platform == "darwin" else ())):
            executable = shutil.which(name)
            assert executable, name
            (tools / name).symlink_to(executable)
    for name in ("python", "python3", "py", "agent-machines"):
        native_cmd = os.name == "nt" and host != "bash"
        poison = tools / (name + ".cmd" if native_cmd else name)
        poison.write_text(
            '@echo off\n> "%AMBIENT_MARKER%" echo called\nexit /b 91\n' if native_cmd else
            '#!/bin/sh\nprintf called > "$AMBIENT_MARKER"\nexit 91\n', encoding="utf-8",
        )
        poison.chmod(0o700)
    if scenario != "missing-uv":
        shutil.copy2(uv, tools / uv.name)
    env = {
        **os.environ, "HOME": str(home), "USERPROFILE": str(home), "PATH": str(tools),
        "UV_PYTHON_INSTALL_DIR": str(store), "UV_CACHE_DIR": str(tmp_path / "uv-cache"),
        "UV_OFFLINE": "1", "UV_NO_CONFIG": "1", "UV_PYTHON_DOWNLOADS": "never",
    }
    if host == "bash" and os.name == "nt":
        git = shutil.which("git")
        assert git is not None
        git_root = Path(git).resolve().parents[1]
        env["PATH"] = os.pathsep.join((
            str(tools), str(git_root / "usr/bin"), str(git_root / "bin"),
        ))
    for name in list(env):
        if name.startswith(("AGENT_MACHINES_", "AGENT_RT_", "MACHINES_", "COPILOT_EXTENSIONS_",
                            "UPDATE_DRIVER_")) or name in ("BASH_ENV", "ENV", "COPILOT_PLUGIN_ROOT"):
            env.pop(name)
    slot = root / "versions/1.2.3"
    python = prepare_application(uv, application, slot, env)
    site = slot / ("Lib/site-packages" if os.name == "nt" else "lib/python3.10/site-packages")
    package = site / "agent_machines"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "__main__.py").write_text(
        "import json,os,sys\nfrom pathlib import Path\n"
        "if os.environ.get('INHERITED_FD_TARGET'):\n"
        " for fd in range(5,10): os.write(fd,('application-fd-%s\\n'%fd).encode())\n"
        "value={'python':sys.executable,'prefix':sys.prefix,'version':list(sys.version_info[:3]),"
        "'argv':sys.argv[1:],'stdin':sys.stdin.read(),'cwd':os.getcwd(),"
        "'isolated':sys.flags.isolated,'no_bytecode':sys.dont_write_bytecode,"
        "'utf8':sys.flags.utf8_mode,'context':os.environ.get('COPILOT_EXTENSIONS_CONTEXT'),"
        "'selector':os.environ.get('UV_PYTHON')}\n"
        "Path(os.environ['APPLICATION_PROOF']).write_text(json.dumps(value),encoding='utf-8')\n"
        "print('application stdout')\nprint('application stderr',file=sys.stderr)\nraise SystemExit(37)\n",
        encoding="utf-8",
    )
    compiled = subprocess.run(
        [str(python), "-I", "-c",
         "import compileall,sys;sys.exit(not compileall.compile_dir(sys.argv[1],quiet=1))", str(package)],
        env=env, capture_output=True, text=True, timeout=15, creationflags=HEADLESS,
    )
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    (package / "health-collection-v1.json").write_text(json.dumps({
        "schema": "agent-machines.health.collection", "version": 1,
        "operations": ["preview", "apply", "prepare-job", "installed-job"],
    }), encoding="utf-8")
    declaration = slot / "share/copilot-extensions/agent_machines/readiness-isolation.json"
    declaration.parent.mkdir(parents=True)
    shutil.copy2(payload / "readiness-isolation.json", declaration)
    (slot / ".install-complete.json").write_text(
        json.dumps({"version": "1.2.3", "completed_at": "fixture", "pid": 0}), encoding="utf-8",
    )
    if scenario != "newest":
        (root / ("last-known-good" if scenario == "lkg" else "current-version")).write_text(
            "1.2.3\n", encoding="utf-8",
        )
    if scenario == "lkg":
        (root / "current-version").write_text("incomplete\n", encoding="utf-8")
    context = tmp_path / "home/contexts/a/b/c/d/e" if scenario == "namespaced" else None
    proof, uv_log = tmp_path / "application.json", tmp_path / "uv-calls"
    env.update(
        MACHINES_TEST_STATUS=json.dumps({
            "status": "ready", "reason": "namespaced-active" if context else "policy-default-false",
            "actualMode": "namespaced" if context else "legacy",
            "desiredMode": "namespaced" if context else "legacy",
            "runtimeRoot": root.as_posix() if host == "bash" else str(root),
            "context": str(context) if context else "",
            "marketplaceId": "test-market",
        }),
        AGENT_MACHINES_NO_SELFPROVISION="1", APPLICATION_PROOF=str(proof),
        MACHINES_UV_LOG=uv_log.as_posix() if host == "bash" else str(uv_log),
        AMBIENT_MARKER=(tmp_path / "ambient-ran").as_posix(),
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
    prefix = {
        "configuration": ["installation-configuration"], "health-run": ["health", "run"],
        "health-prepare": ["health", "prepare-job"], "health-job": ["health", "job"],
        "installer-readiness": ["installer-readiness"], "invocation-contract": ["invocation-contract"],
    }.get(scenario, ["inspect"])
    arguments = [*prefix, "space value", 'quote"value', "", "end\\", "\u03bb"]
    command = [shell, (payload / "bin/agent-machines").as_posix(), *arguments]
    descriptors = tmp_path / "inherited-fds"
    if host == "bash":
        env["FIXTURE_HOME"] = home.as_posix()
        setup = (
            'export HOME="$(cd "$FIXTURE_HOME" && pwd)"\n'
            'getent() { printf "fixture:x:%s:0::%s:/bin/bash\\n" "$(id -u)" "$HOME"; }\n'
            'export -f getent\n'
        )
        if os.name == "nt":
            harness = tmp_path / "invoke.sh"
            harness.write_text(
                setup + "exec bash " + shlex.join(command[1:]) + "\n", encoding="utf-8",
            )
            command = [shell, harness.as_posix()]
        else:
            command = [
                shell, "-c", setup + 'exec bash "$@"', "machines-fixture", *command[1:],
            ]
    else:
        harness = tmp_path / "invoke.ps1"
        quoted = ",".join("'" + value.replace("'", "''") + "'" for value in arguments)
        harness.write_text(
            "$PSNativeCommandArgumentPassing='Legacy'\n"
            f"$forward=@({quoted})\n& '{str(payload / 'bin/agent-machines.ps1').replace(chr(39), chr(39)*2)}' @forward\n"
            "exit $LASTEXITCODE\n", encoding="utf-8-sig",
        )
        command = [shell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(harness)]
    if host == "bash" and os.name != "nt" and scenario == "module":
        env["INHERITED_FD_TARGET"] = str(descriptors)
        command = [
            shell, "-c",
            'exec 5>>"$INHERITED_FD_TARGET" 6>>"$INHERITED_FD_TARGET" '
            '7>>"$INHERITED_FD_TARGET" 8>>"$INHERITED_FD_TARGET" 9>>"$INHERITED_FD_TARGET"\n'
            '"$@"\ncode=$?\n'
            "for fd in 5 6 7 8 9; do printf 'caller-fd-%s\\n' \"$fd\" >&\"$fd\"; done\n"
            'exit "$code"\n', "machines-inherited-descriptors", *command,
        ]
    before_slot, before_payload = snapshot(slot), snapshot(payload)
    lookup_python = str(python)
    samples: list[float] = []
    missing = scenario.startswith("missing-")
    for cycle in range(1 if missing else 2):
        started = time.monotonic()
        result = subprocess.run(
            command, env=env, cwd=tmp_path, input="foreground input\n",
            capture_output=True, text=True, encoding="utf-8", timeout=35, creationflags=HEADLESS,
        )
        samples.append(time.monotonic() - started)
        assert not Path(env["AMBIENT_MARKER"]).exists()
        assert "installer should not run" not in result.stderr, result.stdout + result.stderr
        assert snapshot(slot) == before_slot and snapshot(payload) == before_payload
        assert result.returncode == (126 if missing else 37), result.stdout + result.stderr
        lookups = uv_log.read_text(encoding="utf-8").splitlines() if uv_log.exists() else []
        assert lookups == ([] if missing else [
            f"python find --no-project --managed-python --no-python-downloads {lookup_python}",
        ] * (cycle + 1)), result.stdout + result.stderr
        if missing:
            assert "application runtime admission" in result.stderr or "uv is unavailable" in result.stderr
            assert not result.stdout and not proof.exists()
            continue
        isolated = scenario.startswith("health-") or scenario in ("installer-readiness", "invocation-contract")
        probe = scenario in ("installer-readiness", "invocation-contract")
        assert result.stdout.strip() == "application stdout" and "application stderr" in result.stderr
        assert json.loads(proof.read_text(encoding="utf-8")) == {
            "python": str(python), "prefix": str(slot), "version": [3, 10, 20],
            "argv": arguments, "stdin": "foreground input\n", "cwd": str(tmp_path),
            "context": str(context) if context else None, "selector": "unrelated-python",
            "isolated": int(isolated), "no_bytecode": not isolated or probe,
            "utf8": int(not isolated or probe),
        }
        if host == "bash" and os.name != "nt" and scenario == "module":
            assert descriptors.read_text(encoding="utf-8").splitlines() == (
                [f"application-fd-{fd}" for fd in range(5, 10)]
                + [f"caller-fd-{fd}" for fd in range(5, 10)]
            ) * (cycle + 1)
    record_property("machines_command_admission", json.dumps({
        "shell": host, "scenario": scenario, "materialized": materialized,
        "samples_seconds": samples, "network": "disabled", "runtime_python": "3.10.20",
        "uv_calls": len(lookups),
    }))
