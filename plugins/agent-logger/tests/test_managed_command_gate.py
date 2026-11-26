"""Real prepared UV admission for six inert Logger commands, without sync effects."""
from __future__ import annotations

from collections.abc import Callable
import hashlib
from importlib import import_module
import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
import time
from typing import cast

import pytest

_fixture = import_module(
    ".managed_command_fixture", __package__,
) if __package__ else import_module("managed_command_fixture")
HEADLESS = cast(int, _fixture.HEADLESS)
PLUGIN = cast(Path, _fixture.PLUGIN)
REPO = cast(Path, _fixture.REPO)
TARGETS = cast(dict[str, str], _fixture.TARGETS)
isolate_context = cast(Callable[[Path], None], _fixture.isolate_context)
prepare_application = cast(
    Callable[[Path, Path, Path, dict[str, str]], Path], _fixture.prepare_application,
)
snapshot = cast(Callable[[Path], dict[str, str]], _fixture.snapshot)
packaged_command = _fixture.packaged_command
prepared_python = _fixture.prepared_python


@pytest.mark.parametrize("host", ["bash", "pwsh", "powershell"])
@pytest.mark.parametrize("scenario", [
    *TARGETS, "lkg", "newest", "namespaced",
    "missing-uv", "missing-provider", "missing-capture",
])
@pytest.mark.parametrize("materialized", [False, True], ids=["raw", "materialized"])
def test_ready_command_admits_exact_prepared_application(
    tmp_path: Path, host: str, materialized: bool, scenario: str,
    prepared_python: tuple[Path, Path, Path], packaged_command: Path,
    record_property: Callable[[str, object], None],
) -> None:
    if (host == "bash" and os.name == "nt") or (host == "powershell" and os.name != "nt"):
        pytest.skip("This control requires the addressed native shell")
    shell = shutil.which(host)
    if not shell:
        pytest.skip(f"{host} unavailable")
    uv, application, store = prepared_python
    home = tmp_path / "home"
    home.mkdir()
    legacy = home / ".agent-logger"
    root = tmp_path / "selected application's \u03bb root" if scenario == "namespaced" else legacy
    payload = tmp_path / "source/plugins/agent-logger"
    shutil.copytree(packaged_command, payload)
    scripts = payload / "scripts"
    isolate_context(payload)
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
    suffix = "sh" if host == "bash" else "ps1"
    provider = scripts / "_bootstrap_python" / f"bootstrap-uv.{suffix}"
    invocation = '    exec "$@"\n' if host == "bash" else "    $result = & $InvokeCaptured @options\n"
    observation = (
        '    printf "%s\\n" "${*:2}" >> "$LOGGER_UV_LOG"\n' if host == "bash" else
        '    [IO.File]::AppendAllText($env:LOGGER_UV_LOG, ($Arguments -join " ") + "`n")\n'
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
        digest = shutil.which("sha256sum") or shutil.which("shasum")
        assert digest
        (tools / Path(digest).name).symlink_to(digest)
    for name in ("python", "python3", "py", *TARGETS):
        poison = tools / (name + ".cmd" if os.name == "nt" else name)
        poison.write_text(
            '@echo off\n> "%AMBIENT_MARKER%" echo called\nexit /b 91\n' if os.name == "nt" else
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
    for name in list(env):
        if name.startswith(("AGENT_LOGGER_", "AGENT_RT_", "LOGGER_", "COPILOT_EXTENSIONS_",
                            "UPDATE_DRIVER_")) or name in ("BASH_ENV", "ENV", "COPILOT_PLUGIN_ROOT"):
            env.pop(name)
    slot = root / "versions/1.2.3"
    python = prepare_application(uv, application, slot, env)
    if scenario != "newest":
        (root / ("last-known-good" if scenario == "lkg" else "current-version")).write_text(
            "1.2.3\n", encoding="utf-8",
        )
    if scenario == "lkg":
        (root / "current-version").write_text("incomplete\n", encoding="utf-8")
    context = tmp_path / "home/contexts/a/b/c/d/e" if scenario == "namespaced" else None
    decision = {
        "status": "ready", "reason": "namespaced-active" if context else "policy-default-false",
        "actualMode": "namespaced" if context else "legacy",
        "desiredMode": "namespaced" if context else "legacy",
        "legacy": {"root": str(legacy)}, "runtimeRoot": "unvalidated-root",
        "context": str(context) if context else "", "installGeneration": 7,
        "marketplaceId": "test-market",
    }
    uv_log, proof, calls = tmp_path / "uv-calls", tmp_path / "application.json", tmp_path / "context-calls"
    missing = scenario.startswith("missing-")
    env.update(
        AGENT_LOGGER_HOME=str(legacy), AGENT_LOGGER_PAYLOAD_ROOT=str(payload),
        LOGGER_TEST_STATUS=json.dumps(decision), APPLICATION_PROOF=str(proof),
        LOGGER_TEST_VALIDATED=json.dumps({"generation": 7, "pluginRoot": str(root)}),
        LOGGER_CONTEXT_CALLS=str(calls), LOGGER_UV_LOG=str(uv_log),
        AMBIENT_MARKER=str(tmp_path / "ambient-ran"), AGENT_LOGGER_NO_SELFPROVISION="1",
        AGENT_LOGGER_INSTALLATION_ID="unrelated", AGENT_LOGGER_TIMER_NAME="unrelated",
        AGENT_LOGGER_TASK_NAME="unrelated",
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
    target = scenario if scenario in TARGETS else "agent-logger"
    module = TARGETS[target]
    arguments = ["inspect", "space value", 'quote"value', "", "end\\", "\u03bb", "1234", "True"]
    command = [shell, str(payload / "bin" / target), *arguments]
    descriptors = tmp_path / "inherited-fds"
    if host == "bash" and scenario == "agent-logger":
        env["INHERITED_FD_TARGET"] = str(descriptors)
        command = [
            shell, "-c",
            'exec 5>>"$INHERITED_FD_TARGET" 6>>"$INHERITED_FD_TARGET" '
            '7>>"$INHERITED_FD_TARGET" 8>>"$INHERITED_FD_TARGET" 9>>"$INHERITED_FD_TARGET"\n'
            '"$@"\ncode=$?\n'
            "for fd in 5 6 7 8 9; do printf 'caller-fd-%s\\n' \"$fd\" >&\"$fd\"; done\n"
            'exit "$code"\n', "logger-inherited-descriptors", *command,
        ]
    elif host != "bash":
        harness = tmp_path / "invoke.ps1"
        quoted = ",".join("'" + value.replace("'", "''") + "'" for value in arguments[:-2])
        harness.write_text(
            "$PSNativeCommandArgumentPassing='Legacy'\n"
            f"$port=1234\n$enabled=$true\n$forward=@({quoted}) + @($port,$enabled)\n"
            f"& '{str(payload / 'bin' / (target + '.ps1')).replace(chr(39), chr(39)*2)}' @forward\n"
            "exit $LASTEXITCODE\n", encoding="utf-8-sig",
        )
        command = [shell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(harness)]
    original_slot, original_payload = snapshot(slot), snapshot(payload)
    samples: list[float] = []
    expected_lookup = f"python find --no-project --managed-python --no-python-downloads {python}"
    for cycle in range(1 if missing else 2):
        started = time.monotonic()
        result = subprocess.run(
            command, env=env, cwd=tmp_path, input="foreground input\n",
            capture_output=True, text=True, encoding="utf-8", timeout=35, creationflags=HEADLESS,
        )
        samples.append(time.monotonic() - started)
        assert not Path(env["AMBIENT_MARKER"]).exists()
        assert "installer should not run" not in result.stderr, result.stdout + result.stderr
        assert snapshot(slot) == original_slot and snapshot(payload) == original_payload
        assert result.returncode == (126 if missing else 37), result.stdout + result.stderr
        lookups = uv_log.read_text(encoding="utf-8").splitlines() if uv_log.exists() else []
        assert lookups == ([] if missing else [expected_lookup] * (cycle + 1)), result.stdout + result.stderr
        assert calls.read_text(encoding="utf-8").splitlines() == (
            ["status", *(["validate"] if context else [])] * (cycle + 1)
        )
        if missing:
            assert "application runtime admission" in result.stderr or "uv is unavailable" in result.stderr
            assert not result.stdout and not proof.exists(), result.stdout + result.stderr
            continue
        suffix_hash = hashlib.sha256(str(root).lower().encode()).hexdigest()[:12]
        assert result.stdout.strip() == "application stdout" and "application stderr" in result.stderr
        assert json.loads(proof.read_text(encoding="utf-8")) == {
            "python": str(python), "prefix": str(slot), "version": [3, 10, 20],
            "module": module if "." in module else module + ".__main__",
            "argv": arguments, "stdin": "foreground input\n", "cwd": str(tmp_path),
            "root": str(root), "context": str(context) if context else None,
            "installation_id": "test-market/agent-logger" if context else None,
            "timer_name": f"agent-logger-sync-{suffix_hash}" if context else None,
            "task_name": f"Agent Logger Session Sync - {suffix_hash}" if context else None,
            "selector": "unrelated-python",
        }
        if host == "bash" and scenario == "agent-logger":
            assert descriptors.read_text(encoding="utf-8").splitlines() == (
                [f"application-fd-{fd}" for fd in range(5, 10)]
                + [f"caller-fd-{fd}" for fd in range(5, 10)]
            ) * (cycle + 1)
    if scenario == "agent-logger":
        blocked_env = {
            **env, "COPILOT_EXTENSIONS_PAYLOAD_COMMAND": "session-sync",
            "COPILOT_EXTENSIONS_PAYLOAD_MODULE": "agent_logger.segmenter.collate",
        }
        gate = scripts / ("runtime-gate.sh" if host == "bash" else "runtime-gate.ps1")
        denied = subprocess.run(
            [shell, *([] if host == "bash" else ["-NoProfile", "-NonInteractive", "-File"]), str(gate)],
            env=blocked_env, capture_output=True, text=True, timeout=15, creationflags=HEADLESS,
        )
        assert denied.returncode == 126 and "unsupported payload dispatch target" in denied.stderr
        assert uv_log.read_text(encoding="utf-8").splitlines() == lookups
        assert calls.read_text(encoding="utf-8").splitlines() == ["status"] * 2
    record_property("logger_command_admission", json.dumps({
        "shell": host, "scenario": scenario, "materialized": materialized,
        "samples_seconds": samples, "network": "disabled", "runtime_python": "3.10.20",
        "uv_calls": len(lookups),
    }))
