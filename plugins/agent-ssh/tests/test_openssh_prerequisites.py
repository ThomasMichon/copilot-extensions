from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
from typing import Any
import zipfile

import pytest

from agent_procutil import no_window_kwargs

PWSH = shutil.which("pwsh")
INSTALLER = Path(__file__).parents[1] / "transports" / "dtssh" / "scripts" / "install-host.ps1"
HELPER = (
    Path(__file__).parents[1]
    / "transports" / "dtssh" / "scripts" / "openssh-prerequisites.ps1"
)
pytestmark = [
    pytest.mark.guard,
    pytest.mark.skipif(PWSH is None, reason="PowerShell 7 is unavailable"),
]


def _archive(tmp_path: Path, extra: tuple[str, bytes] | None = None) -> Path:
    path = tmp_path / "fixture.zip"
    with zipfile.ZipFile(path, "w") as archive:
        for name in (
            "ssh.exe", "sshd.exe", "ssh-keygen.exe", "libcrypto.dll",
            "ssh-shellhost.exe", "sftp-server.exe", "sshd-session.exe", "sshd-auth.exe",
        ):
            archive.writestr("OpenSSH-Win64/" + name, b"fixture, not executable\n")
        if extra is not None:
            archive.writestr(*extra)
    return path


def _run(
    tmp_path: Path, archive: Path, body: str, *, digest: str | None = None,
) -> dict[str, Any]:
    env = {
        **os.environ,
        "TEST_OPENSSH_HELPER": str(HELPER),
        "TEST_OPENSSH_ARCHIVE": str(archive),
        "TEST_OPENSSH_ROOT": str(tmp_path),
        "TEST_OPENSSH_INSTALLER": str(INSTALLER),
        "TEST_OPENSSH_DIGEST": digest or hashlib.sha256(archive.read_bytes()).hexdigest(),
    }
    script = """
        $ErrorActionPreference = 'Stop'
        . $env:TEST_OPENSSH_HELPER
        function Get-AgentSshOpenSSHRelease {
            return @{Directory='OpenSSH-Win64'; Uri='https://example.com/fixture.zip';
                     Sha256=$env:TEST_OPENSSH_DIGEST}
        }
        function Save-AgentSshOpenSSHArchive([string]$Uri, [string]$Path) {
            [IO.File]::Copy($env:TEST_OPENSSH_ARCHIVE, $Path)
        }
        function Get-AgentSshOpenSSHVersion { return [Version]'10.0' }
    """ + body
    result = subprocess.run(
        [
            str(PWSH), "-NoProfile", "-NonInteractive", "-EncodedCommand",
            base64.b64encode(script.encode("utf-16-le")).decode("ascii"),
        ],
        capture_output=True, text=True, timeout=30, check=False, env=env,
        **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stderr + result.stdout
    payload: dict[str, Any] = json.loads(result.stdout.splitlines()[-1])
    return payload


@pytest.mark.skipif(os.name != "nt", reason="Native private-directory ACL requires Windows")
def test_fresh_preparation_and_offline_reuse_do_not_activate_transport(tmp_path: Path) -> None:
    result = _run(tmp_path, _archive(tmp_path), """
        $destination = Join-Path $env:TEST_OPENSSH_ROOT 'selected'
        $first = Install-AgentSshOpenSSH -Destination $destination
        function Save-AgentSshOpenSSHArchive { throw 'Reuse attempted a download' }
        $second = Install-AgentSshOpenSSH -Destination $destination
        [pscustomobject]@{First=$first; Second=$second} | ConvertTo-Json -Compress
    """)
    destination = tmp_path / "selected"
    assert result["First"] == result["Second"]
    assert Path(result["First"]["Sshd"]) == destination / "sshd.exe"
    assert len(list(destination.iterdir())) == 8
    assert not list(tmp_path.glob(".openssh-prepare-*"))


@pytest.mark.parametrize(
    "entry",
    [
        "../escape",
        "OpenSSH-Win64/../escape",
        "OpenSSH-Win64/SSH.EXE",
        "OpenSSH-Win64/stream:payload",
        "OpenSSH-Win64/nul.txt",
        "OpenSSH-Win64/trailing. ",
        "OpenSSH-Win64/wildcard*",
        "unselected/extra",
    ],
)
def test_archive_rejects_unsafe_entries_before_extracting(tmp_path: Path, entry: str) -> None:
    archive = _archive(tmp_path, (entry, b"untrusted"))
    result = _run(tmp_path, archive, """
        $destination = Join-Path $env:TEST_OPENSSH_ROOT 'expanded'
        [IO.Directory]::CreateDirectory($destination) | Out-Null
        $refused = $false
        try {
            Expand-AgentSshOpenSSHArchive $env:TEST_OPENSSH_ARCHIVE (Get-AgentSshOpenSSHRelease) $destination
        } catch { $refused = $true }
        @{Refused=$refused; Files=@(Get-ChildItem -LiteralPath $destination -Recurse).Count} |
            ConvertTo-Json -Compress
    """)
    assert result == {"Refused": True, "Files": 0}
    assert not (tmp_path / "escape").exists()


def test_digest_mismatch_precedes_archive_extraction(tmp_path: Path) -> None:
    result = _run(tmp_path, _archive(tmp_path), """
        $destination = Join-Path $env:TEST_OPENSSH_ROOT 'expanded'
        [IO.Directory]::CreateDirectory($destination) | Out-Null
        $message = ''
        try {
            Expand-AgentSshOpenSSHArchive $env:TEST_OPENSSH_ARCHIVE (Get-AgentSshOpenSSHRelease) $destination
        } catch { $message = $_.Exception.Message }
        @{Error=$message; Files=@(Get-ChildItem -LiteralPath $destination).Count} |
            ConvertTo-Json -Compress
    """, digest="0" * 64)
    assert "digest mismatch" in result["Error"]
    assert result["Files"] == 0


@pytest.mark.skipif(os.name != "nt", reason="Portable installation requires Windows")
def test_incomplete_existing_directory_is_preserved_without_network(tmp_path: Path) -> None:
    selected = tmp_path / "selected"
    selected.mkdir()
    sentinel = selected / "unrelated.txt"
    sentinel.write_text("preserve", encoding="ascii")
    result = _run(tmp_path, _archive(tmp_path), """
        function Save-AgentSshOpenSSHArchive { throw 'Unexpected network' }
        $message = ''
        try {
            Install-AgentSshOpenSSH -Destination (Join-Path $env:TEST_OPENSSH_ROOT 'selected')
        } catch { $message = $_.Exception.Message }
        @{Error=$message} | ConvertTo-Json -Compress
    """)
    assert "prerequisite is missing" in result["Error"]
    assert sentinel.read_text(encoding="ascii") == "preserve"
    assert not list(tmp_path.glob(".openssh-prepare-*"))


@pytest.mark.skipif(os.name != "nt", reason="Native private-directory ACL requires Windows")
def test_failed_fresh_preparation_cleans_only_its_staging(tmp_path: Path) -> None:
    sentinel = tmp_path / "unrelated.txt"
    sentinel.write_text("preserve", encoding="ascii")
    result = _run(tmp_path, _archive(tmp_path), """
        $refused = $false
        try {
            Install-AgentSshOpenSSH -Destination (Join-Path $env:TEST_OPENSSH_ROOT 'selected')
        } catch { $refused = $_.Exception.Message -like '*digest mismatch*' }
        @{Refused=$refused} | ConvertTo-Json -Compress
    """, digest="0" * 64)
    assert result["Refused"]
    assert not (tmp_path / "selected").exists()
    assert not list(tmp_path.glob(".openssh-prepare-*"))
    assert sentinel.read_text(encoding="ascii") == "preserve"


def test_host_validation_prepares_parent_before_calling_shared_owner(tmp_path: Path) -> None:
    helper = tmp_path / "host-helper.ps1"
    helper.write_text("""
        function Install-AgentSshOpenSSH([string]$Destination) {
            if (-not [IO.Directory]::Exists([IO.Path]::GetDirectoryName($Destination))) {
                throw 'Validation tool parent is missing'
            }
            [IO.Directory]::CreateDirectory($Destination) | Out-Null
            [IO.File]::WriteAllText((Join-Path $Destination 'ssh-keygen.exe'), 'fixture')
        }
    """, encoding="utf-8")
    result = _run(tmp_path, _archive(tmp_path), """
        $tokens = $null
        $errors = $null
        $ast = [Management.Automation.Language.Parser]::ParseFile(
            $env:TEST_OPENSSH_INSTALLER, [ref]$tokens, [ref]$errors)
        if ($errors.Count) { throw 'Invalid installer syntax' }
        $function = $ast.Find({
            param($node)
            $node -is [Management.Automation.Language.FunctionDefinitionAst] -and
                $node.Name -eq 'Install-HostIdentityValidationTools'
        }, $true)
        . ([scriptblock]::Create($function.Extent.Text))
        $InstallDir = Join-Path $env:TEST_OPENSSH_ROOT 'not-yet-created'
        $ValidationOpenSSHDir = Join-Path $InstallDir 'validation-OpenSSH'
        $OpenSSHPrerequisitesSrc = Join-Path $env:TEST_OPENSSH_ROOT 'host-helper.ps1'
        function Get-SshKeygenExe {
            $candidate = Join-Path $ValidationOpenSSHDir 'ssh-keygen.exe'
            if (Test-Path -LiteralPath $candidate) { return $candidate }
        }
        Install-HostIdentityValidationTools
        @{Prepared=[bool](Get-SshKeygenExe)} | ConvertTo-Json -Compress
    """)
    assert result["Prepared"]


@pytest.mark.skipif(os.name != "nt", reason="Native portable Windows path validation")
@pytest.mark.parametrize("destination", ["//server/share/tools", r"\/server\share\tools", r"/\server/share/tools"])
def test_equivalent_unc_spellings_refuse_before_filesystem_or_network(
    tmp_path: Path, destination: str,
) -> None:
    result = _run(tmp_path, _archive(tmp_path), """
        function Assert-AgentSshOrdinaryPath { throw 'Unexpected filesystem access' }
        function Save-AgentSshOpenSSHArchive { throw 'Unexpected network' }
        $message = ''
        try { Install-AgentSshOpenSSH -Destination """ + "'" + destination + "'" + """ }
        catch { $message = $_.Exception.Message }
        @{Error=$message} | ConvertTo-Json -Compress
    """)
    assert "absolute local" in result["Error"]


@pytest.mark.skipif(os.name != "nt", reason="Portable installation requires Windows")
@pytest.mark.parametrize("missing", ["libcrypto.dll", "sshd-session.exe", "sshd-auth.exe"])
def test_missing_companion_refuses_offline_reuse_without_removal(
    tmp_path: Path, missing: str,
) -> None:
    archive = _archive(tmp_path)
    destination = tmp_path / "selected"
    with zipfile.ZipFile(archive) as source:
        for entry in source.infolist():
            path = destination / Path(entry.filename).name
            destination.mkdir(exist_ok=True)
            path.write_bytes(source.read(entry))
    (destination / missing).unlink()
    before = {path.name: path.read_bytes() for path in destination.iterdir()}
    result = _run(tmp_path, archive, """
        function Save-AgentSshOpenSSHArchive { throw 'Unexpected network' }
        $message = ''
        try {
            Install-AgentSshOpenSSH -Destination (Join-Path $env:TEST_OPENSSH_ROOT 'selected')
        } catch { $message = $_.Exception.Message }
        @{Error=$message} | ConvertTo-Json -Compress
    """)
    assert missing in result["Error"]
    assert {path.name: path.read_bytes() for path in destination.iterdir()} == before


def test_older_portable_layout_does_not_require_new_split_server_binaries(tmp_path: Path) -> None:
    archive = _archive(tmp_path)
    destination = tmp_path / "selected"
    destination.mkdir()
    with zipfile.ZipFile(archive) as source:
        for entry in source.infolist():
            name = Path(entry.filename).name
            if name not in ("sshd-session.exe", "sshd-auth.exe"):
                (destination / name).write_bytes(source.read(entry))
    result = _run(tmp_path, archive, """
        function Get-AgentSshOpenSSHVersion { return [Version]'9.5' }
        $files = Get-AgentSshOpenSSHFiles (Join-Path $env:TEST_OPENSSH_ROOT 'selected')
        @{Ready=[bool]$files.Sshd} | ConvertTo-Json -Compress
    """)
    assert result["Ready"]
