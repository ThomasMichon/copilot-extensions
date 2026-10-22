"""Qualify the real installer and its nested output producers without a runtime."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

PLUGIN = Path(__file__).resolve().parents[1]
INSTALLER = PLUGIN / "scripts" / "init.ps1"
VERSION = json.loads((PLUGIN / "plugin.json").read_text(encoding="utf-8"))["version"]
HOSTS = [
    pytest.param(shutil.which(name), id=name)
    for name in ("pwsh", "powershell.exe")
    if name == "pwsh" or os.name == "nt"
]
pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows producer encoding")


def _quote(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _producer_setup(directory: Path) -> str:
    for name in ("installer-human-output.ps1", "versioned_runtime_native.ps1"):
        shutil.copyfile(INSTALLER.parent / name, directory / name)
    text = INSTALLER.read_text(encoding="utf-8")
    start = text.index("Set-StrictMode -Version 2.0")
    end = text.index("# === install-contract:test-persistent-environment", start)
    return text[start:end]


def _copy_payload(target: Path) -> None:
    """Copy the real stamp/lifecycle assets, not an untested runtime assembly."""
    shutil.copytree(
        PLUGIN / "scripts", target / "scripts",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    for name in ("plugin.json", "pyproject.toml", "payload-invocation.json"):
        shutil.copyfile(PLUGIN / name, target / name)


def _environment(home: Path) -> dict[str, str]:
    home.mkdir()
    env = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("COPILOT_", "AGENT_MACHINES_", "MACHINES_FIXTURE_"))
    }
    env.update(
        HOME=str(home), USERPROFILE=str(home), COPILOT_EXTENSIONS_TEST_CONTAINED="1",
        COPILOT_EXTENSIONS_HUMAN_UTF8="1",
    )
    return env


def _run(
    host: str | None, script: Path, env: dict[str, str],
) -> subprocess.CompletedProcess[bytes]:
    if host is None:
        pytest.skip("PowerShell host unavailable")
    from agent_procutil import spawn_sync_in_kill_on_close_job

    arguments = [host, "-NoProfile", "-NonInteractive", "-File", str(script)]
    process, job = spawn_sync_in_kill_on_close_job(
        arguments, require_job=True, allow_breakaway=False,
        cwd=script.parent, env=env, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert job is not None
    try:
        try:
            stdout, stderr = process.communicate(timeout=20)
        except subprocess.TimeoutExpired as error:
            job.terminate_and_wait(2)
            stdout, stderr = process.communicate(timeout=2)
            raise AssertionError(
                f"Original 20s installer bound exceeded; stdout={stdout!r}; stderr={stderr!r}"
            ) from error
        return subprocess.CompletedProcess(arguments, process.returncode, stdout, stderr)
    finally:
        if job.handle:
            job.terminate_and_wait(2)
        process.wait(timeout=2)


@pytest.mark.parametrize("host", HOSTS)
@pytest.mark.parametrize("native_exit", [0, 23])
def test_construction_shell_stderr_uses_requested_producer_encoding(
    host: str | None, native_exit: int, tmp_path: Path,
) -> None:
    text = INSTALLER.read_text(encoding="utf-8")
    start = text.index("function Invoke-VersionedSlotClean {")
    end = text.index("\nfunction Invoke-VersionedMarkComplete", start)
    (tmp_path / "versioned_runtime.py").write_text(
        "import json,os,sys\nfrom pathlib import Path\n"
        "Path(os.environ['MACHINES_FIXTURE_RECEIPT']).write_text("
        "json.dumps([sys.flags.isolated,sys.argv[1:]]), encoding='utf-8')\n"
        "sys.stderr.buffer.write(b'native detail caf\\xe9\\n')\n"
        "print(\"function Start-RuntimeConstruction { "
        "param($Python,$Helper,$Root,$Version,$PayloadHash,$LinkName) "
        "[IO.File]::WriteAllText($env:MACHINES_FIXTURE_CALLED,'called'); return $true }\")\n"
        f"sys.exit({native_exit})\n",
        encoding="ascii",
    )
    script = tmp_path / "construction-output.ps1"
    root = tmp_path / "runtime with spaces"
    script.write_text(
        "[Console]::OutputEncoding = [Text.Encoding]::GetEncoding(1252)\n"
        "$OutputEncoding = [Text.ASCIIEncoding]::new()\n"
        "$Action = 'install'\n"
        + _producer_setup(tmp_path)
        + f"""
function Assert-SourceRuntimeIdentity {{}}
function Get-BootstrapPython {{ {_quote(sys.executable)} }}
function Get-PayloadHash {{ 'fixture-hash' }}
$VersionedRuntime = $true
$InstallDir = {_quote(root)}
$LinkDir = Join-Path $InstallDir '.venv'
$SrcVersion = '2.0.0'
{text[start:end]}
try {{
    Invoke-VersionedSlotClean
    [Console]::Out.WriteLine('completed')
}} catch {{
    [Console]::Out.WriteLine('caught=' + $_.Exception.Message)
}}
if ([Console]::OutputEncoding.CodePage -ne 1252 -or $OutputEncoding.CodePage -ne 20127) {{
    throw 'native decoding or stdin changed'
}}
exit 0
""",
        encoding="ascii",
    )
    receipt = tmp_path / "native-argv.json"
    called = tmp_path / "called"
    result = _run(host, script, {
        **os.environ, "COPILOT_EXTENSIONS_HUMAN_UTF8": "1",
        "MACHINES_FIXTURE_RECEIPT": str(receipt), "MACHINES_FIXTURE_CALLED": str(called),
    })
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(receipt.read_text(encoding="utf-8")) == [
        1, ["--root", str(root), "construction-shell", "--shell", "ps1"],
    ]
    assert "native detail caf\u00e9" in result.stderr.decode("utf-8")
    if native_exit == 0:
        assert called.read_text(encoding="utf-8-sig") == "called"
        assert result.stdout.decode("utf-8").strip() == "completed"
    else:
        assert not called.exists()
        assert "Canonical construction adapter is unavailable" in result.stdout.decode("utf-8")


@pytest.mark.parametrize("host", HOSTS)
def test_actual_legacy_probe_and_staged_stamp_keep_requested_output(
    host: str | None, tmp_path: Path,
) -> None:
    profile = tmp_path / "profile caf\u00e9 'quoted'"
    env = _environment(profile)
    payload = profile / ".copilot" / "installed-plugins" / "fixture" / "agent-machines"
    _copy_payload(payload)
    script = tmp_path / "staged-output.ps1"
    script.write_text(
        "[Console]::OutputEncoding = [Text.Encoding]::GetEncoding(1252)\n"
        "& $env:MACHINES_FIXTURE_ENTRY -Action stamp -Force\n"
        "exit $LASTEXITCODE\n",
        encoding="ascii",
    )
    env["MACHINES_FIXTURE_ENTRY"] = str(payload / "scripts" / "init.ps1")
    result = _run(host, script, env)
    (tmp_path / "staged-stdout.bin").write_bytes(result.stdout)
    (tmp_path / "staged-stderr.bin").write_bytes(result.stderr)
    assert result.returncode == 0, result.stdout + result.stderr
    output = result.stdout.decode("utf-8")
    root = profile / ".agent-machines"
    assert str(root) in output
    assert "Stamped: agent-machines" in output
    assert not result.stderr, result.stderr
    assert "#< CLIXML" not in output
    stages = list((root / ".install-stage").iterdir())
    assert len(stages) == 1
    staged = stages[0] / "agent-machines" / "scripts" / "init.ps1"
    assert staged.read_bytes() == (payload / "scripts" / "init.ps1").read_bytes()
    snapshot = root / "snapshots" / VERSION
    assert Path((root / "payload-dir").read_text(encoding="utf-8-sig")) == snapshot
    assert (snapshot / "scripts" / "init.ps1").is_file()
    assert not (root / "versions").exists()


@pytest.mark.parametrize("host", HOSTS)
@pytest.mark.parametrize("requested", [False, True])
def test_actual_lifecycle_refusal_retains_requested_error_encoding(
    host: str | None, requested: bool, tmp_path: Path,
) -> None:
    env = _environment(tmp_path / "home")
    if not requested:
        env.pop("COPILOT_EXTENSIONS_HUMAN_UTF8")
    payload = tmp_path / "payload"
    _copy_payload(payload)
    script = payload / "scripts" / "lifecycle-output.ps1"
    script.write_text(
        "[Console]::OutputEncoding = [Text.Encoding]::GetEncoding(1252)\n"
        "$Action = 'cell-repair'\n"
        "$PSBoundParameters['Action'] = $Action\n"
        "$PSBoundParameters['Context'] = $env:MACHINES_FIXTURE_CONTEXT\n"
        "$PSBoundParameters['DurableHome'] = $env:MACHINES_FIXTURE_DURABLE\n"
        "$PSBoundParameters['ExpectedMarketplaceId'] = 'fixture-market'\n"
        "$PSBoundParameters['ExpectedPayloadRoot'] = $env:MACHINES_FIXTURE_PAYLOAD\n"
        "$PSBoundParameters['ExpectedPayloadVersion'] = '1.0.0'\n"
        "$PSBoundParameters['SnapshotId'] = '1.0.0'\n"
        "$PSBoundParameters['RuntimeVersion'] = $env:MACHINES_FIXTURE_INVALID_VERSION\n"
        "$PSBoundParameters['ExpectedNamespaceGeneration'] = '1'\n"
        "$PSBoundParameters['ExpectedInstallGeneration'] = '1'\n"
        "$PSBoundParameters['ExpectCurrentAbsent'] = [Management.Automation.SwitchParameter]$true\n"
        "$PSBoundParameters['ExpectLastKnownGoodAbsent'] = [Management.Automation.SwitchParameter]$true\n"
        f"function Get-Command {{ param($Name,$CommandType,$ErrorAction)\n"
        "if ($Name -ne 'python') { throw 'unexpected interpreter lookup' }\n"
        f"[pscustomobject]@{{ Source = {_quote(sys.executable)} }} }}\n"
        + _producer_setup(script.parent),
        encoding="ascii",
    )
    durable = tmp_path / "uncreated-durable"
    invalid = "invalid-caf\u00e9"
    env.update(
        MACHINES_FIXTURE_CONTEXT=str(durable / "missing.json"),
        MACHINES_FIXTURE_DURABLE=str(durable), MACHINES_FIXTURE_PAYLOAD=str(payload),
        MACHINES_FIXTURE_INVALID_VERSION=invalid,
    )
    result = _run(host, script, env)
    assert result.returncode == 1, result.stdout + result.stderr
    assert not result.stdout
    if not requested:
        baseline = subprocess.run(
            [sys.executable, "-I", str(payload / "scripts" / "cell_lifecycle.py"),
             "cell-repair", "--context", env["MACHINES_FIXTURE_CONTEXT"],
             "--durable-home", str(durable), "--expected-marketplace-id", "fixture-market",
             "--expected-payload-root", str(payload), "--expected-payload-version", "1.0.0",
             "--snapshot-id", "1.0.0", "--runtime-version", invalid,
             "--expected-namespace-generation", "1", "--expected-install-generation", "1",
             "--expect-current-absent", "--expect-last-known-good-absent"],
            env=env, capture_output=True, timeout=20,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        assert baseline.returncode == result.returncode
        assert baseline.stdout == result.stdout
        assert baseline.stderr == result.stderr
        assert not durable.exists()
        return
    error = result.stderr.decode("utf-8")
    assert "agent-machines lifecycle:" in error
    assert invalid in error
    assert "#< CLIXML" not in error
    assert not durable.exists()
