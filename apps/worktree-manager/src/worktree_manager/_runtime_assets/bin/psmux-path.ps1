function ConvertTo-AwNormalizedPath {
    param([AllowEmptyString()][string]$Path)
    if ([string]::IsNullOrWhiteSpace($Path)) { return '' }
    $expanded = [Environment]::ExpandEnvironmentVariables($Path.Trim())
    try { $expanded = [IO.Path]::GetFullPath($expanded) } catch {}
    return $expanded.TrimEnd('\', '/').ToLowerInvariant()
}

function Test-AwPsmuxPackagePath {
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][string]$PackageRoot
    )
    $pathN = ConvertTo-AwNormalizedPath $Path
    $rootN = ConvertTo-AwNormalizedPath $PackageRoot
    if (-not $pathN.StartsWith($rootN, [StringComparison]::OrdinalIgnoreCase)) {
        return $false
    }
    $relative = $pathN.Substring($rootN.Length)
    if (-not ($relative.StartsWith('\') -or $relative.StartsWith('/'))) {
        return $false
    }
    $relative = $relative.TrimStart('\', '/')
    $top = ($relative -split '[\\/]')[0]
    return $top.StartsWith('marlocarlo.psmux_', [StringComparison]::OrdinalIgnoreCase)
}

function Get-AwPsmuxBinaryVersion {
    param(
        [Parameter(Mandatory)][string]$Path,
        [scriptblock]$VersionProbe
    )
    try {
        $output = if ($VersionProbe) {
            & $VersionProbe $Path
        } else {
            $metadata = [Diagnostics.FileVersionInfo]::GetVersionInfo($Path)
            if ($metadata.ProductName -eq 'psmux' -and
                $metadata.FileVersion -match '^[0-9]+(?:\.[0-9]+){1,3}$') {
                $metadata.FileVersion
            } else {
                $state = Get-AwPsmuxSessionState -Path $Path
                if (-not $state.Known -or $state.Sessions.Count -gt 0) { return $null }
                & $Path --help 2>&1 | Select-Object -First 1
            }
        }
        $text = $output | Out-String
        if ($text -match '(?im)\bpsmux\s+v?([0-9]+(?:\.[0-9]+)+)\b' -or
            $text -match '^\s*([0-9]+(?:\.[0-9]+)+)\s*$') {
            $version = [version]$Matches[1]
            if ($version.Revision -eq 0) { return $version.ToString(3) }
            return $version.ToString()
        }
    } catch {}
    return $null
}

function Test-AwPsmuxVersionCompatible {
    param(
        [AllowNull()][AllowEmptyString()][string]$Version,
        [string]$MinimumVersion = '3.4.0',
        [string[]]$BlockedVersions = @('3.3.6')
    )
    if ([string]::IsNullOrWhiteSpace($Version)) { return $false }
    try {
        $parsed = [version]$Version
        $minimum = [version]$MinimumVersion
    } catch {
        return $false
    }
    if ($parsed -lt $minimum) { return $false }
    foreach ($blocked in $BlockedVersions) {
        try {
            $blockedParsed = [version]$blocked
        } catch {
            return $false
        }
        $blockedMatch = if ($blockedParsed.Revision -lt 0) {
            $parsed.Major -eq $blockedParsed.Major -and
                $parsed.Minor -eq $blockedParsed.Minor -and
                $parsed.Build -eq $blockedParsed.Build
        } else {
            $parsed -eq $blockedParsed
        }
        if ($blockedMatch) { return $false }
    }
    return $true
}

function Get-AwPsmuxSessionState {
    param(
        [Parameter(Mandatory)][string]$Path,
        [scriptblock]$SessionProbe
    )
    try {
        if (-not $SessionProbe) {
            # Legacy CLI reads can remove live registrations. Only native absence
            # permits replacement; any provider process keeps its sessions held.
            $names = @('psmux', 'pmux', 'tmux', [IO.Path]::GetFileNameWithoutExtension($Path)) |
                Sort-Object -Unique
            foreach ($name in $names) {
                $processes = [Diagnostics.Process]::GetProcessesByName($name)
                try {
                    if ($processes.Count -gt 0) {
                        return [pscustomobject]@{ Known = $false; Sessions = @() }
                    }
                } finally {
                    foreach ($process in $processes) { $process.Dispose() }
                }
            }
            return [pscustomobject]@{ Known = $true; Sessions = @() }
        }
        $probe = & $SessionProbe $Path
        $returnCode = [int]$probe.ReturnCode
        $output = @($probe.Output)
        if ($returnCode -ne 0) {
            return [pscustomobject]@{ Known = $false; Sessions = @() }
        }
        $sessions = @($output | Where-Object { "$_".Trim() })
        return [pscustomobject]@{ Known = $true; Sessions = $sessions }
    } catch {
        return [pscustomobject]@{ Known = $false; Sessions = @() }
    }
}

function Get-AwPsmuxSideBySideRoot {
    # Agent-worktrees-owned home for versioned PSMux copies. Each version lives
    # in its own <root>\<version> directory, so a newer client can be installed
    # while an older provider still serves live sessions.
    $userHome = if ($env:USERPROFILE) {
        $env:USERPROFILE
    } else {
        [Environment]::GetFolderPath('UserProfile')
    }
    return Join-Path $userHome '.agent-worktrees\psmux'
}

function Test-AwPsmuxSideBySidePath {
    param(
        [Parameter(Mandatory)][string]$Path,
        [string[]]$SideBySideRoot = @()
    )
    $pathN = ConvertTo-AwNormalizedPath $Path
    foreach ($root in @($SideBySideRoot)) {
        if ([string]::IsNullOrWhiteSpace($root)) { continue }
        $rootN = ConvertTo-AwNormalizedPath $root
        if (-not $pathN.StartsWith($rootN, [StringComparison]::OrdinalIgnoreCase)) {
            continue
        }
        $relative = $pathN.Substring($rootN.Length)
        if (-not ($relative.StartsWith('\') -or $relative.StartsWith('/'))) { continue }
        $relative = $relative.TrimStart('\', '/')
        if ($relative -and $relative -notmatch '[\\/]') { return $true }
    }
    return $false
}

function Find-AwCompatiblePsmuxPackageBinary {
    param(
        [Parameter(Mandatory)][string]$PackageRoot,
        [string]$MinimumVersion = '3.4.0',
        [string[]]$BlockedVersions = @('3.3.6'),
        [scriptblock]$VersionProbe,
        [string[]]$SideBySideRoot = @()
    )
    $packageBinaries = if (Test-Path -LiteralPath $PackageRoot) {
        Get-ChildItem -LiteralPath $PackageRoot -Directory -Filter 'marlocarlo.psmux_*' `
            -ErrorAction SilentlyContinue |
            ForEach-Object {
                Get-ChildItem -LiteralPath $_.FullName -Recurse -File -Filter 'psmux.exe' `
                    -ErrorAction SilentlyContinue
            }
    }
    $sideBySideBinaries = foreach ($root in @($SideBySideRoot)) {
        if ([string]::IsNullOrWhiteSpace($root) -or
            -not (Test-Path -LiteralPath $root -PathType Container)) { continue }
        Get-ChildItem -LiteralPath $root -Directory -ErrorAction SilentlyContinue |
            Where-Object { -not $_.Name.StartsWith('.') -and $_.Name -notlike '*.partial-*' } |
            ForEach-Object {
                Get-Item -LiteralPath (Join-Path $_.FullName 'psmux.exe') -ErrorAction SilentlyContinue
            }
    }
    $compatible = @(
        @($packageBinaries) + @($sideBySideBinaries) |
            Where-Object { $_ } |
            ForEach-Object {
                $version = Get-AwPsmuxBinaryVersion `
                    -Path $_.FullName -VersionProbe $VersionProbe
                try { $parsed = [version]$version } catch { $parsed = $null }
                if ($parsed -and (Test-AwPsmuxVersionCompatible `
                    -Version $version -MinimumVersion $MinimumVersion `
                    -BlockedVersions $BlockedVersions)) {
                    [pscustomobject]@{
                        Path = $_.FullName
                        Directory = $_.DirectoryName
                        Version = $version
                        ParsedVersion = $parsed
                    }
                }
            } |
            Sort-Object ParsedVersion, Path -Descending
    )
    return $compatible | Select-Object -First 1
}

function Repair-AwPsmuxPath {
    param(
        [Parameter(Mandatory)][string]$SelectedDirectory,
        [AllowEmptyString()][string]$UserPath,
        [AllowEmptyString()][string]$ProcessPath,
        [Parameter(Mandatory)][string]$PackageRoot,
        [string[]]$SideBySideRoot = @()
    )
    $selectedN = ConvertTo-AwNormalizedPath $SelectedDirectory

    function Build-AwPsmuxPath {
        param([AllowEmptyString()][string]$Value)
        $kept = @()
        foreach ($part in @($Value -split ';')) {
            if ([string]::IsNullOrWhiteSpace($part)) { continue }
            $normalized = ConvertTo-AwNormalizedPath $part
            if ($normalized -eq $selectedN) { continue }
            if (Test-AwPsmuxPackagePath -Path $part -PackageRoot $PackageRoot) { continue }
            if (Test-AwPsmuxSideBySidePath -Path $part -SideBySideRoot $SideBySideRoot) { continue }
            $kept += $part
        }
        return (@($SelectedDirectory) + $kept) -join ';'
    }

    $newUserPath = Build-AwPsmuxPath $UserPath
    $newProcessPath = Build-AwPsmuxPath $ProcessPath
    return [pscustomobject]@{
        UserPath = $newUserPath
        ProcessPath = $newProcessPath
        UserChanged = -not [string]::Equals(
            $newUserPath, $UserPath, [StringComparison]::OrdinalIgnoreCase
        )
        ProcessChanged = -not [string]::Equals(
            $newProcessPath, $ProcessPath, [StringComparison]::OrdinalIgnoreCase
        )
    }
}

function ConvertTo-AwPsmuxVersionText {
    param([AllowNull()][AllowEmptyString()][string]$Version)
    try {
        $parsed = [version]$Version
        if ($parsed.Revision -le 0) { return $parsed.ToString(3) }
        return $parsed.ToString()
    } catch {
        return $null
    }
}

function Get-AwPsmuxManifestSha256 {
    param([Parameter(Mandatory)][string]$ManifestPath)
    $text = Get-Content -LiteralPath $ManifestPath -Raw -ErrorAction SilentlyContinue
    if ($text -match '(?im)^\s*InstallerSha256:\s*([0-9a-f]{64})\s*$') {
        return $Matches[1].ToUpperInvariant()
    }
    return $null
}

function Install-AwPsmuxSideBySide {
    <# Install one PSMux version into <Root>\<Version> without touching any
       running provider, its sessions or the WinGet package registration.
       The archive comes from the WinGet source (`winget download`) and must
       match the manifest SHA-256; the extracted psmux.exe must report the
       requested version. A valid existing copy is reused. An empty, damaged or
       wrong-version <Root>\<Version> directory is left unchanged, and the
       verified copy goes into a new <Root>\<Version>-r<id> directory that
       discovery selects by its file version. #>
    param(
        [Parameter(Mandatory)][string]$Version,
        [Parameter(Mandatory)][string]$Root,
        [scriptblock]$Downloader,
        [scriptblock]$VersionProbe
    )
    $expected = ConvertTo-AwPsmuxVersionText $Version
    $target = Join-Path $Root $Version

    function New-AwPsmuxSideBySideResult {
        param([string]$Status, [string]$Message, [string]$FoundVersion, [string]$Directory = $target)
        [pscustomobject]@{
            Status = $Status
            Path = Join-Path $Directory 'psmux.exe'
            Directory = $Directory
            Version = $FoundVersion
            Message = $Message
        }
    }

    function Get-AwExistingSideBySide {
        # The canonical directory first, then replacement generations.
        $candidates = @($target) + @(Get-ChildItem -LiteralPath $Root -Directory `
                -Filter "$Version-r*" -ErrorAction SilentlyContinue |
            Sort-Object Name | ForEach-Object { $_.FullName })
        foreach ($directory in $candidates) {
            $exe = Join-Path $directory 'psmux.exe'
            if (-not (Test-Path -LiteralPath $exe -PathType Leaf)) { continue }
            $found = ConvertTo-AwPsmuxVersionText (Get-AwPsmuxBinaryVersion `
                -Path $exe -VersionProbe $VersionProbe)
            if ($found -and $found -eq $expected) {
                return New-AwPsmuxSideBySideResult 'present' `
                    "psmux $found is already installed side by side at $directory" $found $directory
            }
        }
        return $null
    }

    if (-not $expected) {
        return New-AwPsmuxSideBySideResult 'failed' "invalid psmux version '$Version'" $null
    }
    $existing = Get-AwExistingSideBySide
    if ($existing) { return $existing }

    $id = [guid]::NewGuid().ToString('N')
    $staging = Join-Path $Root ".staging-$id"
    $partial = Join-Path $Root "$Version.partial-$id"
    $destination = $target
    $replaced = Test-Path -LiteralPath $target
    if ($replaced) {
        $destination = Join-Path $Root "$Version-r$($id.Substring(0, 8))"
    }
    try {
        $download = Join-Path $staging 'download'
        $expanded = Join-Path $staging 'expanded'
        New-Item -ItemType Directory -Force -Path $download, $expanded | Out-Null
        if ($Downloader) {
            & $Downloader $Version $download
        } else {
            & winget download --id marlocarlo.psmux --version $Version --exact `
                --download-directory $download --accept-source-agreements `
                --accept-package-agreements 2>&1 | Out-Null
            if ($LASTEXITCODE -ne 0) {
                return New-AwPsmuxSideBySideResult 'failed' `
                    "winget download of psmux $Version exited with code $LASTEXITCODE" $null
            }
        }
        $archives = @(Get-ChildItem -LiteralPath $download -Recurse -File -Filter '*.zip' `
            -ErrorAction SilentlyContinue)
        if ($archives.Count -ne 1) {
            return New-AwPsmuxSideBySideResult 'failed' `
                "expected one psmux $Version archive from the download, found $($archives.Count)" $null
        }
        $hashes = @(Get-ChildItem -LiteralPath $download -Recurse -File -Filter '*.yaml' `
                -ErrorAction SilentlyContinue |
            ForEach-Object { Get-AwPsmuxManifestSha256 -ManifestPath $_.FullName } |
            Where-Object { $_ } | Sort-Object -Unique)
        if ($hashes.Count -ne 1) {
            return New-AwPsmuxSideBySideResult 'failed' `
                "the psmux $Version download has no single manifest InstallerSha256" $null
        }
        $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $archives[0].FullName).Hash
        if ($actual -ne $hashes[0]) {
            return New-AwPsmuxSideBySideResult 'failed' `
                "the psmux $Version archive SHA-256 does not match its manifest" $null
        }
        Expand-Archive -LiteralPath $archives[0].FullName -DestinationPath $expanded -Force
        $binaries = @(Get-ChildItem -LiteralPath $expanded -Recurse -File -Filter 'psmux.exe' `
            -ErrorAction SilentlyContinue)
        if ($binaries.Count -ne 1) {
            return New-AwPsmuxSideBySideResult 'failed' `
                "expected one psmux.exe in the psmux $Version archive, found $($binaries.Count)" $null
        }
        $found = ConvertTo-AwPsmuxVersionText (Get-AwPsmuxBinaryVersion `
            -Path $binaries[0].FullName -VersionProbe $VersionProbe)
        if ($found -ne $expected) {
            $shown = if ($found) { $found } else { 'unknown' }
            return New-AwPsmuxSideBySideResult 'failed' `
                "the extracted psmux reports version $shown, expected $expected" $found
        }
        New-Item -ItemType Directory -Force -Path $partial | Out-Null
        Get-ChildItem -LiteralPath $binaries[0].DirectoryName -Force |
            Copy-Item -Destination $partial -Recurse -Force
        try {
            # Directory.Move refuses an existing destination instead of nesting.
            [IO.Directory]::Move($partial, $destination)
        } catch {
            $existing = Get-AwExistingSideBySide
            if ($existing) { return $existing }
            throw
        }
        $note = if ($replaced) { "; the unexpected $target directory was left unchanged" } else { '' }
        return New-AwPsmuxSideBySideResult 'installed' `
            "psmux $found installed side by side at $destination$note" $found $destination
    } catch {
        return New-AwPsmuxSideBySideResult 'failed' `
            "side-by-side install of psmux $Version failed: $($_.Exception.Message)" $null
    } finally {
        foreach ($leftover in @($staging, $partial)) {
            if (Test-Path -LiteralPath $leftover) {
                Remove-Item -LiteralPath $leftover -Recurse -Force -ErrorAction SilentlyContinue
            }
        }
    }
}
