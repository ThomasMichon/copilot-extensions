# Dot-sourced before runtime resolution by the installed and payload front doors.
$_awUpdateHandled = $false
$_awUpdateDiagnostic = $false
$_updateArgs = @($args)
$_updateProject = ''
$_updateHasProject = $false
if ($_updateArgs.Count -ge 3 -and $_updateArgs[0] -cin @('--project', '-p')) {
    $_updateProject = $_updateArgs[1]
    $_updateHasProject = $true
    $_updateArgs = @($_updateArgs | Select-Object -Skip 2)
} elseif ($_updateArgs.Count -ge 2 -and $_updateArgs[0].StartsWith('--project=')) {
    $_updateProject = $_updateArgs[0].Substring(10)
    $_updateHasProject = $true
    $_updateArgs = @($_updateArgs | Select-Object -Skip 1)
}
if ($_updateArgs.Count -eq 0 -or $_updateArgs[0] -cne 'update') { return }
if (@($_updateArgs | Where-Object {
    $_ -cin @('--phase', '--plugin') -or
    $_.StartsWith('--phase=') -or $_.StartsWith('--plugin=')
}).Count) {
    $_awUpdateDiagnostic = $true
    return
}
if (@($_updateArgs | Where-Object {
    $_ -cin @('--no-manager', '--auth-report', '--help', '-h',
        '--request-status', '--recovery-status', '--recover-after-restart', '--recover-for-repair') -or
    $_.StartsWith('--request-status=')
}).Count) { return }
$_awUpdateHandled = $true
$ErrorActionPreference = 'Stop'
# The Git-sh handoff disables conversion for this native invocation only.
# Retaining it would also disable Git's own file-transport path conversion.
Remove-Item Env:MSYS2_ARG_CONV_EXCL -ErrorAction SilentlyContinue
if ($env:AGENT_WORKTREES_CONTROL_PLANE_PROVIDER -and
    $env:AGENT_WORKTREES_CONTROL_PLANE_PROVIDER -cne 'worktree-manager') {
    throw 'Update bootstrap requires the selected Manager provider.'
}
$_updateRoot = if ($env:WORKTREE_MANAGER_ROOT) { $env:WORKTREE_MANAGER_ROOT } else { Join-Path $HOME '.worktree-manager' }
$_updateRegistry = if ($env:AGENT_WORKTREES_CONTROL_PLANE_PROVIDERS_DIR) {
    $env:AGENT_WORKTREES_CONTROL_PLANE_PROVIDERS_DIR
} else { Join-Path $HOME '.agent-worktrees\control-plane-providers.d' }
$_updateRegistrations = @()
if (Test-Path -LiteralPath $_updateRegistry) {
    $_updateRegistrations = @(Get-ChildItem -LiteralPath $_updateRegistry -Filter '*.json' -File)
}
$_registeredRoot = ''
foreach ($_registration in $_updateRegistrations) {
    $_provider = [IO.File]::ReadAllText($_registration.FullName) | ConvertFrom-Json
    if ($_provider.provider -ceq 'worktree-manager') {
        if ($_registeredRoot) { throw 'Duplicate Manager registration.' }
        $_registeredRoot = $_provider.provider_root
        if (-not ($_registeredRoot -is [string]) -or -not [IO.Path]::IsPathRooted($_registeredRoot)) {
            throw 'Manager root is not absolute.'
        }
    } elseif (-not $env:AGENT_WORKTREES_CONTROL_PLANE_PROVIDER) {
        throw 'Select the Manager provider explicitly; provider preserved.'
    }
}
if ($_registeredRoot) {
    if ($env:WORKTREE_MANAGER_ROOT -and $env:WORKTREE_MANAGER_ROOT -cne $_registeredRoot) {
        throw 'Manager root disagrees with its registration.'
    }
    $_updateRoot = $_registeredRoot
} elseif ($_updateRegistrations.Count -or $env:AGENT_WORKTREES_CONTROL_PLANE_PROVIDER) {
    throw 'Selected Manager is not registered.'
}
# Publication-owned bootstrap defaults; never inspect the selected runtime.
$_updateRepo = 'https://github.com/ThomasMichon/copilot-extensions.git'
$_updateRef = 'main'
function ConvertFrom-UpdateSourceString([string]$Text) {
    if (-not $Text -or $Text[0] -cnotin @([char]'"', [char]"'")) {
        throw 'Unsupported or malformed source string.'
    }
    $quote = $Text[0]
    $value = [Text.StringBuilder]::new()
    for ($i = 1; $i -lt $Text.Length; $i++) {
        $character = $Text[$i]
        if ($character -ceq $quote) {
            if ($Text.Substring($i + 1) -notmatch '^\s*(#.*)?$' -or -not $value.Length) {
                throw 'Unsupported or malformed source string.'
            }
            return $value.ToString()
        }
        if ([char]::IsControl($character)) { throw 'Control characters in source string.' }
        if ($quote -ceq [char]'"' -and $character -ceq [char]'\') {
            $i++
            if ($i -ge $Text.Length -or $Text[$i] -cnotin @([char]'\', [char]'"')) {
                throw 'Unsupported or malformed source escape.'
            }
            $character = $Text[$i]
        }
        [void]$value.Append($character)
    }
    throw 'Unterminated source string.'
}
$_updateConfig = Join-Path $_updateRoot 'config.toml'
if (Test-Path -LiteralPath $_updateConfig) {
    $_inSource = $false
    $_seen = @{}
    $_sourceSeen = $false
    foreach ($_line in [IO.File]::ReadAllLines($_updateConfig)) {
        if ($_line -match '^\s*\[') {
            $_inSource = $_line -cmatch '^\s*\[source\]\s*(#.*)?$'
            if ($_line -cmatch 'source' -and -not $_inSource) { throw 'Unsupported source table syntax.' }
            if ($_inSource -and $_sourceSeen) { throw 'Duplicate source table.' }
            if ($_inSource) { $_sourceSeen = $true }
            continue
        }
        if (-not $_inSource -and $_line -cmatch '^\s*["'']?source["'']?[\s.=]') { throw 'Unsupported inline source table.' }
        if ($_inSource -and $_line -cmatch '^\s*(repo|ref)\s*=\s*(.*)$') {
            $_key = $Matches[1]
            $_encodedValue = $Matches[2]
            if ($_seen.ContainsKey($_key)) { throw 'Duplicate source key.' }
            $_seen[$_key] = $true
            $_value = ConvertFrom-UpdateSourceString $_encodedValue
            if ($_key -ceq 'repo') { $_updateRepo = $_value } else { $_updateRef = $_value }
        } elseif ($_inSource -and $_line -notmatch '^\s*(#.*)?$') {
            throw 'Unsupported source key syntax.'
        }
    }
}
if (-not $_updateRepo -or $_updateRepo.StartsWith('-') -or -not $_updateRef -or $_updateRef.StartsWith('-')) {
    throw 'Invalid configured Git source/ref; source preserved.'
}
$_updateUrl = ''
if ($_updateRepo -cmatch '^https://github\.com/([A-Za-z0-9._-]+)/([A-Za-z0-9._-]+?)(?:\.git)?/?$') {
    $_updateSlug = "$($Matches[1])/$($Matches[2])"
    if ($_updateRef -cmatch '^[A-Za-z0-9._/][A-Za-z0-9._/-]*$') {
        $_updateUrl = "https://raw.githubusercontent.com/$_updateSlug/$_updateRef/worktree-manager/bootstrap.ps1"
    }
}
function New-UpdateScratchDirectory {
    $base = if ($env:AGENT_SCRATCH_ROOT) { $env:AGENT_SCRATCH_ROOT } else { [IO.Path]::GetTempPath() }
    $prefix = 'agent-update-' + [DateTime]::UtcNow.ToString('yyyyMMdd-HHmmss') + '-'
    if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
        $path = & mktemp -d (Join-Path $base ($prefix + 'XXXXXX'))
        if ($LASTEXITCODE -ne 0) { throw 'Private update scratch creation failed.' }
        return $path
    }
    if (-not ('CopilotExtensions.UpdateScratchDirectory' -as [type])) {
        Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
namespace CopilotExtensions {
    public static class UpdateScratchDirectory {
        [StructLayout(LayoutKind.Sequential)]
        private struct SecurityAttributes {
            public int Length;
            public IntPtr Descriptor;
            [MarshalAs(UnmanagedType.Bool)] public bool InheritHandle;
        }
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, ExactSpelling = true, SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        private static extern bool CreateDirectoryW(string path, ref SecurityAttributes attributes);
        public static void Create(string path, byte[] descriptor) {
            var pinned = GCHandle.Alloc(descriptor, GCHandleType.Pinned);
            try {
                var attributes = new SecurityAttributes {
                    Length = Marshal.SizeOf(typeof(SecurityAttributes)),
                    Descriptor = pinned.AddrOfPinnedObject(),
                    InheritHandle = false
                };
                if (!CreateDirectoryW(path, ref attributes))
                    throw new Win32Exception(Marshal.GetLastWin32Error());
            } finally { pinned.Free(); }
        }
    }
}
'@
    }
    $user = [Security.Principal.WindowsIdentity]::GetCurrent().User
    $security = [Security.AccessControl.DirectorySecurity]::new()
    $security.SetOwner($user)
    $security.SetAccessRuleProtection($true, $false)
    $inheritance = [Security.AccessControl.InheritanceFlags]::ContainerInherit -bor
        [Security.AccessControl.InheritanceFlags]::ObjectInherit
    foreach ($sid in @($user,
        [Security.Principal.SecurityIdentifier]::new('S-1-5-18'),
        [Security.Principal.SecurityIdentifier]::new('S-1-5-32-544'))) {
        $security.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new(
            $sid, [Security.AccessControl.FileSystemRights]::FullControl,
            $inheritance, [Security.AccessControl.PropagationFlags]::None,
            [Security.AccessControl.AccessControlType]::Allow))
    }
    $path = Join-Path $base ($prefix + [guid]::NewGuid().ToString('N'))
    [CopilotExtensions.UpdateScratchDirectory]::Create($path, $security.GetSecurityDescriptorBinaryForm())
    return $path
}
$_updateGitRouting = @('GIT_DIR', 'GIT_WORK_TREE', 'GIT_COMMON_DIR', 'GIT_INDEX_FILE',
    'GIT_PREFIX', 'GIT_OBJECT_DIRECTORY', 'GIT_ALTERNATE_OBJECT_DIRECTORIES')
function Invoke-UpdateGit([string[]]$Arguments) {
    $git = Get-Command git -CommandType Application -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if (-not $git) { throw 'Git is required for the configured bootstrap source; source preserved.' }
    $start = [Diagnostics.ProcessStartInfo]::new()
    $start.FileName = $git.Source
    $start.UseShellExecute = $false
    $start.CreateNoWindow = $true
    $start.RedirectStandardOutput = $true
    $start.RedirectStandardError = $true
    foreach ($name in $_updateGitRouting) { $start.EnvironmentVariables.Remove($name) }
    if ($start.PSObject.Properties['ArgumentList']) {
        foreach ($part in $Arguments) { [void]$start.ArgumentList.Add($part) }
    } else {
        $quoted = foreach ($part in $Arguments) {
            $escaped = [regex]::Replace($part, '(\\*)"', '$1$1\"')
            '"' + [regex]::Replace($escaped, '(\\+)$', '$1$1') + '"'
        }
        $start.Arguments = $quoted -join ' '
    }
    $process = [Diagnostics.Process]::new()
    $process.StartInfo = $start
    try {
        if (-not $process.Start()) { throw 'Git bootstrap child did not start.' }
        $stdout = $process.StandardOutput.ReadToEndAsync()
        $stderr = $process.StandardError.ReadToEndAsync()
        $process.WaitForExit()
        [Console]::Out.Write($stdout.GetAwaiter().GetResult())
        [Console]::Error.Write($stderr.GetAwaiter().GetResult())
        return $process.ExitCode
    } finally { $process.Dispose() }
}
$_updateScript = New-UpdateScratchDirectory
$_updateFile = Join-Path $_updateScript 'bootstrap.ps1'
try {
    if ($_updateUrl) {
        Add-Type -AssemblyName System.Net.Http
        try {
            Invoke-WebRequest -Uri $_updateUrl -OutFile $_updateFile -UseBasicParsing
        } catch [Net.WebException], [System.Net.Http.HttpRequestException] {
            if (-not (Get-Command git -CommandType Application -ErrorAction SilentlyContinue)) { throw }
            [Console]::Error.WriteLine("Raw bootstrap download failed: $_")
            [Console]::Error.WriteLine('Fetching the same configured Git source with normal Git credentials.')
            $_updateUrl = ''
        }
    }
    if (-not $_updateUrl) {
        if ($_updateRepo -notmatch '^[^/\\:]+:' -or [IO.Path]::IsPathRooted($_updateRepo)) {
            if (-not [IO.Path]::IsPathRooted($_updateRepo)) {
                $_updateRepo = Join-Path (Get-Location).Path $_updateRepo
            }
            $_updateRepo = [IO.Path]::GetFullPath($_updateRepo)
        }
        $_updateCheckout = Join-Path $_updateScript 'source'
        $_gitRc = Invoke-UpdateGit -Arguments @('init', '--quiet', '--', $_updateCheckout)
        if ($_gitRc -ne 0) { exit $_gitRc }
        $_gitRc = Invoke-UpdateGit -Arguments @('-C', $_updateCheckout, 'fetch', '--quiet', '--depth', '1', '--', $_updateRepo, $_updateRef)
        if ($_gitRc -ne 0) { exit $_gitRc }
        $_gitRc = Invoke-UpdateGit -Arguments @('-C', $_updateCheckout, '-c', 'core.hooksPath=', 'checkout', '--quiet', '--detach', 'FETCH_HEAD')
        if ($_gitRc -ne 0) { exit $_gitRc }
        $_updateFile = Join-Path $_updateCheckout 'worktree-manager\bootstrap.ps1'
        if (-not (Test-Path -LiteralPath $_updateFile -PathType Leaf) -or
            ([IO.File]::GetAttributes($_updateFile) -band [IO.FileAttributes]::ReparsePoint) -or
            ([IO.File]::GetAttributes((Split-Path $_updateFile)) -band [IO.FileAttributes]::ReparsePoint)) {
            throw 'Configured Git source has no ordinary Manager bootstrap.ps1.'
        }
    }
    $_forwardUpdate = @('update')
    if ($_updateHasProject) { $_forwardUpdate += @('--project', $_updateProject) }
    $_forwardUpdate += @($_updateArgs | Select-Object -Skip 1)
    # In-process script invocation preserves the parent's Job/console containment.
    $_savedUpdateRoot = $env:WORKTREE_MANAGER_ROOT
    $_savedUpdateContext = $env:AGENT_WORKTREES_UPDATE_CONTEXT
    $_savedUpdateGit = @{}
    try {
        foreach ($_name in $_updateGitRouting) {
            $_savedUpdateGit[$_name] = [Environment]::GetEnvironmentVariable($_name)
            [Environment]::SetEnvironmentVariable(
                $_name, [System.Management.Automation.Language.NullString]::Value)
        }
        $env:WORKTREE_MANAGER_ROOT = $_updateRoot
        if (-not $env:AGENT_WORKTREES_UPDATE_CONTEXT) {
            $env:AGENT_WORKTREES_UPDATE_CONTEXT = (Get-Location).Path
        }
        & $_updateFile --repair-update $_updateRepo $_updateRef @_forwardUpdate
        $_updateRc = $LASTEXITCODE
    } finally {
        if ($null -eq $_savedUpdateRoot) {
            Remove-Item Env:WORKTREE_MANAGER_ROOT -ErrorAction SilentlyContinue
        } else { $env:WORKTREE_MANAGER_ROOT = $_savedUpdateRoot }
        if ($null -eq $_savedUpdateContext) {
            Remove-Item Env:AGENT_WORKTREES_UPDATE_CONTEXT -ErrorAction SilentlyContinue
        } else { $env:AGENT_WORKTREES_UPDATE_CONTEXT = $_savedUpdateContext }
        foreach ($_name in $_savedUpdateGit.Keys) {
            if ($null -eq $_savedUpdateGit[$_name]) {
                [Environment]::SetEnvironmentVariable(
                    $_name, [System.Management.Automation.Language.NullString]::Value)
            } else {
                [Environment]::SetEnvironmentVariable($_name, $_savedUpdateGit[$_name])
            }
        }
    }
} finally {
    Remove-Item -LiteralPath $_updateScript -Recurse -Force
}
exit $_updateRc
