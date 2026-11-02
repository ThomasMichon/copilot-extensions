from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from test_ensure_service_hooks import PLUGIN
from test_session_stamp_security import _hook, _run
from stamp_fixture import no_shared_write_umask  # noqa: F401


def _active_hook(
    tmp_path: Path, shell: str,
) -> tuple[list[str], dict[str, str], Path, Path]:
    command, environment, repo = _hook(tmp_path, shell)
    shell_cache = tmp_path / "shell-cache"
    shell_cache.mkdir()
    environment["PSModuleAnalysisCachePath"] = str(shell_cache / "module-analysis.cache")
    payload = Path(environment["AGENT_INDEX_PAYLOAD_ROOT"])
    library = PLUGIN.parents[1] / "libs/installation-context"
    context_dir = payload / "scripts/installation-context"
    for source in library.iterdir():
        if source.is_file() and source.suffix in {".py", ".ps1", ".sh", ".awk"}:
            shutil.copy2(source, context_dir / source.name)
    tool = context_dir / "installation_context.py"
    durable = tmp_path / "durable"

    def invoke(*arguments: str) -> dict[str, object]:
        result = subprocess.run(
            [sys.executable, "-I", str(tool), *arguments],
            env=environment, capture_output=True, text=True, check=True, timeout=20,
        )
        return json.loads(result.stdout)

    stamped = invoke(
        "stamp", "--source-json",
        '{"source":"github","repo":"Example-Org/Example-Marketplace.git"}',
        "--marketplace-key", "example", "--plugin-id", "agent-index",
        "--payload-root", str(payload), "--payload-version", "9.9.9",
        "--payload-origin", "explicit",
        "--expected-namespace-generation", "0",
        "--expected-install-generation", "0", "--durable-home", str(durable),
    )
    context = Path(str(stamped["installReceipt"]))
    install = json.loads(context.read_text())
    namespace = json.loads(Path(install["namespaceReceipt"]).read_text())
    policy = tmp_path / "installation-mode.json"
    policy.write_text(
        '{"schema":"copilot-extensions.installation-mode","version":1,'
        '"installationMode":{"enabled":true}}'
    )
    invoke(
        "activation-cas", "--context", str(context),
        "--expected-marketplace-id", install["marketplaceId"],
        "--expected-plugin-id", "agent-index",
        "--expected-namespace-generation", str(namespace["generation"]),
        "--expected-install-generation", str(install["generation"]),
        "--expected-activation-generation", "0",
        "--activation-mode", "namespaced", "--activation-state", "active",
        "--legacy-disposition", "absent",
        "--legacy-probe-json",
        '{"declared":true,"result":"absent","checkedAt":"2026-01-01T00:00:00Z"}',
        "--legacy-root", environment["AGENT_INDEX_HOME"],
        "--durable-home", str(durable),
    )
    resolution = invoke(
        "status", "--payload-root", str(payload), "--plugin-id", "agent-index",
        "--context", str(context), "--durable-home", str(durable),
        "--legacy-root", environment["AGENT_INDEX_HOME"],
        "--policy-path", str(policy),
    )
    assert resolution["status"] == "ready"
    assert resolution["reason"] == "namespaced-active"
    # Redirect policy reads only; the canonical resolver still validates receipts.
    environment["STAMP_TEST_POLICY"] = str(policy)
    if shell == "bash":
        runner = context_dir / "installation-context.sh"
        original = context_dir / "fixture-context.sh"
        runner.rename(original)
        runner.write_text(
            '#!/usr/bin/env bash\n'
            'if [[ "$1" == status ]]; then\n'
            '  exec bash "$(dirname "$0")/fixture-context.sh" "$@" '
            '--policy-path "$STAMP_TEST_POLICY"\n'
            'fi\nexec bash "$(dirname "$0")/fixture-context.sh" "$@"\n'
        )
    else:
        runner = context_dir / "installation-context.ps1"
        runner.rename(context_dir / "fixture-context.ps1")
        runner.write_text(
            "$forwarded = @($args)\n"
            "if ($args[0] -eq 'status') {\n"
            "  $forwarded += @('-PolicyPath', $env:STAMP_TEST_POLICY)\n"
            "}\n"
            "& (Get-Process -Id $PID).Path -NoProfile -File "
            "(Join-Path $PSScriptRoot 'fixture-context.ps1') @forwarded\n"
            "exit $LASTEXITCODE\n"
        )
    environment["COPILOT_EXTENSIONS_CONTEXT"] = str(context)
    state = Path(environment["BOOTSTRAP_KILLSWITCH_STATE_FILE"])
    state.parent.mkdir(parents=True)
    state.write_text('{"active":true,"reason":"default-installation-only"}')
    # An accidental guard invocation must fail even if its output is discarded.
    suffix = "sh" if shell == "bash" else "ps1"
    (payload / "scripts" / f"bootstrap-killswitch-guard.{suffix}").write_text("exit 23\n")
    for name in ("install.sh", "install.ps1", "ensure-service.sh", "ensure-service.ps1"):
        (payload / "scripts" / name).write_text("FORBIDDEN LIFECYCLE INVOCATION\n")
    return command, environment, repo, context.parent


@pytest.mark.parametrize("shell", ["bash", "powershell"])
def test_valid_namespaced_actual_hook_stamps_only_cell_metadata(
    tmp_path: Path, shell: str,
) -> None:
    command, environment, repo, root = _active_hook(tmp_path, shell)
    before = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    result = _run(command, environment, repo)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "{}"
    assert "default-installation-only" not in result.stderr
    after = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    expected = {
        Path("payload-dir"), Path("stamped-version"), Path(".publication.lock"),
    }
    if sys.platform == "win32" and shell == "powershell":
        lease = Path(".publication-leases") / (hashlib.sha256(b"redirects").hexdigest() + ".lock")
        expected.add(lease)
        assert after[lease] == b""
    assert set(after) - set(before) == expected
    assert all(after[path] == contents for path, contents in before.items())
    assert after[Path("stamped-version")] == b"9.9.9\n"
    assert after[Path("payload-dir")].decode().strip() == environment["AGENT_INDEX_PAYLOAD_ROOT"]
    assert not Path(environment["AGENT_INDEX_HOME"]).exists()
    assert not (Path(environment["HOME"]) / ".local").exists()
    assert not list(root.rglob("pyvenv.cfg"))
    assert not list(root.rglob("deploy-manifest.json"))
    assert not (root / "engine").exists()
    assert not (Path(environment["HOME"]) / ".config/systemd").exists()


def test_namespaced_powershell_hook_respects_cell_publication_mutex(tmp_path: Path) -> None:
    command, environment, repo, root = _active_hook(tmp_path, "powershell")
    holder = tmp_path / "hold-mutex.ps1"
    holder.write_text(
        "$identity = [IO.Path]::GetFullPath($env:STAMP_TEST_ROOT).TrimEnd('/\\')\n"
        "if ($env:OS -eq 'Windows_NT') { $identity = $identity.ToLowerInvariant() }\n"
        "$sha = [Security.Cryptography.SHA256]::Create()\n"
        "$hash = [BitConverter]::ToString($sha.ComputeHash("
        "[Text.Encoding]::UTF8.GetBytes($identity))).Replace('-', '').Substring(0,24)\n"
        "$sha.Dispose()\n"
        '$name = "CopilotExtensions.AgentIndex.Stamp.$hash.Publish"\n'
        "if ($env:OS -eq 'Windows_NT') { $name = \"Global\\$name\" }\n"
        "try { $mutex = New-Object Threading.Mutex($false, $name) }\n"
        "catch [UnauthorizedAccessException] {\n"
        "  $mutex = New-Object Threading.Mutex($false, $name.Replace('Global\\','Local\\'))\n"
        "}\n"
        "$null = $mutex.WaitOne()\n"
        "try { [Console]::Out.WriteLine('held'); [Console]::Out.Flush(); "
        "$null = [Console]::ReadLine() }\n"
        "finally { $mutex.ReleaseMutex(); $mutex.Dispose() }\n"
    )
    environment["STAMP_TEST_ROOT"] = str(root)
    with subprocess.Popen(
        [command[0], "-NoProfile", "-File", str(holder)],
        env=environment, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True,
    ) as process:
        assert process.stdout is not None
        try:
            assert process.stdout.readline().strip() == "held"
            result = _run(command, environment, repo)
            assert result.returncode == 0, result.stderr
            assert result.stdout == "{}"
            assert not (root / "stamped-version").exists()
            assert not (root / "payload-dir").exists()
            assert not Path(environment["AGENT_INDEX_HOME"]).exists()
        finally:
            _, errors = process.communicate("\n", timeout=20)
        assert process.returncode == 0, errors
    result = _run(command, environment, repo)
    assert result.returncode == 0, result.stderr
    assert (root / "stamped-version").read_text() == "9.9.9\n"
