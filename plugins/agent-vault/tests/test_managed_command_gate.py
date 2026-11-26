"""Real prepared UV admission before Vault foreground entry and pinned validation."""
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

_fixture = import_module(".managed_command_fixture", __package__)
HEADLESS = cast(int, _fixture.HEADLESS)
PLUGIN = cast(Path, _fixture.PLUGIN)
REPO = cast(Path, _fixture.REPO)
isolate_context = cast(Callable[[Path, str], None], _fixture.isolate_context)
prepare_application = cast(
    Callable[[Path, Path, Path, dict[str, str]], Path], _fixture.prepare_application,
)
snapshot = cast(Callable[[Path], dict[str, str]], _fixture.snapshot)
packaged_command = _fixture.packaged_command
prepared_python = _fixture.prepared_python

SCENARIOS = (
    "current", "lkg", "newest", "namespaced", "configuration-root", "pinned",
    "pinned-rejected", "readiness", "pinned-readiness", "contract",
    "missing-uv", "missing-provider", "missing-capture",
)


@pytest.mark.parametrize("host", ["bash", "pwsh", "powershell"])
@pytest.mark.parametrize("materialized", [False, True], ids=["raw", "materialized"])
@pytest.mark.parametrize("scenario", SCENARIOS)
def test_ready_command_admits_before_application_and_pin_validation(
    tmp_path: Path, host: str, materialized: bool, scenario: str,
    prepared_python: tuple[Path, Path, Path, Path], packaged_command: Path,
    record_property: Callable[[str, object], None],
) -> None:
    if (host == "bash") == (os.name == "nt"):
        pytest.skip("This control requires the addressed native shell")
    shell = shutil.which(host)
    if not shell:
        pytest.skip(f"{host} unavailable")
    uv, bootstrap, application, store = prepared_python
    home = tmp_path / "home"
    home.mkdir()
    legacy = home / ".agent-vault"
    root = tmp_path / "selected application's \u03bb root" if scenario == "namespaced" else legacy
    payload = tmp_path / "source/plugins/agent-vault"
    shutil.copytree(packaged_command, payload)
    scripts = payload / "scripts"
    isolate_context(payload, host)
    suffix = "sh" if host == "bash" else "ps1"
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
    provider = scripts / "_bootstrap_python" / f"bootstrap-uv.{suffix}"
    invocation = '    exec "$@"\n' if host == "bash" else "    $result = & $InvokeCaptured @options\n"
    observation = (
        '    printf "%s\\n" "${*:2}" >> "$VAULT_UV_LOG"\n' if host == "bash" else
        '    [IO.File]::AppendAllText($env:VAULT_UV_LOG, ($Arguments -join " ") + "`n")\n'
    )
    original = provider.read_text(encoding="utf-8")
    assert original.count(invocation) == 1
    provider.write_text(original.replace(invocation, observation + invocation), encoding="utf-8")
    tools = tmp_path / "private tools"
    tools.mkdir()
    if os.name != "nt":
        for name in ("bash", "dirname", "awk", "tr", "sed", "date", "sort", "find",
                     "mktemp", "mkfifo", "sleep", "rm", "rmdir", "mkdir", "cat",
                     "grep", "cut", "flock", "ln", "readlink", "cksum"):
            executable = shutil.which(name)
            if name == "flock" and sys.platform == "darwin":
                continue
            assert executable, name
            (tools / name).symlink_to(executable)
    for name in ("python", "python3", "py"):
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
        if name.startswith(("AGENT_VAULT_", "VAULT_", "COPILOT_EXTENSIONS_")) or name in (
            "BASH_ENV", "ENV", "KPDB", "_AGENT_WORKTREES_VAULT_RUNTIME_GUARD",
        ):
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
    identity = "example/agent-vault" if context else ""
    decision = {
        "status": "ready", "reason": "namespaced-active",
        "actualMode": "namespaced" if context else "legacy",
        "desiredMode": "namespaced" if context else "legacy",
        "legacy": {"root": str(root)}, "marketplaceId": "example",
        "runtimeRoot": str(root), "context": str(context) if context else "",
        "installGeneration": 7,
    }
    uv_log, proof = tmp_path / "uv-calls", tmp_path / "application.json"
    validation = tmp_path / "pin-validation"
    env.update(
        AGENT_VAULT_HOME=str(legacy), AGENT_VAULT_PAYLOAD_ROOT=str(payload),
        VAULT_TEST_STATUS=json.dumps(decision), APPLICATION_PROOF=str(proof),
        VAULT_VALIDATION_PROOF=str(validation), VAULT_UV_LOG=str(uv_log),
        AMBIENT_MARKER=str(tmp_path / "ambient-ran"), AGENT_VAULT_NO_SELFPROVISION="1",
        UV_PYTHON="unrelated-python", UV_NO_MANAGED_PYTHON="1",
        UV_CONFIG_FILE="missing-relative.toml", UV_NO_CONFIG="false",
        VIRTUAL_ENV="unrelated", CONDA_PREFIX="unrelated", PYTHONPATH="unrelated", PYTHONHOME="unrelated",
        PYTHONDONTWRITEBYTECODE="1",
    )
    if context:
        env["COPILOT_EXTENSIONS_CONTEXT"] = str(context)
    pinned = scenario.startswith(("pinned", "missing-"))
    cache: str | None = str(root / "cache") if context else None
    if pinned:
        database = tmp_path / "synthetic database.kdbx"
        document = {"version": 1, "database": os.path.normcase(str(database.resolve())),
                    "home": os.path.normcase(str(root.resolve())), "installation_id": identity}
        digest = hashlib.sha256(json.dumps(document, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        cache = str((root / "cache/identity" / digest).resolve())
        env.update(
            AGENT_VAULT_PINNED_CONTEXT=json.dumps({
                "version": 1, "kpdb": str(database), "group": "", "vault_name": "", "port": 1234,
            }),
            AGENT_VAULT_CACHE="1", AGENT_VAULT_CACHE_DIR=cache,
        )
        if scenario == "pinned-rejected":
            env["AGENT_VAULT_CACHE_DIR"] = str(root / "cache/foreign")
    if scenario == "missing-provider":
        provider.rename(provider.with_suffix(".unavailable"))
    elif scenario == "missing-capture":
        capture = (
            scripts / "native_capture.sh" if materialized else
            payload.parents[1] / "libs/agent-procutil/src/agent_procutil/native_capture.sh"
        ) if host == "bash" else scripts / "_bootstrap_python" / "bootstrap-capture.ps1"
        capture.rename(capture.with_suffix(".unavailable"))
    arguments = ["installer-readiness"] if "readiness" in scenario else (
        ["invocation-contract", "--json"] if scenario == "contract" else
        ["call", "space value", 'quote"value', "", "end\\", "\u03bb"]
    )
    if scenario == "configuration-root":
        arguments = ["--configuration-root", str(home), *arguments]
    command = [shell, str(payload / "bin/agent-vault"), *arguments]
    descriptors = tmp_path / "inherited-fds"
    if host == "bash" and scenario == "current":
        env["INHERITED_FD_TARGET"] = str(descriptors)
        command = [
            shell, "-c",
            'exec 5>>"$INHERITED_FD_TARGET" 6>>"$INHERITED_FD_TARGET" '
            '7>>"$INHERITED_FD_TARGET" 8>>"$INHERITED_FD_TARGET" 9>>"$INHERITED_FD_TARGET"\n'
            '"$@"\ncode=$?\n'
            "for fd in 5 6 7 8 9; do printf 'caller-fd-%s\\n' \"$fd\" >&\"$fd\"; done\n"
            'exit "$code"\n', "vault-inherited-descriptors", *command,
        ]
    elif host != "bash":
        harness = tmp_path / "invoke.ps1"
        quoted = ",".join("'" + value.replace("'", "''") + "'" for value in arguments)
        harness.write_text(
            "$PSNativeCommandArgumentPassing='Legacy'\n"
            f"$forward=@({quoted})\n& '{str(payload / 'bin/agent-vault.ps1').replace(chr(39), chr(39)*2)}' @forward\n"
            "exit $LASTEXITCODE\n", encoding="utf-8-sig",
        )
        command = [shell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(harness)]
    compiled = subprocess.run(
        [str(application), "-I", "-m", "compileall", "-q",
         str(slot / ("Lib/site-packages" if os.name == "nt" else "lib/python3.10/site-packages")),
         str(payload / "src")],
        env=env, capture_output=True, text=True, timeout=15, creationflags=HEADLESS,
    )
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    original_slot, original_payload = snapshot(slot), snapshot(payload)
    samples = []
    missing, rejected = scenario.startswith("missing-"), scenario == "pinned-rejected"
    expected_lookup = f"python find --no-project --managed-python --no-python-downloads {python}"
    for cycle in range(1 if missing or rejected else 2):
        started = time.monotonic()
        result = subprocess.run(
            command, env=env, cwd=tmp_path, input="foreground input\n",
            capture_output=True, text=True, encoding="utf-8", timeout=35, creationflags=HEADLESS,
        )
        samples.append(time.monotonic() - started)
        assert not Path(env["AMBIENT_MARKER"]).exists()
        assert "installer should not run" not in result.stderr, result.stdout + result.stderr
        assert snapshot(slot) == original_slot and snapshot(payload) == original_payload
        assert result.returncode == (126 if missing or rejected else 37), result.stdout + result.stderr
        calls = uv_log.read_text(encoding="utf-8").splitlines() if uv_log.exists() else []
        assert calls == ([] if missing else [expected_lookup] * (cycle + 1)), result.stdout + result.stderr
        assert validation.exists() is (pinned and not missing)
        if missing or rejected:
            assert not result.stdout and not proof.exists(), result.stdout + result.stderr
            continue
        assert result.stdout.strip() == "application stdout" and "application stderr" in result.stderr
        assert json.loads(proof.read_text(encoding="utf-8")) == {
            "python": str(python), "prefix": str(slot), "version": [3, 10, 20],
            "argv": arguments, "stdin": "foreground input\n", "cwd": str(tmp_path),
            "root": str(root), "context": str(context) if context else None,
            "installation_id": identity or None, "cache": cache,
            "selector": "unrelated-python", "isolated": 1,
            "bytecode": "readiness" in scenario or scenario == "contract",
        }
        if host == "bash" and scenario == "current":
            assert descriptors.read_text(encoding="utf-8").splitlines() == (
                [f"application-fd-{fd}" for fd in range(5, 10)]
                + [f"caller-fd-{fd}" for fd in range(5, 10)]
            ) * (cycle + 1)
    record_property("vault_command_admission", json.dumps({
        "shell": host, "scenario": scenario, "materialized": materialized,
        "samples_seconds": samples, "network": "disabled", "runtime_python": "3.10.20",
        "bootstrap_python": "3.11.18", "application_uv_lookups": len(calls),
        "pin_validated": validation.exists(),
    }))
