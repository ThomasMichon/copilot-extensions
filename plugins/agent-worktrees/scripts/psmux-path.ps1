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
            & $Path --help 2>&1 | Select-Object -First 1
        }
        $text = $output | Out-String
        if ($text -match '(?im)\bpsmux\s+v?([0-9]+(?:\.[0-9]+)+)\b') {
            return $Matches[1]
        }
        if ($text -match '^\s*([0-9]+(?:\.[0-9]+)+)\s*$') {
            return $Matches[1]
        }
    } catch {}
    return $null
}

function Get-AwWingetPinVersion {
    param(
        [AllowEmptyString()][string]$Output,
        [Parameter(Mandatory)][string]$PackageId
    )
    foreach ($line in @($Output -split "`r?`n")) {
        $tokens = @($line -split '\s+' | Where-Object { $_ })
        $idIndex = -1
        for ($i = 0; $i -lt $tokens.Count; $i++) {
            if ($tokens[$i].Equals($PackageId, [StringComparison]::OrdinalIgnoreCase)) {
                $idIndex = $i
                break
            }
        }
        if ($idIndex -ge 0 -and $idIndex + 1 -lt $tokens.Count) {
            return $tokens[-1]
        }
    }
    return $null
}

function Get-AwPsmuxSessionState {
    param(
        [Parameter(Mandatory)][string]$Path,
        [scriptblock]$SessionProbe
    )
    try {
        if ($SessionProbe) {
            $probe = & $SessionProbe $Path
            $returnCode = [int]$probe.ReturnCode
            $output = @($probe.Output)
        } else {
            $output = @(& $Path ls 2>$null)
            $returnCode = $LASTEXITCODE
        }
        if ($returnCode -ne 0) {
            return [pscustomobject]@{ Known = $false; Sessions = @() }
        }
        $sessions = @($output | Where-Object { "$_".Trim() })
        return [pscustomobject]@{ Known = $true; Sessions = $sessions }
    } catch {
        return [pscustomobject]@{ Known = $false; Sessions = @() }
    }
}

function Find-AwPsmuxPackageBinary {
    param(
        [Parameter(Mandatory)][string]$PackageRoot,
        [Parameter(Mandatory)][string]$DesiredVersion,
        [scriptblock]$VersionProbe
    )
    if (-not (Test-Path -LiteralPath $PackageRoot)) { return $null }
    $executables = @(
        Get-ChildItem -LiteralPath $PackageRoot -Directory -Filter 'marlocarlo.psmux_*' `
            -ErrorAction SilentlyContinue |
            Sort-Object FullName |
            ForEach-Object {
                Get-ChildItem -LiteralPath $_.FullName -Recurse -File -Filter 'psmux.exe' `
                    -ErrorAction SilentlyContinue
            } |
            Sort-Object FullName
    )
    foreach ($exe in $executables) {
        $version = Get-AwPsmuxBinaryVersion -Path $exe.FullName -VersionProbe $VersionProbe
        if ($version -eq $DesiredVersion) {
            return [pscustomobject]@{
                Path = $exe.FullName
                Directory = $exe.DirectoryName
                Version = $version
            }
        }
    }
    return $null
}

function Repair-AwPsmuxPath {
    param(
        [Parameter(Mandatory)][string]$SelectedDirectory,
        [AllowEmptyString()][string]$UserPath,
        [AllowEmptyString()][string]$ProcessPath,
        [Parameter(Mandatory)][string]$PackageRoot
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
