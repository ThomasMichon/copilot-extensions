# Dot-sourced before runtime resolution by the installed and payload front doors.
$_awUpdateHandled = $false
$_awUpdateDiagnostic = $false
$_awUpdateStandalone = $null -ne [Environment]::GetEnvironmentVariable(
    'AGENT_WORKTREES_INTERNAL_UPDATE_PAYLOAD'
)
if ($_awUpdateStandalone) {
    $_awUpdatePayload = $env:AGENT_WORKTREES_INTERNAL_UPDATE_PAYLOAD -ceq '1'
    $_awUpdateCaller = if ($_awUpdatePayload) {
        Join-Path (Split-Path -Parent $PSScriptRoot) 'scripts\invoke-payload-runtime.ps1'
    } else { Join-Path $PSScriptRoot 'agent-worktrees.ps1' }
    Remove-Item Env:AGENT_WORKTREES_INTERNAL_UPDATE_PAYLOAD -ErrorAction SilentlyContinue
}
if ($env:AGENT_WORKTREES_INTERNAL_SKIP_UPDATE_BOOTSTRAP -ceq '1') {
    Remove-Item Env:AGENT_WORKTREES_INTERNAL_SKIP_UPDATE_BOOTSTRAP -ErrorAction SilentlyContinue
    return
}
$_originalUpdateArgs = @($args)
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
    $_ -cin @('--phase', '--plugin', '--inventory', '--target', '--runtime-backend') -or
    $_.StartsWith('--phase=') -or $_.StartsWith('--plugin=') -or $_.StartsWith('--target=') -or
    $_.StartsWith('--runtime-backend=')
}).Count) {
    $_awUpdateDiagnostic = $true
    return
}
if (@($_updateArgs | Where-Object {
    $_ -cin @('--auth-report', '--help', '-h',
        '--request-status', '--recovery-status', '--recover-after-restart', '--recover-for-repair') -or
    $_.StartsWith('--request-status=')
}).Count) { return }
if ([Environment]::OSVersion.Platform -eq [PlatformID]::Win32NT -and
    $_updateArgs -cnotcontains '--supervised-debug') {
    try {
        if (-not ('CopilotExtensions.UpdateBreakawayProbe' -as [type])) {
            Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
namespace CopilotExtensions {
    public static class UpdateBreakawayProbe {
        [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
        private struct StartupInfo {
            public int Size;
            public string Reserved, Desktop, Title;
            public int X, Y, XSize, YSize, XCountChars, YCountChars, FillAttribute, Flags;
            public short ShowWindow, ReservedSize;
            public IntPtr ReservedBytes, StandardInput, StandardOutput, StandardError;
        }
        [StructLayout(LayoutKind.Sequential)]
        private struct ProcessInformation {
            public IntPtr Process, Thread;
            public int ProcessId, ThreadId;
        }
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, ExactSpelling = true, SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        private static extern bool CreateProcessW(
            string application, string commandLine, IntPtr processAttributes, IntPtr threadAttributes,
            bool inheritHandles, int flags, IntPtr environment, string directory,
            ref StartupInfo startup, out ProcessInformation process);
        [DllImport("kernel32.dll", SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        private static extern bool TerminateProcess(IntPtr process, int exitCode);
        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern int WaitForSingleObject(IntPtr handle, int milliseconds);
        [DllImport("kernel32.dll")]
        [return: MarshalAs(UnmanagedType.Bool)]
        private static extern bool CloseHandle(IntPtr handle);
        public static int Check(string executable) {
            var startup = new StartupInfo { Size = Marshal.SizeOf(typeof(StartupInfo)) };
            ProcessInformation process;
            // Suspended and windowless: no shell code runs during the capability probe.
            if (!CreateProcessW(executable, null, IntPtr.Zero, IntPtr.Zero, false,
                    0x09000004, IntPtr.Zero, null, ref startup, out process))
                return Marshal.GetLastWin32Error();
            try {
                if (!TerminateProcess(process.Process, 0))
                    throw new Win32Exception(Marshal.GetLastWin32Error(), "Breakaway probe cleanup failed");
                int wait = WaitForSingleObject(process.Process, 5000);
                if (wait != 0)
                    throw new Win32Exception(
                        wait == -1 ? Marshal.GetLastWin32Error() : wait,
                        "Breakaway probe cleanup was not confirmed");
                return 0;
            } finally {
                CloseHandle(process.Thread);
                CloseHandle(process.Process);
            }
        }
    }
}
'@
        }
        $_breakawayError = [CopilotExtensions.UpdateBreakawayProbe]::Check((Get-Process -Id $PID).Path)
        if ($_breakawayError -ne 0) {
            throw "Windows refused the required breakaway process (WinError $_breakawayError)."
        }
    } catch {
        [Console]::Error.WriteLine(
            "[agent-worktrees] Update launch preflight failed: $($_.Exception.Message) " +
            'No update was started or request submitted. Re-run in the execution host''s ' +
            'explicit detached/outside-Job shell mode (for example async with detach=true). ' +
            'Plain async execution or update --detach does not remove an enclosing Job. ' +
            'Do not retry in the same bounded shell.'
        )
        if ($_awUpdateStandalone) { exit 1 }
        $_awUpdateHandled = $true
        $global:LASTEXITCODE = 1
        return
    }
}
# The Git-sh handoff disables conversion only for the native invocation.
Remove-Item Env:MSYS2_ARG_CONV_EXCL -ErrorAction SilentlyContinue
if ($_updateArgs -ccontains '--no-manager') {
    if ($_awUpdateStandalone) {
        if (-not (Test-Path -LiteralPath $_awUpdateCaller -PathType Leaf)) {
            [Console]::Error.WriteLine('[agent-worktrees] native update caller is unavailable; no update ran.')
            exit 127
        }
        $env:AGENT_WORKTREES_INTERNAL_SKIP_UPDATE_BOOTSTRAP = '1'
        & $_awUpdateCaller @_originalUpdateArgs
        exit $LASTEXITCODE
    }
    return
}
if (-not $_updateHasProject) {
    for ($_index = 1; $_index -lt $_updateArgs.Count; $_index++) {
        $_argument = $_updateArgs[$_index]
        if ($_argument -cin @('--project', '-p') -and $_index + 1 -lt $_updateArgs.Count) {
            $_updateProject = $_updateArgs[$_index + 1]
            $_updateHasProject = $true
            break
        }
        if ($_argument.StartsWith('--project=')) {
            $_updateProject = $_argument.Substring(10)
            $_updateHasProject = $true
            break
        }
    }
}
$_probeExcluded = @($_updateArgs | Where-Object {
    $_ -cin @('--no-project-controller', '--repair-engine') -or $_.StartsWith('--repair-engine=')
}).Count -gt 0
$_runtimeRoot = Join-Path $env:USERPROFILE '.agent-worktrees'
$_probePossible = @('current-version', 'last-known-good', 'versions') |
    Where-Object { Test-Path -LiteralPath (Join-Path $_runtimeRoot $_) }
if ($_awUpdatePayload -and (
    $env:COPILOT_EXTENSIONS_CONTEXT -or
    (Test-Path -LiteralPath (Join-Path $env:USERPROFILE '.copilot-extensions\installation-mode.json'))
)) { $_probePossible = $true }
if (-not $_probeExcluded -and $_probePossible -and $_awUpdateCaller) {
    if ($_awUpdatePayload) {
        $_probePayload = Split-Path -Parent (Split-Path -Parent $_awUpdateCaller)
    } else {
        $_probePayload = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
        if (-not (Test-Path -LiteralPath (Join-Path $_probePayload 'plugin.json') -PathType Leaf)) {
            $_probePayload = ''
            try { $_probePayload = ([IO.File]::ReadAllText((Join-Path $_runtimeRoot 'payload-dir'))).Trim() } catch {}
        }
    }
    $_probeHelper = if ($_probePayload) {
        Join-Path $_probePayload 'scripts\installation-context\shell-caller-owned.ps1'
    } else { '' }
    $_probeWorker = if ($_probePayload) {
        Join-Path $_probePayload 'scripts\update_controller_probe.py'
    } else { '' }
    $_controller = ''
    if ($_probeHelper -and (Test-Path -LiteralPath $_probeHelper -PathType Leaf) -and
        (Test-Path -LiteralPath $_probeWorker -PathType Leaf)) {
        $_request = @{
            host = (Get-Process -Id $PID).Path
            caller = $_awUpdateCaller
            project = $_updateProject
            has_project = $_updateHasProject
            cwd = (Get-Location).Path
        }
        $_previousEap = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        try {
            $_probeOutput = @(& $_probeHelper -Worker $_probeWorker -Request $_request -TimeoutSeconds 30 2>$null)
            $_probeRc = $LASTEXITCODE
        } finally { $ErrorActionPreference = $_previousEap }
        if ($_probeRc -eq 124) {
            [Console]::Error.WriteLine('[agent-worktrees] controller probe timed out; no update ran.')
            if ($_awUpdateStandalone) { exit 124 }
            $_awUpdateHandled = $true
            $global:LASTEXITCODE = 124
            return
        }
        if ($_probeRc -eq 0) { $_controller = ($_probeOutput -join "`n").Trim() }
    }
    if ($_controller) {
        $_previousSkip = $env:AGENT_WORKTREES_INTERNAL_SKIP_UPDATE_BOOTSTRAP
        $_previousController = $env:AGENT_WORKTREES_INTERNAL_PROJECT_CONTROLLER
        try {
            $env:AGENT_WORKTREES_INTERNAL_SKIP_UPDATE_BOOTSTRAP = '1'
            $env:AGENT_WORKTREES_INTERNAL_PROJECT_CONTROLLER = '1'
            & $_awUpdateCaller @_originalUpdateArgs
            $_controllerRc = $LASTEXITCODE
        } finally {
            if ($null -eq $_previousSkip) {
                Remove-Item Env:AGENT_WORKTREES_INTERNAL_SKIP_UPDATE_BOOTSTRAP -ErrorAction SilentlyContinue
            } else { $env:AGENT_WORKTREES_INTERNAL_SKIP_UPDATE_BOOTSTRAP = $_previousSkip }
            if ($null -eq $_previousController) {
                Remove-Item Env:AGENT_WORKTREES_INTERNAL_PROJECT_CONTROLLER -ErrorAction SilentlyContinue
            } else { $env:AGENT_WORKTREES_INTERNAL_PROJECT_CONTROLLER = $_previousController }
        }
        if ($_awUpdateStandalone) { exit $_controllerRc }
        $_awUpdateHandled = $true
        $global:LASTEXITCODE = $_controllerRc
        return
    }
}
$_awUpdateHandled = $true
$ErrorActionPreference = 'Stop'
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
# Bootstrap transport is independent of the runtime source read by bootstrap.
$_updateRepo = 'https://github.com/ThomasMichon/copilot-extensions.git'
$_updateRef = 'main'
if ($null -ne [Environment]::GetEnvironmentVariable('WORKTREE_MANAGER_BOOTSTRAP_REPO')) {
    $_updateRepo = $env:WORKTREE_MANAGER_BOOTSTRAP_REPO
}
if ($null -ne [Environment]::GetEnvironmentVariable('WORKTREE_MANAGER_BOOTSTRAP_REF')) {
    $_updateRef = $env:WORKTREE_MANAGER_BOOTSTRAP_REF
}
if (-not $_updateRepo -or $_updateRepo.StartsWith('-') -or -not $_updateRef -or
    $_updateRef.StartsWith('-') -or $_updateRepo -match '[\x00-\x1f\x7f]' -or
    $_updateRef -match '[\x00-\x1f\x7f]') {
    throw 'Invalid bootstrap transport source/ref; runtime source preserved.'
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
        & $_updateFile --bootstrap-update @_forwardUpdate
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
