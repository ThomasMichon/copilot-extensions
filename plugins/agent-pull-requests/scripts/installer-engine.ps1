<#
.SYNOPSIS
    Canonical vendored installer-engine helpers for runtime plugins.

.DESCRIPTION
    This file is the canonical source of truth for the shared installer-engine
    helpers that runtime plugins use in one of two valid dev-time forms:
    (1) a canonical-reference adopter dot-sources this file directly from
    `libs/installer-engine/` while working in `dev`, and promotion
    materializes a real plugin-local `scripts/installer-engine.ps1` copy back
    into the shipped payload; or (2) a byte-vendored adopter keeps that
    plugin-local copy on `dev`, kept byte-identical by
    `tools/sync-installer-engine.py`. In the shipped artifact, plugins never
    source this file across plugin boundaries.
#>

# Packaged human-output initialization: installer-human-output.ps1
$script:InstallerDependencyPolicy = Join-Path $PSScriptRoot 'installer_dependency_policy.py'

function Remove-InstallRuntimeEntry {
    param([Parameter(Mandatory)][System.IO.FileSystemInfo]$Entry)

    if ($Entry.Attributes -band [System.IO.FileAttributes]::ReparsePoint) {
        if ($Entry.Attributes -band [System.IO.FileAttributes]::Directory) {
            [System.IO.Directory]::Delete($Entry.FullName)
        } else {
            [System.IO.File]::Delete($Entry.FullName)
        }
        return
    }
    if ($Entry.Attributes -band [System.IO.FileAttributes]::Directory) {
        foreach ($child in @(Get-ChildItem -LiteralPath $Entry.FullName -Force -ErrorAction Stop)) {
            Remove-InstallRuntimeEntry -Entry $child
        }
    }
    if ($Entry.Attributes -band [System.IO.FileAttributes]::ReadOnly) {
        $Entry.Attributes = $Entry.Attributes -band (-bnot [System.IO.FileAttributes]::ReadOnly)
    }
    if ($Entry.Attributes -band [System.IO.FileAttributes]::Directory) {
        [System.IO.Directory]::Delete($Entry.FullName)
    } else {
        [System.IO.File]::Delete($Entry.FullName)
    }
}

function Get-InstallDirectoryIdentity {
    param([Parameter(Mandatory)][string]$Path)

    if (-not ('CeInstallerDirectoryIdentity' -as [type])) {
        Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;

public static class CeInstallerDirectoryIdentity {
    [StructLayout(LayoutKind.Sequential)]
    private struct DirectoryInformation {
        public uint Attributes;
        public System.Runtime.InteropServices.ComTypes.FILETIME CreationTime;
        public System.Runtime.InteropServices.ComTypes.FILETIME LastAccessTime;
        public System.Runtime.InteropServices.ComTypes.FILETIME LastWriteTime;
        public uint VolumeSerialNumber;
        public uint SizeHigh;
        public uint SizeLow;
        public uint Links;
        public uint FileIndexHigh;
        public uint FileIndexLow;
    }

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern SafeFileHandle CreateFile(
        string path, uint access, uint share, IntPtr security,
        uint creation, uint flags, IntPtr template);

    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool GetFileInformationByHandle(
        SafeFileHandle handle, out DirectoryInformation information);

    [DllImport("libc", EntryPoint = "realpath", SetLastError = true)]
    private static extern IntPtr RealPath(string path, IntPtr buffer);

    [DllImport("libc", EntryPoint = "free")]
    private static extern void Free(IntPtr pointer);

    public static string Read(string path) {
        if (Environment.OSVersion.Platform != PlatformID.Win32NT) {
            IntPtr resolved = RealPath(path, IntPtr.Zero);
            if (resolved == IntPtr.Zero) {
                throw new Win32Exception(Marshal.GetLastWin32Error());
            }
            try { return Marshal.PtrToStringAnsi(resolved); }
            finally { Free(resolved); }
        }
        using (SafeFileHandle handle = CreateFile(
            path, 0, 7, IntPtr.Zero, 3, 0x02000000, IntPtr.Zero)) {
            if (handle.IsInvalid) {
                throw new Win32Exception(Marshal.GetLastWin32Error());
            }
            DirectoryInformation information;
            if (!GetFileInformationByHandle(handle, out information)) {
                throw new Win32Exception(Marshal.GetLastWin32Error());
            }
            return string.Format(
                "{0:x8}:{1:x8}{2:x8}", information.VolumeSerialNumber,
                information.FileIndexHigh, information.FileIndexLow);
        }
    }
}
'@
    }
    return [CeInstallerDirectoryIdentity]::Read($Path)
}

function Remove-InstallRuntimeContent {
    <# The caller holds the install transaction lock. Never unlink that lock or
       another invocation's staged source while removing runtime/config data. #>
    param([Parameter(Mandatory)][string]$InstallRoot)

    $full = [System.IO.Path]::GetFullPath($InstallRoot)
    $root = $full.TrimEnd([char[]]@('\', '/'))
    if ($root -eq ([System.IO.Path]::GetPathRoot($full)).TrimEnd([char[]]@('\', '/'))) {
        throw 'Refusing to purge a filesystem root'
    }
    $directory = Get-Item -LiteralPath $full -Force -ErrorAction Stop
    if (-not $directory.PSIsContainer -or
        ($directory.Attributes -band [System.IO.FileAttributes]::ReparsePoint)) {
        throw 'Runtime purge requires a real install directory, not a link'
    }
    $identity = Get-InstallDirectoryIdentity -Path $full
    foreach ($homePath in @($HOME, $env:USERPROFILE, $env:HOME)) {
        if ($homePath -and (Test-Path -LiteralPath $homePath -PathType Container -ErrorAction Stop) -and
            $identity -ceq (Get-InstallDirectoryIdentity -Path ([IO.Path]::GetFullPath($homePath)))) {
            throw 'Refusing to purge a user home'
        }
    }
    foreach ($entry in @(Get-ChildItem -LiteralPath $full -Force -ErrorAction Stop)) {
        if ($entry.Name -in @('.install.lock', '.install.lock.pid', '.install.lock.pid.reap', '.install-stage')) {
            continue
        }
        Remove-InstallRuntimeEntry -Entry $entry
    }
}

function Invoke-NativeCapture {
    param([Parameter(Mandatory)][scriptblock]$Command)

    $previousErrorAction = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $exitCode = 1
    $output = ''
    try {
        $output = (& $Command 2>&1 | Out-String -Width 4096).Trim()
        $exitCode = $LASTEXITCODE
    } catch {
        $output = ($_ | Out-String -Width 4096).Trim()
    } finally {
        $ErrorActionPreference = $previousErrorAction
    }
    return [pscustomobject]@{ ExitCode = $exitCode; Output = $output }
}

function Test-IsSreModuleMismatch {
    param([string]$Output)
    return $Output -match 'SRE module mismatch'
}

function Test-IsVenvCorruption {
    param([string]$Output)
    return ($Output -match 'failed to locate pyvenv\.cfg') -or ($Output -match 'exit code:\s*106')
}

function Invoke-UvPipInstallResilient {
    param(
        [Parameter(Mandatory)][string[]]$Arguments,
        [string]$UvCommand = 'uv',
        [string]$PayloadDirToScrub,
        [scriptblock]$BeforeAttempt
    )

    $policyPython = $null
    for ($i = 0; $i -lt $Arguments.Count - 1; $i++) {
        if ($Arguments[$i] -eq '--python' -and (Test-Path -LiteralPath $Arguments[$i + 1] -PathType Leaf)) {
            $policyPython = $Arguments[$i + 1]
            break
        }
    }
    if (-not $policyPython) {
        $candidate = Get-Command python3, python -CommandType Application -ErrorAction SilentlyContinue |
            Where-Object { $_.Source -notmatch '\\WindowsApps\\' } | Select-Object -First 1
        if ($candidate) { $policyPython = $candidate.Source }
    }
    if (-not $policyPython -or -not (Test-Path -LiteralPath $script:InstallerDependencyPolicy -PathType Leaf)) {
        return [pscustomobject]@{
            ExitCode = 1
            Output = 'dependency-seasoning: bootstrap Python or canonical policy helper is unavailable'
        }
    }
    $delays = @(3, 6, 10)
    if ($BeforeAttempt) { & $BeforeAttempt | Out-Null }
    $result = Invoke-NativeCapture {
        & $policyPython -I $script:InstallerDependencyPolicy --uv $UvCommand -- @Arguments
    }
    foreach ($delay in $delays) {
        if ($result.ExitCode -eq 0 -or -not (Test-IsSreModuleMismatch ($result.Output | Out-String))) {
            break
        }
        Write-Warn "uv build hit a transient SRE module mismatch (shared Python cache race, #6785) -- retrying in ${delay}s"
        Start-Sleep -Seconds $delay
        if ($BeforeAttempt) { & $BeforeAttempt | Out-Null }
        $result = Invoke-NativeCapture {
            & $policyPython -I $script:InstallerDependencyPolicy --uv $UvCommand -- @Arguments
        }
    }

    if ($result.ExitCode -eq 0 -and $PayloadDirToScrub) {
        Remove-Item -LiteralPath (Join-Path $PayloadDirToScrub 'build') `
            -Recurse -Force -ErrorAction SilentlyContinue
        Get-ChildItem -LiteralPath $PayloadDirToScrub -Filter '*.egg-info' -ErrorAction SilentlyContinue |
            ForEach-Object {
                Remove-Item -LiteralPath $_.FullName -Recurse -Force -ErrorAction SilentlyContinue
            }
    }

    return $result
}

function Invoke-UvVenvResilient {
    param(
        [Parameter(Mandatory)][string]$VenvDir,
        [Parameter(Mandatory)][string[]]$Arguments,
        [string]$UvCommand = 'uv'
    )

    $cfgPath = Join-Path $VenvDir 'pyvenv.cfg'
    $delays = @(3, 6, 10)
    $result = Invoke-NativeCapture { & $UvCommand venv $VenvDir @Arguments }
    foreach ($delay in $delays) {
        if ($result.ExitCode -eq 0 -and (Test-Path $cfgPath)) { break }
        $text = ($result.Output | Out-String)
        if ($result.ExitCode -eq 0) {
            Write-Warn "uv venv reported success but pyvenv.cfg is missing at $cfgPath (shared interpreter race, #6852) -- retrying in ${delay}s"
        } elseif (Test-IsSreModuleMismatch $text) {
            Write-Warn "uv venv hit a transient SRE module mismatch (shared Python cache race, #6785) -- retrying in ${delay}s"
        } elseif (Test-IsVenvCorruption $text) {
            Write-Warn "uv venv hit a transient pyvenv.cfg corruption (shared Python cache race, #6852) -- retrying in ${delay}s"
        } else {
            break
        }
        Start-Sleep -Seconds $delay
        $result = Invoke-NativeCapture { & $UvCommand venv $VenvDir @Arguments }
    }
    return $result
}

function Ensure-Uv {
    param(
        [Parameter(Mandatory)][string]$InstallRoot,
        [string]$ToolDirectory = 'tool',
        [bool]$AcquireIfMissing = $true,
        [string]$BootstrapVersion = '0.12.6'
    )

    $existing = Get-Command uv -CommandType Application -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($existing) {
        $result = Invoke-NativeCapture { & $existing.Source --version }
        if ($result.ExitCode -eq 0) {
            return $existing.Source
        }
    }

    $toolDir = Join-Path $InstallRoot $ToolDirectory
    $uvPath = Join-Path $toolDir 'uv.exe'
    if (Test-Path -LiteralPath $uvPath) {
        $result = Invoke-NativeCapture { & $uvPath --version }
        if ($result.ExitCode -eq 0) {
            $env:PATH = "$toolDir;$env:PATH"
            return $uvPath
        }
        Remove-Item -LiteralPath $uvPath -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath (Join-Path $toolDir 'uvx.exe') -Force -ErrorAction SilentlyContinue
    }

    if (-not $AcquireIfMissing) {
        return $null
    }

    $arch = if ($env:PROCESSOR_ARCHITEW6432) {
        $env:PROCESSOR_ARCHITEW6432
    } else {
        $env:PROCESSOR_ARCHITECTURE
    }
    if ($arch -eq 'AMD64') {
        $asset = 'uv-x86_64-pc-windows-msvc.zip'
        $expectedSha256 = 'df7cb9f243eae1621400d4fcf5b1b3d90f20e264ece91b64deb3b0078abca6ef'
    } elseif ($arch -eq 'ARM64') {
        $asset = 'uv-aarch64-pc-windows-msvc.zip'
        $expectedSha256 = '6dda514fbbe3152d980758e0f6347116060114d7d24932fc0ea5d8063f8b253a'
    } else {
        Write-Fail "uv bootstrap does not support Windows architecture: $arch"
        return $null
    }

    New-Item -ItemType Directory -Path $toolDir -Force | Out-Null
    $url = "https://github.com/astral-sh/uv/releases/download/$BootstrapVersion/$asset"
    $archive = [IO.Path]::GetTempFileName()
    $staging = Join-Path $InstallRoot ".uv-stage-$PID"
    $previousSecurityProtocol = [Net.ServicePointManager]::SecurityProtocol
    $client = $null
    try {
        if (Test-Path -LiteralPath $staging) {
            Remove-Item -LiteralPath $staging -Recurse -Force -ErrorAction SilentlyContinue
        }
        New-Item -ItemType Directory -Path $staging -Force | Out-Null
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        $client = New-Object Net.WebClient
        $client.Headers['User-Agent'] = 'installer-engine-bootstrap'
        $client.DownloadFile($url, $archive)
        $archiveStream = [IO.File]::OpenRead($archive)
        try {
            $sha256 = [Security.Cryptography.SHA256]::Create()
            try {
                $digest = $sha256.ComputeHash($archiveStream)
                $actualSha256 = ([BitConverter]::ToString($digest)).Replace('-', '').ToLowerInvariant()
            } finally {
                $sha256.Dispose()
            }
        } finally {
            $archiveStream.Dispose()
        }
        if ($actualSha256 -ne $expectedSha256) {
            throw "uv archive SHA-256 mismatch for $asset (expected $expectedSha256, got $actualSha256)"
        }
        Add-Type -AssemblyName System.IO.Compression.FileSystem
        [IO.Compression.ZipFile]::ExtractToDirectory($archive, $staging)
        $uvSource = Get-ChildItem -LiteralPath $staging -Recurse -File -Filter 'uv.exe' |
            Select-Object -First 1 -ExpandProperty FullName
        if (-not $uvSource) { throw 'uv.exe was absent from the release archive' }
        $uvxSource = Get-ChildItem -LiteralPath $staging -Recurse -File -Filter 'uvx.exe' |
            Select-Object -First 1 -ExpandProperty FullName
        if ($uvxSource) {
            Move-Item -LiteralPath $uvxSource -Destination (Join-Path $toolDir 'uvx.exe') -Force
        }
        Move-Item -LiteralPath $uvSource -Destination $uvPath -Force
    } catch {
        Remove-Item -LiteralPath $uvPath -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath (Join-Path $toolDir 'uvx.exe') -Force -ErrorAction SilentlyContinue
        Write-Fail "Failed to vendor uv from $url`: $_"
        return $null
    } finally {
        if ($client) { $client.Dispose() }
        [Net.ServicePointManager]::SecurityProtocol = $previousSecurityProtocol
        Remove-Item -LiteralPath $archive -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $staging -Recurse -Force -ErrorAction SilentlyContinue
    }

    $result = Invoke-NativeCapture { & $uvPath --version }
    if ($result.ExitCode -ne 0) {
        Remove-Item -LiteralPath $uvPath -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath (Join-Path $toolDir 'uvx.exe') -Force -ErrorAction SilentlyContinue
        Write-Fail "Vendored uv is not executable: $($result.Output)"
        return $null
    }

    $env:PATH = "$toolDir;$env:PATH"
    Write-Ok "Vendored uv into $toolDir"
    return $uvPath
}

function Get-SignedBasePython {
    param([version]$MinimumVersion = [version]'3.10')
    if ($env:OS -ne 'Windows_NT') { return $null }
    $cands = @()
    if (Get-Command py -ErrorAction SilentlyContinue) {
        foreach ($v in '3.13', '3.12', '3.11', '3.10') {
            if ([version]$v -lt $MinimumVersion) { continue }
            $result = Invoke-NativeCapture { & py "-$v" -c 'import sys;print(sys.executable)' }
            if ($result.ExitCode -eq 0 -and $result.Output) {
                $cands += $result.Output
            }
        }
    }
    foreach ($c in ($cands | Select-Object -Unique)) {
        if (Test-Path $c) {
            try {
                if ((Get-AuthenticodeSignature $c).Status -eq 'Valid') {
                    return $c
                }
            } catch {}
        }
    }
    return $null
}

function New-SignedVenv {
    param(
        [Parameter(Mandatory)][string]$VenvDir,
        [Parameter(Mandatory)][string]$VenvPython,
        [string]$PythonVersion = '3.10',
        [string]$UvCommand = 'uv',
        [bool]$RequireSignedBase = $false,
        [bool]$AllowExisting = $true,
        [version]$MinimumSignedBaseVersion = [version]'3.10'
    )

    $cfgPath = Join-Path $VenvDir 'pyvenv.cfg'
    if (Test-Path $VenvPython) {
        $sig = if ($env:OS -eq 'Windows_NT') {
            try { (Get-AuthenticodeSignature $VenvPython).Status } catch { 'Unknown' }
        } else {
            'Valid'
        }
        if ($sig -ne 'Valid' -and (Get-SignedBasePython -MinimumVersion $MinimumSignedBaseVersion)) {
            Write-Step 'Existing venv python is unsigned (Smart App Control-incompatible) -- rebuilding from signed Python'
            try { Remove-Item -Recurse -Force $VenvDir -ErrorAction Stop } catch {}
        } elseif (-not (Test-Path $cfgPath)) {
            Write-Warn "Existing venv python.exe present but pyvenv.cfg is missing at $cfgPath (shared interpreter race, #6852) -- rebuilding"
            try { Remove-Item -Recurse -Force $VenvDir -ErrorAction Stop } catch {}
        }
    }
    if ((Test-Path $VenvPython) -and (Test-Path $cfgPath)) {
        return $true
    }

    $signedBase = Get-SignedBasePython -MinimumVersion $MinimumSignedBaseVersion
    if ($signedBase) {
        & $signedBase -m venv --copies $VenvDir 2>&1 | Out-Null
        if ((Test-Path $VenvPython) -and (Test-Path $cfgPath)) {
            Write-Ok "Venv created from signed Python ($signedBase)"
            return $true
        }
        Write-Warn 'Signed-Python venv creation failed -- falling back to uv'
    } elseif ($RequireSignedBase) {
        Write-Warn 'No signed system Python found -- using uv (unsigned). On Smart App Control machines, install python.org Python 3.10+ and re-run.'
    }

    $arguments = @('--python', $PythonVersion)
    if ($AllowExisting) { $arguments += '--allow-existing' }
    $result = Invoke-UvVenvResilient -VenvDir $VenvDir -Arguments $arguments -UvCommand $UvCommand
    if ($result.ExitCode -ne 0 -or -not (Test-Path $cfgPath)) {
        $fallbackArgs = @()
        if ($AllowExisting) { $fallbackArgs += '--allow-existing' }
        $result = Invoke-UvVenvResilient -VenvDir $VenvDir -Arguments $fallbackArgs -UvCommand $UvCommand
    }
    return ((Test-Path $VenvPython) -and (Test-Path $cfgPath))
}

function Write-DeployManifest {
    param(
        [Parameter(Mandatory)][string]$Service,
        [Parameter(Mandatory)][string]$Plugin,
        [Parameter(Mandatory)][string]$InstallPath,
        [Parameter(Mandatory)][string]$PluginPath,
        [Parameter(Mandatory)][string]$VenvPath,
        [Parameter(Mandatory)][scriptblock]$GetSourceKind,
        [Parameter(Mandatory)][scriptblock]$GetGitInfo,
        [string]$SourcePathOverride = '',
        [string]$VersionOverride = '',
        [string]$PayloadHash = '',
        [hashtable]$AdditionalFields = @{}
    )

    $manifestPath = [IO.Path]::GetFullPath((Join-Path $InstallPath 'deploy-manifest.json'))
    $provenancePath = if ($SourcePathOverride) { $SourcePathOverride } else { $PluginPath }
    if ($SourcePathOverride) {
        $normalized = ($provenancePath -replace '\\', '/')
        $kind = if ($normalized -match '/\.copilot/installed-plugins/') { 'marketplace' } else { 'local' }
    } else {
        $kind = & $GetSourceKind $provenancePath
    }
    $ver = if ($VersionOverride) { $VersionOverride } else { '0.0.0' }
    if (-not $VersionOverride) {
        $pyproj = Join-Path $PluginPath 'pyproject.toml'
        if (Test-Path $pyproj) {
            $verLine = Select-String -Path $pyproj -Pattern '^\s*version\s*=' | Select-Object -First 1
            if ($verLine) { $ver = ($verLine.Line -replace '.*=\s*"([^"]+)".*','$1') }
        }
    }

    $commit = $null
    $branch = $null
    $dirty = $false
    if ($kind -eq 'local') {
        $gitInfo = & $GetGitInfo (Split-Path $provenancePath)
        $commit = $gitInfo.commit
        $branch = $gitInfo.branch
        $dirty = $gitInfo.dirty
    }

    $source = [ordered]@{
        kind    = $kind
        path    = ($provenancePath -replace '\\', '/')
        repo    = 'copilot-extensions'
        plugin  = $Plugin
        version = $ver
        commit  = $commit
        branch  = $branch
        dirty   = $dirty
    }
    if ($PayloadHash) {
        $source['content_hash'] = $PayloadHash
    }

    $deployedByHost = if ($env:COMPUTERNAME) {
        $env:COMPUTERNAME.ToLowerInvariant()
    } else {
        ([System.Net.Dns]::GetHostName()).ToLowerInvariant()
    }

    $manifest = [ordered]@{
        schema_version = 3
        service        = $Service
        deployed_at    = (Get-Date -Format 'o')
        deployed_by    = "$deployedByHost-windows"
        source         = $source
        venv           = ($VenvPath -replace '\\', '/')
        runtime        = 'python'
    }

    foreach ($key in $AdditionalFields.Keys) {
        $manifest[$key] = $AdditionalFields[$key]
    }

    $tmp = $manifestPath + '.tmp-' + [Guid]::NewGuid().ToString('N')
    try {
        $manifest | ConvertTo-Json -Depth 6 -ErrorAction Stop |
            Set-Content -LiteralPath $tmp -Encoding UTF8 -ErrorAction Stop
        if ([IO.File]::Exists($manifestPath)) {
            [IO.File]::Replace($tmp, $manifestPath, [NullString]::Value)
        } else {
            [IO.File]::Move($tmp, $manifestPath)
        }
    } catch {
        [Console]::Error.WriteLine("Deploy manifest publication failed: $($_.Exception.Message)")
        throw
    } finally {
        if ([IO.File]::Exists($tmp)) { [IO.File]::Delete($tmp) }
    }
    Write-Ok "Deploy manifest written (source: $kind)"
}

function Publish-LegacySnapshotSource {
    param(
        [Parameter(Mandatory)][string]$SnapshotDir,
        [Parameter(Mandatory)][string]$HelperDirectory,
        [Parameter(Mandatory)][string]$PayloadRoot,
        [Parameter(Mandatory)][string]$PluginId,
        [Parameter(Mandatory)][string]$RuntimeRoot,
        [Parameter(Mandatory)][string]$ProjectRoot,
        [string]$SnapshotId,
        [string]$PayloadVersion
    )
    if ($env:COPILOT_EXTENSIONS_CONTEXT) { return }
    if ([bool]$SnapshotId -ne [bool]$PayloadVersion) {
        throw 'Snapshot identity and payload version must be supplied together.'
    }
    $receiptVersion = if ($SnapshotId) { 2 } else { 1 }
    if ($receiptVersion -eq 2 -and
        ($SnapshotId -cnotmatch '^[A-Za-z0-9._+-]+$' -or $PayloadVersion -cnotmatch '^[A-Za-z0-9._+-]+$')) {
        throw 'Snapshot identity and payload version must be safe identifiers.'
    }
    $helper = Join-Path $HelperDirectory 'installation-context\installation-context.ps1'
    $resolved = @(& $helper -Action resolve -PayloadRoot $PayloadRoot `
        -PluginId $PluginId -ProjectRoot $ProjectRoot)
    if ($LASTEXITCODE -ne 0) {
        if (Test-Path -LiteralPath (Join-Path $RuntimeRoot 'runtime-deployment.json')) {
            throw 'Cannot preserve canonical snapshot source under an explicit deployment policy.'
        }
        Write-Warning 'Snapshot has no canonical source evidence; explicit deployment ownership will require repair.'
        return
    }
    $resolution = ($resolved -join "`n") | ConvertFrom-Json -ErrorAction Stop
    $receipt = Join-Path $SnapshotDir 'snapshot-provenance.json'
    if (Test-Path -LiteralPath $receipt) {
        $entry = Get-Item -LiteralPath $receipt -Force
        if ($entry.PSIsContainer -or ($entry.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw 'Snapshot source must be an ordinary file.'
        }
        $previous = Get-Content -LiteralPath $receipt -Raw | ConvertFrom-Json -ErrorAction Stop
        if ($previous.schema -cne 'copilot-extensions.legacy-snapshot-source' -or
            ($previous.version -isnot [int] -and $previous.version -isnot [long]) -or
            $previous.version -ne $receiptVersion -or
            ($receiptVersion -eq 2 -and
                ($previous.snapshotId -isnot [string] -or $previous.payloadVersion -isnot [string] -or
                    $previous.snapshotId -cne $SnapshotId -or $previous.payloadVersion -cne $PayloadVersion)) -or
            $previous.resolution.sourceFingerprint -cne $resolution.sourceFingerprint -or
            $previous.resolution.pluginId -cne $resolution.pluginId) {
            throw 'Existing snapshot source belongs to another owner or is invalid.'
        }
        return
    }
    $document = [ordered]@{
        schema = 'copilot-extensions.legacy-snapshot-source'
        version = $receiptVersion
        resolution = $resolution
    }
    if ($receiptVersion -eq 2) {
        $document.snapshotId = $SnapshotId
        $document.payloadVersion = $PayloadVersion
    }
    $document = $document | ConvertTo-Json -Depth 30
    $temporary = Join-Path $SnapshotDir ('.source-provenance-' + [guid]::NewGuid().ToString('N'))
    try {
        [IO.File]::WriteAllText($temporary, $document, [Text.UTF8Encoding]::new($false))
        [IO.File]::Move($temporary, $receipt)
    } finally {
        if ([IO.File]::Exists($temporary)) { [IO.File]::Delete($temporary) }
    }
}

function Write-SimpleBinstub {
    param(
        [Parameter(Mandatory)][string]$CommandName,
        [Parameter(Mandatory)][string]$ModuleName,
        [Parameter(Mandatory)][string]$RuntimeRoot,
        [Parameter(Mandatory)][string]$LocalBin,
        [Parameter(Mandatory)][string]$InstallBinDir,
        [Parameter(Mandatory)][string]$SnapshotInstallerPath,
        [Parameter(Mandatory)][string]$NoSelfProvisionEnv,
        [string]$ResolverPs1Source,
        [string]$ResolverShSource,
        [string]$SnapshotSourcePath,
        [string]$SnapshotId,
        [string]$PayloadVersion
    )

    function Publish-SimpleBinstubFile {
        param([string]$Destination, [scriptblock]$Write)
        $temporary = Join-Path (Split-Path -Parent $Destination) (
            '.' + (Split-Path -Leaf $Destination) + '.' + [Guid]::NewGuid().ToString('N'))
        try {
            & $Write $temporary
            if ([IO.File]::Exists($Destination)) {
                [IO.File]::Replace($temporary, $Destination, [NullString]::Value)
            } else {
                [IO.File]::Move($temporary, $Destination)
            }
        } catch {
            [Console]::Error.WriteLine("Binstub publication failed (${Destination}): $($_.Exception.Message)")
            throw
        } finally {
            if ([IO.File]::Exists($temporary)) { [IO.File]::Delete($temporary) }
        }
    }
    foreach ($source in @($ResolverPs1Source, $ResolverShSource)) {
        if ($source -and -not (Test-Path -LiteralPath $source -PathType Leaf)) {
            throw "Binstub resolver source is unavailable: $source"
        }
    }
    if ($SnapshotSourcePath) {
        if (-not $ResolverPs1Source) { throw 'Snapshot source capture requires the owning resolver source.' }
        $snapshotParent = [IO.Path]::GetFullPath((Split-Path -Parent $SnapshotSourcePath))
        $expectedParent = [IO.Path]::GetFullPath((Join-Path $RuntimeRoot 'snapshots'))
        if (-not $snapshotParent.Equals($expectedParent, [StringComparison]::OrdinalIgnoreCase) -or
            -not (Test-Path -LiteralPath $SnapshotSourcePath -PathType Container) -or
            ((Get-Item -LiteralPath $SnapshotSourcePath -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw 'Snapshot source capture requires an ordinary runtime snapshot candidate.'
        }
        $helperDirectory = Split-Path -Parent $ResolverPs1Source
        $sourcePayload = if ($env:COPILOT_PLUGIN_STAGED_FROM) {
            $env:COPILOT_PLUGIN_STAGED_FROM
        } else { Split-Path -Parent $helperDirectory }
        $sourceProject = if ($env:COPILOT_EXTENSIONS_INSTALL_PROJECT_ROOT) {
            $env:COPILOT_EXTENSIONS_INSTALL_PROJECT_ROOT
        } else { (Get-Location).ProviderPath }
        Publish-LegacySnapshotSource -SnapshotDir $SnapshotSourcePath -HelperDirectory $helperDirectory `
            -PayloadRoot $sourcePayload -PluginId $CommandName -RuntimeRoot $RuntimeRoot `
            -ProjectRoot $sourceProject -SnapshotId $SnapshotId -PayloadVersion $PayloadVersion
    }
    if (-not (Test-Path $LocalBin)) { New-Item -ItemType Directory -Path $LocalBin -Force -ErrorAction Stop | Out-Null }
    if (-not (Test-Path $InstallBinDir)) { New-Item -ItemType Directory -Path $InstallBinDir -Force -ErrorAction Stop | Out-Null }
    if ($ResolverPs1Source) {
        Publish-SimpleBinstubFile (Join-Path $InstallBinDir 'resolve-runtime.ps1') {
            param($temporary)
            Copy-Item -LiteralPath $ResolverPs1Source -Destination $temporary -Force -ErrorAction Stop
        }
    }
    if ($ResolverShSource) {
        Publish-SimpleBinstubFile (Join-Path $InstallBinDir 'resolve-runtime.sh') {
            param($temporary)
            Copy-Item -LiteralPath $ResolverShSource -Destination $temporary -Force -ErrorAction Stop
        }
    }

    $runtimeRootLiteral = $RuntimeRoot.Replace("'", "''")
    $snapshotInstallerLiteral = $SnapshotInstallerPath.Replace("'", "''")
    $moduleLiteral = $ModuleName.Replace("'", "''")
    $commandLiteral = $CommandName.Replace("'", "''")
    $coldDispatcher = ''
    if ($CommandName -ceq 'agent-vault') {
        $coldDispatcher = @'
$_gate = Join-Path $_snap 'scripts\runtime-gate.ps1'
if (-not (Test-Path -LiteralPath $_gate -PathType Leaf) -or
    -not (Select-String -LiteralPath $_gate -SimpleMatch '# Installation driver contract: vault-first-use-v1' -Quiet)) {
    [Console]::Error.WriteLine('[agent-vault] owning snapshot dispatcher is unavailable; repair the owning payload.')
    exit 126
}
$env:AGENT_VAULT_HOME = $_root
$env:AGENT_VAULT_PAYLOAD_ROOT = $_snap
& $_gate @args
exit $LASTEXITCODE
'@
    }

    $ps1Path = Join-Path $LocalBin "$CommandName.ps1"
    $ps1Content = @"
`$env:PYTHONUTF8 = '1'
`$_root = '$runtimeRootLiteral'
`$_resolver = Join-Path `$_root 'bin\resolve-runtime.ps1'
if (-not (Test-Path -LiteralPath `$_resolver -PathType Leaf) -or
    -not (Select-String -LiteralPath `$_resolver -SimpleMatch '# Trace contract: normal/read-only/ready-v1' -Quiet)) {
    [Console]::Error.WriteLine('[$commandLiteral] read-only runtime resolver is unavailable. Re-enable/update the plugin, then retry.')
    exit 126
}
function _Resolve-Py([string]`$TraceMode = 'ready') {
    `$AgentRtPy = `$null
    if (Test-Path -LiteralPath `$_resolver) { `$env:AGENT_RT_ROOT = `$_root; . `$_resolver -TraceMode `$TraceMode }
    return `$AgentRtPy
}
`$_py = _Resolve-Py
if (`$_py) { & `$_py -m $moduleLiteral @args; exit `$LASTEXITCODE }
if (`$env:$NoSelfProvisionEnv) { [Console]::Error.WriteLine('[$commandLiteral] runtime not provisioned ($NoSelfProvisionEnv set).'); exit 1 }
`$_snap = ''
try { `$_snap = ([IO.File]::ReadAllText((Join-Path `$_root 'payload-dir'))).Trim() } catch {}
`$_inst = if (`$_snap) { Join-Path `$_snap '$snapshotInstallerLiteral' } else { '' }
if (-not (`$_inst -and (Test-Path -LiteralPath `$_inst -PathType Leaf))) {
    [Console]::Error.WriteLine("[$commandLiteral] cannot self-provision: owning snapshot installer unavailable: `$_inst")
    exit 127
}
`$_probe = Join-Path `$_snap 'scripts\installation-context\plugin-publication-probe.ps1'
if (-not (Test-Path -LiteralPath `$_probe -PathType Leaf)) {
    [Console]::Error.WriteLine('[$commandLiteral] Runtime publication refused: snapshot admission helper is unavailable. Re-enable/update the plugin, then retry.')
    exit 126
}
`$global:LASTEXITCODE = 0
& `$_probe -PayloadRoot `$_snap -PluginId '$commandLiteral' -LegacyRoot `$_root
if (`$LASTEXITCODE -ne 0) { exit `$LASTEXITCODE }
$coldDispatcher
`$_py = _Resolve-Py normal
if (`$_py) { & `$_py -m $moduleLiteral @args; exit `$LASTEXITCODE }
if (-not (Test-Path -LiteralPath `$_root)) { New-Item -ItemType Directory -Path `$_root -Force | Out-Null }
`$_lockPath = Join-Path `$_root '.provision.lock'
`$_lock = `$null
while (-not `$_lock) {
    try {
        `$_lock = [IO.File]::Open(`$_lockPath, [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
    } catch {
        Start-Sleep -Milliseconds 200
    }
}
`$_provisionRc = 0
try {
    `$_py = _Resolve-Py
    if (-not `$_py) {
        `$_snap = ''
        try { `$_snap = ([IO.File]::ReadAllText((Join-Path `$_root 'payload-dir'))).Trim() } catch {}
        `$_inst = if (`$_snap) { Join-Path `$_snap '$snapshotInstallerLiteral' } else { '' }
        if (-not (`$_inst -and (Test-Path -LiteralPath `$_inst))) {
            [Console]::Error.WriteLine("[$commandLiteral] cannot self-provision: owning snapshot installer unavailable: `$_inst")
            `$_provisionRc = 127
        } else {
            [Console]::Error.WriteLine('[$commandLiteral] runtime not provisioned -- provisioning on first use (acquires uv + builds a venv; ~30-120s). Do not kill; extend your timeout.')
            [Console]::Error.WriteLine('::agent-provisioning:: plugin=$commandLiteral eta_seconds=120 reason=first-use')
                        `$_hostExe = `$null
                        try {
                            `$_whereExe = Join-Path `$env:SystemRoot 'System32\where.exe'
                            if (Test-Path -LiteralPath `$_whereExe) {
                                `$_found = & `$_whereExe pwsh 2>`$null | Select-Object -First 1
                                if (`$_found -and (Test-Path -LiteralPath `$_found)) { `$_hostExe = `$_found }
                            }
                        } catch {}
                        if (-not `$_hostExe) {
                            `$_hostExe = Join-Path `$env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
                        }
                        & `$_hostExe -NoProfile -ExecutionPolicy Bypass -File `$_inst provision -InstallDir `$_root 2>&1 | ForEach-Object { [Console]::Error.WriteLine(`$_) }
                        `$_provisionRc = `$LASTEXITCODE
                        if (`$_provisionRc -eq 0) { `$_py = _Resolve-Py }
                    }
    }
} finally {
    if (`$_lock) { `$_lock.Dispose() }
}
if (`$_provisionRc -ne 0) { exit `$_provisionRc }
if (`$_py) { & `$_py -m $moduleLiteral @args; exit `$LASTEXITCODE }
[Console]::Error.WriteLine('[$commandLiteral] provisioning completed without a resolvable runtime.')
exit 1
"@
    Publish-SimpleBinstubFile $ps1Path {
        param($temporary)
        [System.IO.File]::WriteAllText($temporary, $ps1Content, (New-Object System.Text.UTF8Encoding $false))
    }

    $cmdPath = Join-Path $LocalBin "$CommandName.cmd"
    $cmdContent = @"
@echo off
setlocal
set "PYTHONUTF8=1"
set "_PS1=%USERPROFILE%\.local\bin\$CommandName.ps1"
if not exist "%_PS1%" (echo [$CommandName] binstub not found: %_PS1%>&2 & exit /b 127)
set "_PSHOST="
for /f "delims=" %%I in ('"%SystemRoot%\System32\where.exe" pwsh 2^>nul') do if not defined _PSHOST set "_PSHOST=%%I"
if not defined _PSHOST set "_PSHOST=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
"%_PSHOST%" -NoProfile -ExecutionPolicy Bypass -File "%_PS1%" %*
exit /b %ERRORLEVEL%
"@
    Publish-SimpleBinstubFile $cmdPath {
        param($temporary)
        [System.IO.File]::WriteAllText($temporary, $cmdContent, (New-Object System.Text.UTF8Encoding $false))
    }
    Write-Ok "Binstub: $ps1Path (+ .cmd fallback, self-provisioning)"
}
