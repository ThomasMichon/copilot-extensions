"""Real prepared admission without PR, network, broker or installation effects."""
from __future__ import annotations

from collections.abc import Callable
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
isolate_selection = cast(Callable[[Path], None], _fixture.isolate_selection)
prepare_application = cast(
    Callable[[Path, Path, Path, dict[str, str]], Path], _fixture.prepare_application,
)
snapshot = cast(Callable[[Path], dict[str, str]], _fixture.snapshot)
packaged_command = _fixture.packaged_command
prepared_python = _fixture.prepared_python


@pytest.mark.parametrize("host", ["bash", "pwsh", "powershell"])
@pytest.mark.parametrize("scenario", [
    "current", "lkg", "newest", "cell", "cell-changed", "missing-uv",
    "missing-provider", "missing-capture", "cell-missing-bootstrap", "cell-missing-admission",
    "long-body", "cell-long-body",
])
@pytest.mark.parametrize("materialized", [False, True], ids=["raw", "materialized"])
def test_ready_command_admits_exact_prepared_application(
    tmp_path: Path, host: str, materialized: bool, scenario: str,
    prepared_python: tuple[Path, Path, Path, Path], packaged_command: Path,
    record_property: Callable[[str, object], None],
) -> None:
    if (host == "bash" and os.name == "nt") or (host == "powershell" and os.name != "nt"):
        pytest.skip("This control requires the addressed native shell")
    shell = shutil.which(host)
    if not shell:
        pytest.skip(f"{host} unavailable")
    uv, application, bootstrap, store = prepared_python
    home = tmp_path / "home"
    home.mkdir()
    cell = scenario.startswith("cell")
    root = tmp_path / "selected application's \u03bb root" if cell else home / ".agent-pull-requests"
    payload = tmp_path / "source/plugins/agent-pull-requests"
    shutil.copytree(packaged_command, payload)
    scripts = payload / "scripts"
    library = (payload if materialized else payload.parents[1]) / "libs/agent-procutil"
    shutil.copytree(REPO / "libs/agent-procutil/src", library / "src")
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
        (scripts / "native_capture.sh").unlink(missing_ok=True)
    isolate_selection(payload)
    if host != "bash":
        entry = scripts / "invoke-payload-runtime.ps1"
        original = entry.read_text(encoding="utf-8")
        invocation = "            if ($ScriptEntry) {\n"
        assert original.count(invocation) == 1
        entry.write_text(original.replace(
            invocation,
            '            [IO.File]::AppendAllText($env:PULL_REQUESTS_TRANSPORT_LOG, '
            '$env:COPILOT_EXTENSIONS_FORWARDED_ARGUMENTS_0 + "`n")\n' + invocation,
        ), encoding="utf-8")
    suffix = "sh" if host == "bash" else "ps1"
    provider = scripts / "_bootstrap_python" / f"bootstrap-uv.{suffix}"
    invocation = '    exec "$@"\n' if host == "bash" else "    $result = & $InvokeCaptured @options\n"
    observation = (
        '    if [[ "${2:-}" == python ]]; then printf "%s\\n" "${*:2}" >> "$PULL_REQUESTS_UV_LOG"; fi\n'
        if host == "bash" else
        '    if ($Arguments[0] -eq "python") { [IO.File]::AppendAllText($env:PULL_REQUESTS_UV_LOG, ($Arguments -join " ") + "`n") }\n'
    )
    original = provider.read_text(encoding="utf-8")
    assert original.count(invocation) == 1
    provider.write_text(original.replace(invocation, observation + invocation), encoding="utf-8")
    admission = library / "src/agent_procutil/managed_python.py"
    original = admission.read_text(encoding="utf-8")
    observation = (
        '    with open(os.environ["PULL_REQUESTS_UV_LOG"], "a", encoding="utf-8") as log:\n'
        '        log.write("python find --no-project --managed-python --no-python-downloads " + expected + "\\n")\n'
    )
    assert original.count("    result = run_caller_owned(\n") == 1
    admission.write_text(
        original.replace("    result = run_caller_owned(\n", observation + "    result = run_caller_owned(\n"),
        encoding="utf-8",
    )
    tools = tmp_path / "private tools"
    tools.mkdir()
    if os.name != "nt":
        for name in ("bash", "dirname", "awk", "tr", "sed", "date", "sort", "find",
                     "mktemp", "mkfifo", "sleep", "rm", "rmdir", "mkdir", "cat",
                     "grep", "cut", "ln", "readlink", "cksum", "wc"):
            executable = shutil.which(name)
            assert executable, name
            (tools / name).symlink_to(executable)
    for name in ("python", "python3", "py", "agent-pull-requests"):
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
        if name.startswith(("AGENT_PULL_REQUESTS_", "AGENT_RT_", "PULL_REQUESTS_", "COPILOT_EXTENSIONS_",
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
    uv_log, proof = tmp_path / "uv-calls", tmp_path / "application.json"
    transport_log = tmp_path / "transport-calls"
    bootstrap_proof = tmp_path / "bootstrap.json"
    context = root / "install.json"
    env.update(
        AGENT_PULL_REQUESTS_PAYLOAD_ROOT=str(payload),
        AGENT_PULL_REQUESTS_NO_SELFPROVISION="1",
        PULL_REQUESTS_TEST_MODE="cell" if cell else "legacy",
        PULL_REQUESTS_TEST_ROOT=str(root), PULL_REQUESTS_TEST_PYTHON=str(python),
        PULL_REQUESTS_TEST_CONTEXT=str(context), PULL_REQUESTS_TEST_SCENARIO=scenario,
        PULL_REQUESTS_UV_LOG=str(uv_log), APPLICATION_PROOF=str(proof),
        PULL_REQUESTS_TRANSPORT_LOG=str(transport_log),
        BOOTSTRAP_PROOF=str(bootstrap_proof), AMBIENT_MARKER=str(tmp_path / "ambient-ran"),
        UV_PYTHON="unrelated-python", UV_NO_MANAGED_PYTHON="1",
        UV_CONFIG_FILE="missing-relative.toml", UV_NO_CONFIG="false",
        VIRTUAL_ENV="unrelated", CONDA_PREFIX="unrelated", PYTHONPATH="unrelated", PYTHONHOME="unrelated",
        PYTHONDONTWRITEBYTECODE="1",
    )
    if scenario == "missing-provider":
        provider.rename(provider.with_suffix(".unavailable"))
    elif scenario == "missing-capture":
        captures = (
            [scripts / "native_capture.sh", library / "src/agent_procutil/native_capture.sh"]
            if host == "bash" else [scripts / "_bootstrap_python" / (
                "bootstrap-capture.ps1" if os.name == "nt" else "versioned_runtime_native.ps1"
            )]
        )
        present = [capture for capture in captures if capture.is_file()]
        assert present
        for capture in present:
            capture.rename(capture.with_suffix(".unavailable"))
    elif scenario == "cell-missing-bootstrap":
        (scripts / "bootstrap-python-version.txt").write_text("cpython@3.99.0\n", encoding="utf-8")
    elif scenario == "cell-missing-admission":
        admission.rename(admission.with_suffix(".unavailable"))
    arguments = ["inspect", "space value", 'quote"value', "", "end\\", "\u03bb", "1234", "True"]
    if scenario.endswith("long-body"):
        arguments[-2:-2] = ["--body", "x" * 8000 + "\U0001f642" * 1000 + "y" * 16000]
    elif scenario in ("newest", "cell"):
        arguments = []
    command = [shell, str(payload / "bin/agent-pull-requests"), *arguments]
    descriptors = tmp_path / "inherited-fds"
    if host == "bash" and scenario == "current":
        env["INHERITED_FD_TARGET"] = str(descriptors)
        command = [
            shell, "-c",
            'exec 5>>"$INHERITED_FD_TARGET" 6>>"$INHERITED_FD_TARGET" '
            '7>>"$INHERITED_FD_TARGET" 8>>"$INHERITED_FD_TARGET" 9>>"$INHERITED_FD_TARGET"\n'
            '"$@"\ncode=$?\n'
            "for fd in 5 6 7 8 9; do printf 'caller-fd-%s\\n' \"$fd\" >&\"$fd\"; done\n"
            'exit "$code"\n', "pull-requests-inherited-descriptors", *command,
        ]
    elif host != "bash":
        harness = tmp_path / "invoke.ps1"
        quoted = ",".join("'" + value.replace("'", "''") + "'" for value in arguments[:-2])
        values = (
            f"$port=1234\n$enabled=$true\n$forward=@({quoted}) + @($port,$enabled)\n"
            if arguments else "$forward=@()\n"
        )
        harness.write_text(
            "$PSNativeCommandArgumentPassing='Legacy'\n"
            "$env:COPILOT_EXTENSIONS_FORWARDED_ARGUMENTS_0='caller-owned'\n"
            "$env:COPILOT_EXTENSIONS_FORWARDED_ARGUMENTS_sibling_0='other-command'\n"
            + values +
            f"& '{str(payload / 'bin/agent-pull-requests.ps1').replace(chr(39), chr(39)*2)}' @forward\n"
            "$code=$LASTEXITCODE\n"
            "if ($env:COPILOT_EXTENSIONS_FORWARDED_ARGUMENTS_0 -cne 'caller-owned') { throw 'caller argument environment changed' }\n"
            "if ($env:COPILOT_EXTENSIONS_FORWARDED_ARGUMENTS_sibling_0 -cne 'other-command') { throw 'sibling argument environment changed' }\n"
            "exit $code\n", encoding="utf-8-sig",
        )
        command = [shell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(harness)]
    original_slot, original_payload = snapshot(slot), snapshot(payload)
    positive = scenario in ("current", "lkg", "newest", "cell", "long-body", "cell-long-body")
    application_lookup = f"python find --no-project --managed-python --no-python-downloads {python}"
    bootstrap_lookup = (
        "python find --no-project --system --managed-python --no-python-downloads "
        + ("cpython@3.99.0" if scenario == "cell-missing-bootstrap" else "cpython@3.11.18")
    )
    expected_lookups = (
        [bootstrap_lookup] + ([] if scenario in ("cell-missing-bootstrap", "cell-missing-admission") else [application_lookup])
        if cell else ([application_lookup] if positive else [])
    )
    samples: list[float] = []
    for cycle in range(2 if positive else 1):
        started = time.monotonic()
        result = subprocess.run(
            command, env=env, cwd=tmp_path, input="foreground input\n",
            capture_output=True, text=True, encoding="utf-8", timeout=35, creationflags=HEADLESS,
        )
        samples.append(time.monotonic() - started)
        assert not Path(env["AMBIENT_MARKER"]).exists()
        assert snapshot(slot) == original_slot and snapshot(payload) == original_payload
        assert result.returncode == (37 if positive else 126), result.stdout + result.stderr
        lookups = uv_log.read_text(encoding="utf-8").splitlines() if uv_log.exists() else []
        assert lookups == expected_lookups * (cycle + 1), result.stdout + result.stderr
        if host != "bash":
            transports = transport_log.read_text(encoding="utf-8").splitlines() if transport_log.exists() else []
            expected_transports = (
                cycle + 1 if positive or scenario in ("cell-changed", "cell-missing-admission") else 0
            )
            assert transports == ["caller-owned"] * expected_transports
        if not positive:
            assert result.stderr and not result.stdout and not proof.exists(), result.stdout + result.stderr
            continue
        assert result.stdout.strip() == "application stdout" and "application stderr" in result.stderr
        assert json.loads(proof.read_text(encoding="utf-8")) == {
            "python": str(python), "prefix": str(slot), "version": [3, 10, 20],
            "argv": arguments, "stdin": "foreground input\n", "cwd": str(tmp_path),
            "root": str(root), "context": str(context) if cell else None,
            "selector": "unrelated-python", "transport_keys": [],
        }
        if cell:
            assert json.loads(bootstrap_proof.read_text(encoding="utf-8")) == {
                "python": str(bootstrap), "version": [3, 11, 18], "argv": ["--", *arguments],
            }
        if host == "bash" and scenario == "current":
            assert descriptors.read_text(encoding="utf-8").splitlines() == (
                [f"application-fd-{fd}" for fd in range(5, 10)]
                + [f"caller-fd-{fd}" for fd in range(5, 10)]
            ) * (cycle + 1)
    record_property("pull_requests_command_admission", json.dumps({
        "shell": host, "scenario": scenario, "materialized": materialized,
        "samples_seconds": samples, "network": "disabled", "runtime_python": "3.10.20",
        "bootstrap_python": "3.11.18" if cell else None, "uv_calls": len(lookups),
    }))
