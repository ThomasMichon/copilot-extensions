"""Exercise prepared Worktrees admission without workspace or terminal effects."""
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
    "module", "lkg", "newest", "namespaced", "installer-readiness", "invocation-contract",
    "cache-policy", "prune",
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
    root = tmp_path / "selected application's \u03bb root" if scenario == "namespaced" else home / ".agent-worktrees"
    payload = tmp_path / "source/plugins/agent-worktrees"
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
        'printf "%s\\n" "$WORKTREES_TEST_STATUS"\n', encoding="utf-8", newline="\n",
    )
    (scripts / "installation-context/installation-context.ps1").write_text(
        "Write-Output $env:WORKTREES_TEST_STATUS\nexit 0\n", encoding="utf-8",
    )
    for suffix in ("sh", "ps1"):
        (scripts / f"install.{suffix}").write_text(
            "echo 'installer should not run' >&2\nexit 98\n" if suffix == "sh" else
            "throw 'installer should not run'\n", encoding="utf-8", newline="\n",
        )
    suffix = "sh" if host == "bash" else "ps1"
    provider = scripts / "_bootstrap_python" / f"bootstrap-uv.{suffix}"
    invocation = '    exec "$@"\n' if host == "bash" else "    $result = & $InvokeCaptured @options\n"
    observation = (
        '    printf "%s\\n" "${*:2}" >> "$WORKTREES_UV_LOG"\n' if host == "bash" else
        '    [IO.File]::AppendAllText($env:WORKTREES_UV_LOG, ($Arguments -join " ") + "`n")\n'
    )
    original = provider.read_text(encoding="utf-8")
    assert original.count(invocation) == 1
    provider.write_text(
        original.replace(invocation, observation + invocation), encoding="utf-8", newline="\n",
    )
    tools = tmp_path / "private tools"
    tools.mkdir()
    if os.name != "nt":
        for name in ("bash", "dirname", "awk", "tr", "sed", "date", "sort", "find", "wc",
                     "mktemp", "mkfifo", "sleep", "rm", "rmdir", "mkdir", "cat",
                     "grep", "cut", "ln", "readlink", "cksum", "id",
                     *(("dscl",) if sys.platform == "darwin" else ())):
            executable = shutil.which(name)
            assert executable, name
            (tools / name).symlink_to(executable)
    for name in ("python", "python3", "py", "agent-worktrees"):
        poison = tools / (name + ".cmd" if os.name == "nt" else name)
        poison.write_text(
            '@echo off\n> "%AMBIENT_MARKER%" echo called\nexit /b 91\n' if os.name == "nt" else
            '#!/bin/sh\nprintf called > "$AMBIENT_MARKER"\nexit 91\n',
            encoding="utf-8", newline="\n",
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
        if name.startswith(("AGENT_WORKTREES_", "AGENT_RT_", "WORKTREES_", "COPILOT_EXTENSIONS_",
                            "UPDATE_DRIVER_")) or name in (
                                "BASH_ENV", "ENV", "COPILOT_PLUGIN_ROOT", "PYTHONHOME",
                                "PYTHONDONTWRITEBYTECODE", "PYTHONPYCACHEPREFIX",
                            ):
            env.pop(name)
    slot = root / "versions/1.2.3"
    python = prepare_application(uv, application, slot, env)
    site = slot / ("Lib/site-packages" if os.name == "nt" else "lib/python3.10/site-packages")
    package = site / "agent_worktrees"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "__main__.py").write_text(
        "import json,os,sys\nfrom pathlib import Path\n"
        "value={'python':sys.executable,'prefix':sys.prefix,'version':list(sys.version_info[:3]),"
        "'argv':sys.argv[1:],'stdin':sys.stdin.read(),'cwd':os.getcwd(),"
        "'isolated':sys.flags.isolated,'no_bytecode':sys.dont_write_bytecode,"
        "'cache':sys.pycache_prefix,"
        "'context':os.environ.get('COPILOT_EXTENSIONS_CONTEXT'),"
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
    cache = tmp_path / "application cache" if scenario == "cache-policy" else None
    if cache:
        support = site / "agent_procutil"
        support.mkdir()
        (support / "__init__.py").write_text("", encoding="utf-8")
        (support / "python_cache.py").write_text(
            "POLICY_VERSION=2\n"
            f"def policy_arguments(root, runtime): return ['-X', {('pycache_prefix=' + str(cache))!r}]\n",
            encoding="utf-8",
        )
    declaration = slot / "share/copilot-extensions/agent_worktrees/readiness-isolation.json"
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
    prune = tmp_path / "prune-launch.json"
    env.update(
        WORKTREES_TEST_STATUS=json.dumps({
            "status": "ready", "reason": "namespaced-active" if context else "policy-default-false",
            "actualMode": "namespaced" if context else "legacy",
            "desiredMode": "namespaced" if context else "legacy",
            "runtimeRoot": str(root), "context": str(context) if context else "",
            "activationGeneration": "activation", "installGeneration": "installation",
            "contextValidation": {"namespaceGeneration": "namespace", "generation": "installation"},
            "namespaceGeneration": "namespace", "generation": "installation",
        }),
        AGENT_WORKTREES_NO_SELFPROVISION="1", APPLICATION_PROOF=str(proof),
        WORKTREES_UV_LOG=str(uv_log), WORKTREES_PRUNE_PROOF=str(prune),
        AMBIENT_MARKER=str(tmp_path / "ambient-ran"),
        UV_PYTHON="unrelated-python", UV_NO_MANAGED_PYTHON="1",
        UV_CONFIG_FILE="missing-relative.toml", UV_NO_CONFIG="false",
        VIRTUAL_ENV="unrelated", CONDA_PREFIX="unrelated",
    )
    if context:
        env["COPILOT_EXTENSIONS_CONTEXT"] = str(context)
    if scenario == "prune" or scenario.startswith("missing-"):
        log = root / "logs/activity.jsonl"
        log.parent.mkdir(parents=True)
        log.write_text("x" * 524288, encoding="utf-8")
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
    probe = scenario in ("installer-readiness", "invocation-contract")
    arguments = [scenario if probe else "inspect", "space value", "\u03bb", "1234", "True"]
    entry = payload / "bin/payload/agent-worktrees"
    if host == "bash":
        setup = (
            'export HOME="$(cd "$FIXTURE_HOME" && pwd)"\n'
            'getent() { printf "fixture:x:%s:0::%s:/bin/bash\\n" "$(id -u)" "$HOME"; }\n'
            'export -f getent\n'
            'setsid() { printf "%s\\n%s\\n" "$*" "$(wc -l < "$WORKTREES_UV_LOG")" > "$WORKTREES_PRUNE_PROOF"; }\n'
            'export -f setsid\n'
        )
        env["FIXTURE_HOME"] = str(home)
        harness = tmp_path / "invoke.sh"
        harness.write_text(
            setup + "exec bash " + shlex.join([entry.as_posix(), *arguments]) + "\n",
            encoding="utf-8", newline="\n",
        )
        command = [shell, harness.as_posix()]
    else:
        harness = tmp_path / "invoke.ps1"
        quoted = ",".join("'" + value.replace("'", "''") + "'" for value in arguments)
        harness.write_text(
            "$PSNativeCommandArgumentPassing='Standard'\n"
            "function Start-Process { param($FilePath,$ArgumentList,$WindowStyle,$ErrorAction)\n"
            " @{executable=$FilePath;arguments=$ArgumentList;uv_calls=@([IO.File]::ReadAllLines($env:WORKTREES_UV_LOG)).Count} | "
            "ConvertTo-Json -Compress | Set-Content -LiteralPath $env:WORKTREES_PRUNE_PROOF }\n"
            f"$forward=@({quoted})\n"
            f"& '{str(entry.with_suffix('.ps1')).replace(chr(39), chr(39)*2)}' @forward\n"
            "exit $LASTEXITCODE\n", encoding="utf-8-sig",
        )
        command = [shell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(harness)]
    before_slot, before_payload = snapshot(slot), snapshot(payload)
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
            f"python find --no-project --managed-python --no-python-downloads {python}",
        ] * (cycle + 1)), result.stdout + result.stderr
        if missing:
            assert "application runtime admission" in result.stderr or "uv is unavailable" in result.stderr
            assert not result.stdout and not proof.exists() and not prune.exists()
            assert not list((root / "logs").glob("*.prune-marker.*"))
            continue
        assert result.stdout.strip() == "application stdout" and "application stderr" in result.stderr
        assert json.loads(proof.read_text(encoding="utf-8")) == {
            "python": str(python), "prefix": str(slot), "version": [3, 10, 20],
            "argv": arguments, "stdin": "foreground input\n", "cwd": str(tmp_path),
            "context": str(context) if context else None, "selector": "unrelated-python",
            "isolated": int(probe), "no_bytecode": not bool(cache), "cache": str(cache) if cache else None,
        }
        if scenario == "prune":
            deadline = time.monotonic() + 5
            while not prune.exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            assert prune.exists()
            if host == "bash":
                launch, calls = prune.read_text(encoding="utf-8").splitlines()
                assert launch.startswith(str(python) + " -I -m agent_worktrees activity-prune-worker ")
                assert calls.strip() == "1"
            else:
                launched = json.loads(prune.read_text(encoding="utf-8-sig"))
                assert launched["executable"] == "conhost.exe" and launched["uv_calls"] == 1
                assert launched["arguments"][:6] == [
                    "--headless", f'"{python}"', "-I", "-m", "agent_worktrees", "activity-prune-worker",
                ]
    record_property("worktrees_command_admission", json.dumps({
        "shell": host, "scenario": scenario, "materialized": materialized,
        "samples_seconds": samples, "network": "disabled", "runtime_python": "3.10.20",
        "uv_calls": len(lookups),
    }))
