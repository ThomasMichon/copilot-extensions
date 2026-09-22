<#
.SYNOPSIS
    Canonical vendored installer-engine helpers for runtime plugins.

.DESCRIPTION
    This file is the canonical source of truth for the shared installer-engine
    helpers that runtime plugins vendor into their own `scripts/` directories.
    Plugins do NOT import it across plugin boundaries at install or runtime;
    `tools/sync-installer-engine.py` copies it byte-for-byte into each adopter.
#>

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
        [string]$PayloadDirToScrub
    )

    $delays = @(3, 6, 10)
    $result = Invoke-NativeCapture { & $UvCommand pip install @Arguments }
    foreach ($delay in $delays) {
        if ($result.ExitCode -eq 0 -or -not (Test-IsSreModuleMismatch ($result.Output | Out-String))) {
            break
        }
        Write-Warn "uv build hit a transient SRE module mismatch (shared Python cache race, #6785) -- retrying in ${delay}s"
        Start-Sleep -Seconds $delay
        $result = Invoke-NativeCapture { & $UvCommand pip install @Arguments }
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

    $existing = Get-Command uv -CommandType Application -ErrorAction SilentlyContinue
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
    if ($env:OS -ne 'Windows_NT') { return $null }
    $cands = @()
    if (Get-Command py -ErrorAction SilentlyContinue) {
        foreach ($v in '3.13', '3.12', '3.11', '3.10') {
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
        [bool]$AllowExisting = $true
    )

    $cfgPath = Join-Path $VenvDir 'pyvenv.cfg'
    if (Test-Path $VenvPython) {
        $sig = if ($env:OS -eq 'Windows_NT') {
            try { (Get-AuthenticodeSignature $VenvPython).Status } catch { 'Unknown' }
        } else {
            'Valid'
        }
        if ($sig -ne 'Valid' -and (Get-SignedBasePython)) {
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

    $signedBase = Get-SignedBasePython
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
        [string]$PayloadHash = '',
        [hashtable]$AdditionalFields = @{}
    )

    $manifestPath = Join-Path $InstallPath 'deploy-manifest.json'
    $kind = & $GetSourceKind $PluginPath
    $ver = '0.0.0'
    $pyproj = Join-Path $PluginPath 'pyproject.toml'
    if (Test-Path $pyproj) {
        $verLine = Select-String -Path $pyproj -Pattern '^\s*version\s*=' | Select-Object -First 1
        if ($verLine) { $ver = ($verLine.Line -replace '.*=\s*"([^"]+)".*','$1') }
    }

    $commit = $null
    $branch = $null
    $dirty = $false
    if ($kind -eq 'local') {
        $gitInfo = & $GetGitInfo (Split-Path $PluginPath)
        $commit = $gitInfo.commit
        $branch = $gitInfo.branch
        $dirty = $gitInfo.dirty
    }

    $source = [ordered]@{
        kind    = $kind
        path    = ($PluginPath -replace '\\', '/')
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

    $manifest = [ordered]@{
        schema_version = 3
        service        = $Service
        deployed_at    = (Get-Date -Format 'o')
        deployed_by    = "$($env:COMPUTERNAME.ToLower())-windows"
        source         = $source
        venv           = ($VenvPath -replace '\\', '/')
        runtime        = 'python'
    }

    foreach ($key in $AdditionalFields.Keys) {
        $manifest[$key] = $AdditionalFields[$key]
    }

    $tmp = "$manifestPath.tmp"
    $manifest | ConvertTo-Json -Depth 6 | Set-Content -Path $tmp -Encoding UTF8
    Move-Item -Force -Path $tmp -Destination $manifestPath
    Write-Ok "Deploy manifest written (source: $kind)"
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
        [string]$ResolverShSource
    )

    if (-not (Test-Path $LocalBin)) { New-Item -ItemType Directory -Path $LocalBin -Force | Out-Null }
    if (-not (Test-Path $InstallBinDir)) { New-Item -ItemType Directory -Path $InstallBinDir -Force | Out-Null }
    if ($ResolverPs1Source -and (Test-Path $ResolverPs1Source)) {
        Copy-Item -LiteralPath $ResolverPs1Source -Destination (Join-Path $InstallBinDir 'resolve-runtime.ps1') -Force
    }
    if ($ResolverShSource -and (Test-Path $ResolverShSource)) {
        Copy-Item -LiteralPath $ResolverShSource -Destination (Join-Path $InstallBinDir 'resolve-runtime.sh') -Force
    }

    $runtimeRootLiteral = $RuntimeRoot.Replace("'", "''")
    $snapshotInstallerLiteral = $SnapshotInstallerPath.Replace("'", "''")
    $moduleLiteral = $ModuleName.Replace("'", "''")
    $commandLiteral = $CommandName.Replace("'", "''")

    $ps1Path = Join-Path $LocalBin "$CommandName.ps1"
    $ps1Content = @"
`$env:PYTHONUTF8 = '1'
`$_root = '$runtimeRootLiteral'
`$_resolver = Join-Path `$_root 'bin\resolve-runtime.ps1'
function _Resolve-Py {
    `$AgentRtPy = `$null
    if (Test-Path -LiteralPath `$_resolver) { `$env:AGENT_RT_ROOT = `$_root; . `$_resolver }
    return `$AgentRtPy
}
`$_py = _Resolve-Py
if (`$_py) { & `$_py -m $moduleLiteral @args; exit `$LASTEXITCODE }
if (`$env:$NoSelfProvisionEnv) { [Console]::Error.WriteLine('[$commandLiteral] runtime not provisioned ($NoSelfProvisionEnv set).'); exit 1 }
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
    [System.IO.File]::WriteAllText($ps1Path, $ps1Content, (New-Object System.Text.UTF8Encoding $false))

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
    [System.IO.File]::WriteAllText($cmdPath, $cmdContent, (New-Object System.Text.UTF8Encoding $false))
    Write-Ok "Binstub: $ps1Path (+ .cmd fallback, self-provisioning)"
}
