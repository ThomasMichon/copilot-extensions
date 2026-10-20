"""Conditional stamp callers propagate shared writer failures before success."""
from __future__ import annotations

import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "plugins" / "agent-vault" / "scripts"
ENGINE = ROOT / "libs" / "installer-engine" / "installer-engine.sh"


def _function(name: str) -> str:
    text = (SCRIPTS / "install.sh").read_text(encoding="utf-8")
    match = re.search(rf"^{re.escape(name)}\(\) \{{.*?^\}}", text, re.M | re.S)
    assert match, name
    return match.group()


@pytest.mark.skipif(os.name == "nt", reason="native POSIX publication")
@pytest.mark.parametrize("failure", [
    "none", "directories", "resolver-staging", "ps1-copy", "sh-copy",
    "resolver-rename", "stub-staging", "stub-write", "stub-chmod",
    "stub-rename", "missing-source", "ps1-directory", "sh-directory", "stub-directory",
    "ps1-directory-link", "sh-directory-link", "stub-directory-link",
])
def test_conditional_stamp_reports_publication_failures(tmp_path: Path, failure: str) -> None:
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("Bash unavailable")
    payload = tmp_path / "payload"
    scripts = payload / "scripts"
    scripts.mkdir(parents=True)
    for name in ("source-runtime.sh", "resolve-runtime.sh", "resolve-runtime.ps1"):
        shutil.copyfile(SCRIPTS / name, scripts / name)
    if failure == "missing-source":
        (scripts / "resolve-runtime.sh").rename(scripts / "unavailable-resolver.sh")
    root = tmp_path / "runtime"
    bin_dir = root / "bin"
    bin_dir.mkdir(parents=True)
    local_bin = tmp_path / "local-bin"
    local_bin.mkdir()
    stub = local_bin / "agent-vault"
    stub.write_text("#!/bin/sh\n# previous command\n", encoding="utf-8")
    for suffix in ("ps1", "sh"):
        (bin_dir / f"resolve-runtime.{suffix}").write_text("previous resolver\n", encoding="utf-8")
    for name in ("payload-dir", "stamped-version", "current-version", "last-known-good"):
        (root / name).write_text("previous\n", encoding="utf-8")
    collision = None
    if "-directory" in failure:
        collision = {
            "ps1": bin_dir / "resolve-runtime.ps1",
            "sh": bin_dir / "resolve-runtime.sh",
            "stub": stub,
        }[failure.split("-")[0]]
    if collision is not None:
        collision.unlink()
        if failure.endswith("-link"):
            directory = tmp_path / "collision-directory"
            directory.mkdir()
            collision.symlink_to(directory, target_is_directory=True)
        else:
            collision.mkdir()
        (collision / "sentinel").write_text("preserved\n", encoding="utf-8")
    old_resolvers = {
        path: path.read_bytes() for path in bin_dir.iterdir() if path.is_file()
    }
    before = {name: (root / name).read_bytes() for name in (
        "payload-dir", "stamped-version", "current-version", "last-known-good",
    )}
    old_stub = stub.read_bytes() if stub.is_file() else None
    script = f"""
set -euo pipefail
PLUGIN_DIR={shlex.quote(str(payload))}
SCRIPT_DIR="$PLUGIN_DIR/scripts"
INSTALL_DIR={shlex.quote(str(root))}
LOCAL_BIN={shlex.quote(str(local_bin))}
SOURCE_RUNTIME_IDENTITY=""
SRC_VERSION=1.2.3
_failure={shlex.quote(failure)}
_ok() {{ printf 'OK: %s\\n' "$*"; }}
_fail() {{ printf 'ERROR: %s\\n' "$*" >&2; }}
injected() {{ printf 'injected %s\\n' "$_failure" >&2; }}
mkdir() {{
    if [[ "$_failure" == directories && "${{2:-}}" == "$LOCAL_BIN" ]]; then
        injected; return 42
    fi
    command mkdir "$@"
}}
mktemp() {{
    if [[ "$_failure" == resolver-staging && "$1" == "$INSTALL_DIR/bin/.resolve-runtime.ps1.XXXXXX" ]] ||
       [[ "$_failure" == stub-staging && "$1" == "$LOCAL_BIN/.agent-vault.XXXXXX" ]]; then
        injected; return 42
    fi
    command mktemp "$@"
}}
cp() {{
    if [[ "$_failure" == ps1-copy && "${{2:-}}" == "$SCRIPT_DIR/resolve-runtime.ps1" ]] ||
       [[ "$_failure" == sh-copy && "${{2:-}}" == "$SCRIPT_DIR/resolve-runtime.sh" ]]; then
        printf 'partial resolver' > "$3"
        injected; return 42
    fi
    command cp "$@"
}}
cat() {{
    if [[ "$_failure" == stub-write ]]; then
        printf 'partial command'
        injected; return 42
    fi
    command cat "$@"
}}
chmod() {{
    if [[ "$_failure" == stub-chmod ]]; then injected; return 42; fi
    command chmod "$@"
}}
mv() {{
    if [[ "$_failure" == resolver-rename && "${{3:-}}" == "$INSTALL_DIR/bin/resolve-runtime.ps1" ]] ||
       [[ "$_failure" == stub-rename && "${{3:-}}" == "$LOCAL_BIN/agent-vault" ]]; then
        injected; return 42
    fi
    command mv "$@"
}}
. {shlex.quote(str(ENGINE))}
. "$SCRIPT_DIR/source-runtime.sh"
{_function("_deploy_binstub")}
{_function("do_stamp")}
if do_stamp; then exit 0; else exit 19; fi
"""
    result = subprocess.run(
        [bash, "-c", script], cwd=tmp_path, capture_output=True, text=True,
        timeout=15, env={**os.environ, "HOME": str(tmp_path / "home")},
    )
    if failure == "none":
        assert result.returncode == 0, result.stdout + result.stderr
        assert "Stamped:" in result.stdout
        assert Path((root / "payload-dir").read_text().strip()) == payload
        assert os.access(stub, os.X_OK)
        parsed = subprocess.run([bash, "-n", str(stub)], capture_output=True, text=True, timeout=5)
        assert parsed.returncode == 0, parsed.stderr
        for suffix in ("ps1", "sh"):
            assert (bin_dir / f"resolve-runtime.{suffix}").read_bytes() == (
                scripts / f"resolve-runtime.{suffix}"
            ).read_bytes()
    else:
        assert result.returncode == 19, result.stdout + result.stderr
        assert "ERROR:" in result.stderr
        assert "Binstub:" not in result.stdout
        assert "Stamped:" not in result.stdout
        if old_stub is not None:
            assert stub.read_bytes() == old_stub
        assert all((root / name).read_bytes() == value for name, value in before.items())
        if collision is not None:
            assert "destination must be a file" in result.stderr
            assert (collision / "sentinel").read_text() == "preserved\n"
            assert sorted(path.name for path in collision.iterdir()) == ["sentinel"]
            assert all(path.read_bytes() == value for path, value in old_resolvers.items())
        elif failure != "missing-source":
            assert f"injected {failure}" in result.stderr
        if failure in {"ps1-copy", "resolver-rename"}:
            assert (bin_dir / "resolve-runtime.ps1").read_text() == "previous resolver\n"
        if failure == "sh-copy":
            assert (bin_dir / "resolve-runtime.sh").read_text() == "previous resolver\n"
    assert not list(bin_dir.glob(".resolve-runtime.*"))
    assert not list(local_bin.glob(".agent-vault.*"))


@pytest.mark.parametrize("host", ["powershell", "pwsh"])
@pytest.mark.parametrize("failure", [
    "none", "first-publication", "ps1-copy", "sh-copy", "resolver-rename",
    "resolver-source-locked", "ps1-rename", "cmd-rename", "missing-source",
    "ps1-directory", "sh-directory", "command-ps1-directory", "command-cmd-directory",
])
def test_native_shared_writer_preserves_complete_files(
    tmp_path: Path, host: str, failure: str,
) -> None:
    executable = shutil.which(host)
    if not executable:
        pytest.skip(f"{host} unavailable")
    replacement_failure = failure in {
        "resolver-rename", "resolver-source-locked", "ps1-rename", "cmd-rename",
    }
    if replacement_failure and os.name != "nt":
        pytest.skip("Windows mandatory file-sharing replacement boundary")
    scripts = tmp_path / "source"
    scripts.mkdir()
    for suffix in ("ps1", "sh"):
        (scripts / f"resolve-runtime.{suffix}").write_text(f"new {suffix} resolver\n", encoding="utf-8")
    if failure == "missing-source":
        (scripts / "resolve-runtime.sh").rename(scripts / "unavailable.sh")
    root = tmp_path / "runtime"
    bin_dir = root / "bin"
    bin_dir.mkdir(parents=True)
    local_bin = tmp_path / "local-bin"
    local_bin.mkdir()
    targets = {
        "ps1-copy": bin_dir / "resolve-runtime.ps1",
        "sh-copy": bin_dir / "resolve-runtime.sh",
        "resolver-rename": bin_dir / "resolve-runtime.ps1",
        "resolver-source-locked": bin_dir / "resolve-runtime.ps1",
        "ps1-rename": local_bin / "agent-vault.ps1",
        "cmd-rename": local_bin / "agent-vault.cmd",
        "ps1-directory": bin_dir / "resolve-runtime.ps1",
        "sh-directory": bin_dir / "resolve-runtime.sh",
        "command-ps1-directory": local_bin / "agent-vault.ps1",
        "command-cmd-directory": local_bin / "agent-vault.cmd",
    }
    for path in set(targets.values()):
        if failure != "first-publication":
            path.write_text("previous complete file\n", encoding="utf-8")
    collision = targets[failure] if failure.endswith("-directory") else None
    if collision is not None:
        collision.unlink()
        collision.mkdir()
        (collision / "sentinel").write_text("preserved\n", encoding="utf-8")
    old = {name: path.read_bytes() for name, path in targets.items() if path.is_file()}
    def quote(path: Path) -> str:
        return "'" + str(path).replace("'", "''") + "'"

    harness = tmp_path / "publish.ps1"
    harness.write_text(f"""
$ErrorActionPreference = 'Continue'
$failure = '{failure}'
$sourceDir = {quote(scripts)}
$installBin = {quote(bin_dir)}
$localBin = {quote(local_bin)}
$replacementTarget = {quote(targets.get(failure, tmp_path / "unused"))}
$replacementLock = $null
function Write-Ok($Message) {{ Write-Output "SUCCESS: $Message" }}
function Copy-Item {{
    [CmdletBinding()]
    param($LiteralPath, $Destination, [switch]$Force)
    if (($failure -eq 'ps1-copy' -and (Split-Path -Leaf $LiteralPath) -eq 'resolve-runtime.ps1') -or
        ($failure -eq 'sh-copy' -and (Split-Path -Leaf $LiteralPath) -eq 'resolve-runtime.sh')) {{
        [IO.File]::WriteAllText($Destination, 'partial resolver')
        Write-Error "Injected $failure"
        return
    }}
    Microsoft.PowerShell.Management\\Copy-Item @PSBoundParameters
    if ($failure -eq 'resolver-source-locked' -and
        (Split-Path -Leaf $LiteralPath) -eq 'resolve-runtime.ps1') {{
        $script:replacementLock = [IO.File]::Open($Destination, 'Open', 'Read', 'Read')
        [Console]::Error.WriteLine("Locked replacement boundary: $failure")
    }}
}}
. {quote(ENGINE.with_suffix(".ps1"))}
try {{
    if ($failure -in @('resolver-rename', 'ps1-rename', 'cmd-rename')) {{
        $replacementLock = [IO.File]::Open($replacementTarget, 'Open', 'Read', 'Read')
        [Console]::Error.WriteLine("Locked replacement boundary: $failure")
    }}
    Write-SimpleBinstub -CommandName agent-vault -ModuleName agent_vault `
        -RuntimeRoot {quote(root)} -LocalBin $localBin -InstallBinDir $installBin `
        -SnapshotInstallerPath 'scripts/install.ps1' -NoSelfProvisionEnv TEST_NO_PROVISION `
        -ResolverPs1Source (Join-Path $sourceDir 'resolve-runtime.ps1') `
        -ResolverShSource (Join-Path $sourceDir 'resolve-runtime.sh')
}} catch {{
    [Console]::Error.WriteLine($_.Exception.Message)
    $cause = $_.Exception
    while ($cause.InnerException) {{ $cause = $cause.InnerException }}
    [Console]::Error.WriteLine("NATIVE_ERROR_CODE=" + ($cause.HResult -band 65535))
    exit 19
}} finally {{
    if ($replacementLock) {{ $replacementLock.Dispose() }}
}}
""", encoding="utf-8")
    result = subprocess.run(
        [executable, "-NoProfile", "-NonInteractive", "-File", str(harness)],
        cwd=tmp_path, capture_output=True, text=True, timeout=20,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    if failure in {"none", "first-publication"}:
        assert result.returncode == 0, result.stdout + result.stderr
        assert "SUCCESS: Binstub:" in result.stdout
        assert "-m agent_vault" in (local_bin / "agent-vault.ps1").read_text()
        assert (local_bin / "agent-vault.cmd").is_file()
        for suffix in ("ps1", "sh"):
            assert (bin_dir / f"resolve-runtime.{suffix}").read_bytes() == (
                scripts / f"resolve-runtime.{suffix}"
            ).read_bytes()
    else:
        assert result.returncode == 19, result.stdout + result.stderr
        assert "SUCCESS:" not in result.stdout
        if failure == "missing-source":
            assert "resolver source is unavailable" in result.stderr
        elif collision is not None:
            assert "Binstub publication failed" in result.stderr
            assert (collision / "sentinel").read_text() == "preserved\n"
            assert sorted(path.name for path in collision.iterdir()) == ["sentinel"]
        else:
            assert (
                f"Locked replacement boundary: {failure}" if replacement_failure else f"Injected {failure}"
            ) in result.stderr
            assert "Binstub publication failed" in result.stderr
            if replacement_failure:
                assert "NATIVE_ERROR_CODE=32" in result.stderr
            assert targets[failure].read_bytes() == old[failure]
    staged = list(bin_dir.glob(".*"))
    if failure == "resolver-source-locked":
        assert len(staged) == 1
        assert staged[0].read_bytes() == (scripts / "resolve-runtime.ps1").read_bytes()
    else:
        assert not staged
    assert not list(local_bin.glob(".*"))
