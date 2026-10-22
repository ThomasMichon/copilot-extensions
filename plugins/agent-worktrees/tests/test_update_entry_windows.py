"""Executable Windows-only scratch ACL and exact native Git argv contracts."""
from __future__ import annotations

import json
import os
import subprocess

import pytest
from agent_procutil import no_window_kwargs
from test_update_entry import PLUGIN
from test_update_entry import update_entry_case as shared_update_entry_case

update_entry_case = shared_update_entry_case

pytestmark = pytest.mark.skipif(os.name != "nt", reason="native Windows security/process contract")


def test_update_entry_windows_scratch_token_user_acl(
    update_entry_case, native_powershell, monkeypatch,
):
    tmp, home, _resolver = update_entry_case
    root = home / ".worktree-manager"
    root.mkdir()
    (root / "current-version").write_text("missing-runtime\n")
    monkeypatch.setenv("ACL_PROOF", str(tmp / "acl.json"))
    harness = tmp / "acl.ps1"
    entry = str(PLUGIN / "bin/agent-worktrees.ps1").replace("'", "''")
    harness.write_text(
        r"""
function Get-NativeAcl([string]$Path) {
    $item = if ([IO.Directory]::Exists($Path)) {
        [IO.DirectoryInfo]::new($Path)
    } else { [IO.FileInfo]::new($Path) }
    if ($PSVersionTable.PSEdition -eq 'Core') {
        return [IO.FileSystemAclExtensions]::GetAccessControl($item)
    }
    return $item.GetAccessControl()
}
function Invoke-WebRequest {
    param($Uri, $OutFile, [switch]$UseBasicParsing)
    $root = Split-Path $OutFile
    $user = [Security.Principal.WindowsIdentity]::GetCurrent().User
    $acl = Get-NativeAcl $root
    $collisionRefused = $false
    try {
        [CopilotExtensions.UpdateScratchDirectory]::Create(
            $root, $acl.GetSecurityDescriptorBinaryForm())
    }
    catch { $collisionRefused = $_.Exception.InnerException.NativeErrorCode -eq 183 }
    $child = Join-Path $root 'child'
    [IO.Directory]::CreateDirectory($child) | Out-Null
    $probe = Join-Path $child 'probe'
    [IO.File]::WriteAllText($probe, 'probe')
    $rights = [Security.AccessControl.FileSystemRights]::FullControl
    $inherit = [Security.AccessControl.InheritanceFlags]::ContainerInherit -bor
        [Security.AccessControl.InheritanceFlags]::ObjectInherit
    $own = @($acl.Access | Where-Object {
        $_.IdentityReference.Translate([Security.Principal.SecurityIdentifier]) -eq $user -and
        ($_.FileSystemRights -band $rights) -eq $rights -and
        ($_.InheritanceFlags -band $inherit) -eq $inherit
    })
    $childAccess = @((Get-NativeAcl $probe).Access | Where-Object {
        $_.IdentityReference.Translate([Security.Principal.SecurityIdentifier]) -eq $user -and
        $_.IsInherited -and ($_.FileSystemRights -band $rights) -eq $rights
    })
    $allowed = @($user.Value, 'S-1-5-18', 'S-1-5-32-544')
    $foreign = @($acl.Access | Where-Object {
        $sid = $_.IdentityReference.Translate([Security.Principal.SecurityIdentifier])
        $sid.Value -notin $allowed
    })
    $proof = @{
        ownerIsTokenUser = $acl.GetOwner([Security.Principal.SecurityIdentifier]) -eq $user
        protected = $acl.AreAccessRulesProtected
        fullInheritedControl = $own.Count -gt 0
        childHasUserControl = $childAccess.Count -gt 0
        noForeignAce = $foreign.Count -eq 0
        collisionRefused = $collisionRefused
        unchangedAfterCollision = (Get-NativeAcl $root).Sddl -eq $acl.Sddl
        path = $root
    }
    [IO.File]::WriteAllText($env:ACL_PROOF, ($proof | ConvertTo-Json))
    [IO.File]::WriteAllText($OutFile, 'exit 23')
}
"""
        + f"& '{entry}' update\nexit $LASTEXITCODE\n",
    )
    result = subprocess.run(
        [native_powershell, "-NoProfile", "-File", str(harness)],
        capture_output=True, timeout=45, **no_window_kwargs(),
    )
    assert result.returncode == 23, result.stderr.decode(errors="replace")
    proof = json.loads((tmp / "acl.json").read_text())
    path = proof.pop("path")
    assert all(proof.values()), proof
    assert not os.path.exists(path)


@pytest.mark.parametrize("phase", ["healthy", "fetch-failure"])
def test_update_entry_windows_generic_git_exact_argv(
    update_entry_case, native_powershell, monkeypatch, phase,
):
    tmp, home, _resolver = update_entry_case
    root = home / ".worktree-manager"
    root.mkdir()
    repo = r"C:\source with spaces\selected.git"
    ref = "refs/tags/selected"
    (root / "config.toml").write_text(
        f"[source]\nrepo = {json.dumps(repo)}\nref = {json.dumps(ref)}\n",
    )
    (tmp / "tools/git").unlink()
    monkeypatch.setenv("GIT_ARGS", str(tmp / "git.args"))
    monkeypatch.setenv("GIT_FAIL", "42" if phase == "fetch-failure" else "0")
    downloaded = tmp / "download.ps1"
    downloaded.write_text(
        '$json = ConvertTo-Json -Compress -InputObject @($args)\n'
        '[IO.File]::WriteAllText($env:BOOT_ARGS, $json)\nexit 23\n',
    )
    monkeypatch.setenv("DOWNLOAD_SCRIPT", str(downloaded))
    harness = tmp / "git.ps1"
    entry = str(PLUGIN / "bin/agent-worktrees.ps1").replace("'", "''")
    executable = str(tmp / "tools/git.exe").replace("'", "''")
    harness.write_text(
        r"""
if ($PSVersionTable.PSEdition -eq 'Core') { exit 64 }
Add-Type -TypeDefinition @'
using System;
using System.IO;
public class UpdateGitDouble {
    public static int Main(string[] args) {
        File.AppendAllText(Environment.GetEnvironmentVariable("GIT_ARGS"),
            String.Join("\0", args) + "\0\n");
        if (args[0] == "init") {
            Directory.CreateDirectory(args[args.Length - 1]);
        } else if (Array.IndexOf(args, "fetch") >= 0) {
            return Int32.Parse(Environment.GetEnvironmentVariable("GIT_FAIL"));
        } else if (Array.IndexOf(args, "checkout") >= 0) {
            string destination = Path.Combine(args[1], "worktree-manager");
            Directory.CreateDirectory(destination);
            File.Copy(Environment.GetEnvironmentVariable("DOWNLOAD_SCRIPT"),
                Path.Combine(destination, "bootstrap.ps1"));
        } else { return 99; }
        return 0;
    }
}
'@ -OutputType ConsoleApplication -OutputAssembly """
        + f"'{executable}'\n& '{entry}' --project sample update --force\nexit $LASTEXITCODE\n",
    )
    result = subprocess.run(
        [native_powershell, "-NoProfile", "-File", str(harness)],
        capture_output=True, timeout=45, **no_window_kwargs(),
    )
    if result.returncode == 64:
        pytest.skip("fake executable compilation requires Windows PowerShell/.NET Framework")
    expected = 42 if phase == "fetch-failure" else 23
    assert result.returncode == expected, result.stderr.decode(errors="replace")
    calls = [
        line.decode().split("\0")[:-1] for line in (tmp / "git.args").read_bytes().splitlines()
    ]
    checkout = calls[0][-1]
    assert calls[1] == [
        "-C", checkout, "fetch", "--quiet", "--depth", "1", "--", repo, ref,
    ]
    if phase == "healthy":
        assert json.loads((tmp / "bootstrap.args").read_text()) == [
            "--repair-update", repo, ref, "update", "--project", "sample", "--force",
        ]
    else:
        assert not (tmp / "bootstrap.args").exists()
    assert not os.path.exists(os.path.dirname(checkout))
    assert not (tmp / "curl.args").exists()
