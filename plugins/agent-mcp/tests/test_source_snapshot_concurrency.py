"""Competing native stampers use the real cross-process generation lease."""
from __future__ import annotations

import json
import os
import runpy
import shutil
import subprocess
import time
from contextlib import ExitStack
from pathlib import Path

import pytest

pytestmark = pytest.mark.guard
FIXTURE = runpy.run_path(str(Path(__file__).with_name("test_source_install_transactions.py")))
IDENTITY = FIXTURE["IDENTITY"]

_LEASE_OBSERVER = """
from contextlib import contextmanager
_fixture_runtime = _runtime()
_fixture_lease = _fixture_runtime.construction_lease
@contextmanager
def _observed_lease(root, generation, **kwargs):
    gate = Path(os.environ['SOURCE_STAMP_TEST_COORD'])
    (gate / ('attempt-' + str(os.getpid()))).write_text('waiting', encoding='utf-8')
    with _fixture_lease(root, generation, **kwargs):
        yield
        snapshot = root / generation
        if snapshot.is_dir():
            evidence = {
                path.relative_to(snapshot).as_posix():
                    [path.stat().st_ino, path.stat().st_mtime_ns, path.stat().st_size]
                for path in snapshot.rglob('*') if path.is_file()
            }
            (gate / ('winner-' + str(os.getpid()))).write_text(
                json.dumps(evidence, sort_keys=True), encoding='utf-8')
_fixture_runtime.construction_lease = _observed_lease
"""


def _harness(tmp_path: Path, payload: Path, plugin: str, language: str) -> str:
    setup = FIXTURE["_setup"](tmp_path, payload, plugin, language)
    if language == "sh":
        return setup + """
mv() {
    printf waiting > "$SOURCE_STAMP_TEST_COORD/attempt-$$"
    local deadline=$((SECONDS + 20))
    while [[ ! -f "$SOURCE_STAMP_TEST_COORD/release" ]]; do
        [[ "$SECONDS" -lt "$deadline" ]] || { echo 'Move gate timed out' >&2; return 1; }
        sleep 0.05
    done
    command mv "$@"
}
_initialize_source_runtime
_stamp_source_snapshot
"""
    return setup + f"""
$ServiceName = 'fixture'
$LocalBin = Join-Path $InstallDir 'bin'
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
function Write-ServiceHeader($value) {{ }}
function Write-ServiceOk($value) {{ }}
function Write-Ok($value) {{ }}
function Deploy-SelfProvisioningBinstub {{ }}
function Materialize-SnapshotDependencies {{ throw 'Source snapshot was rematerialized' }}
function Move-Item {{
    param($LiteralPath, $Destination, [switch]$Force)
    [IO.File]::WriteAllText((Join-Path $env:SOURCE_STAMP_TEST_COORD "attempt-$PID"), 'waiting')
    $deadline = [DateTime]::UtcNow.AddSeconds(20)
    while (-not (Test-Path (Join-Path $env:SOURCE_STAMP_TEST_COORD 'release'))) {{
        if ([DateTime]::UtcNow -gt $deadline) {{ throw 'Move gate timed out' }}
        [Threading.Thread]::Sleep(50)
    }}
    Microsoft.PowerShell.Management\\Move-Item -LiteralPath $LiteralPath `
        -Destination $Destination -Force
}}
{FIXTURE['_function'](plugin, 'Invoke-Stamp', language)}
Initialize-SourceRuntime
Invoke-Stamp
"""


@pytest.mark.parametrize("plugin", ["agent-mcp", "agent-codespaces"])
@pytest.mark.parametrize("native_host", ["sh", "pwsh", "powershell"])
def test_concurrent_native_stampers_publish_one_immutable_snapshot(
    tmp_path: Path, plugin: str, native_host: str,
) -> None:
    language = "sh" if native_host == "sh" else "ps1"
    host = FIXTURE["_host"]("sh") if language == "sh" else shutil.which(native_host)
    if not host:
        pytest.skip(f"{native_host} unavailable")
    payload, _ = FIXTURE["_payload"](tmp_path, plugin)
    selected = FIXTURE["_instrument_source_main"](payload, _LEASE_OBSERVER)
    harness = tmp_path / f"competing-stamp.{language}"
    harness.write_text(_harness(tmp_path, payload, plugin, language), encoding="utf-8")
    gate = tmp_path / "coordination"
    gate.mkdir()
    env = {**os.environ, "SOURCE_STAMP_TEST_COORD": str(gate)}
    command = (
        [host, FIXTURE["_quote"](harness, "sh")[1:-1]] if language == "sh" else
        [host, "-NoProfile", "-NonInteractive", "-File", str(harness)]
    )
    install = tmp_path / "install"
    snapshots = install / "snapshots"
    snapshots.mkdir()
    results = []
    # The legacy native move wrapper joins this same barrier only after both
    # absent-target checks: removing shared publication reproduces nested moves.
    # The new CLI reports at lease acquisition; no lease is replaced or mocked.
    with ExitStack() as processes:
        with IDENTITY._runtime().construction_lease(snapshots, selected):
            children = [
                processes.enter_context(subprocess.Popen(
                    command, cwd=tmp_path, env=env, stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, text=True, encoding="utf-8",
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                ))
                for _ in range(2)
            ]
            deadline = time.monotonic() + 20
            while len(list(gate.glob("attempt-*"))) < 2 and time.monotonic() < deadline:
                if any(child.poll() is not None for child in children):
                    break
                time.sleep(0.05)
            ready = len(list(gate.glob("attempt-*"))) == 2
            (gate / "release").write_text("release", encoding="utf-8")
        for child in children:
            output, error = child.communicate(timeout=40)
            results.append((child.returncode, output, error))
    assert ready, results
    assert any(code == 0 for code, _, _ in results), results
    for code, _, error in results:
        if code != 0:
            assert any(message in error for message in (
                "source-runtime:", "Source snapshot publication failed",
                "being used by another process",
            )), results
    snapshot = snapshots / selected
    original = IDENTITY.read_source_identity(payload, FIXTURE["VERSION"], plugin=plugin)
    assert IDENTITY.read_source_identity(
        snapshot, FIXTURE["VERSION"], plugin=plugin,
    ) == original
    assert (snapshot / "runtime-source.json").read_bytes() == (
        payload / "runtime-source.json"
    ).read_bytes()
    assert {path.name for path in snapshots.iterdir()} == {selected, ".construction-leases"}
    assert {path.relative_to(snapshot) for path in snapshot.rglob("*")} == {
        path.relative_to(payload) for path in payload.rglob("*")
    }
    evidence = [
        json.loads(path.read_text(encoding="utf-8")) for path in gate.glob("winner-*")
    ]
    assert evidence
    assert all(record == evidence[0] for record in evidence), "Winner was rewritten on reuse"
    assert Path((install / "payload-dir").read_text(encoding="utf-8").strip()) == snapshot
    assert (install / "stamped-version").read_text(encoding="utf-8").strip() == selected
