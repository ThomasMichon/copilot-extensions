param(
    [Parameter(Mandatory = $true, Position = 0)][string]$Plugin,
    [Parameter(Mandatory = $true, Position = 1)][string]$PluginRoot,
    [Parameter(Mandatory = $true, Position = 2)][string]$Target
)

$ErrorActionPreference = 'Stop'

function Assert-NoControlCharacters([string]$Value) {
    foreach ($character in $Value.ToCharArray()) {
        if ([char]::IsControl($character)) {
            throw 'plugin, plugin root, and target must not contain control characters'
        }
    }
}

function Get-CanonicalPath([string]$Path, [bool]$Directory) {
    if ($Directory -and -not (Test-Path -LiteralPath $Path -PathType Container)) {
        throw 'plugin root must be an existing directory'
    }
    if (-not $Directory -and -not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw 'target must be an existing regular file'
    }
    $item = Get-Item -LiteralPath $Path -Force
    if ($Directory -and -not $item.PSIsContainer) {
        throw 'plugin root must be an existing directory'
    }
    if (
        -not $Directory -and (
            $item.PSIsContainer -or
            $item.LinkType -or
            ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint)
        )
    ) {
        throw 'target must be an existing regular file'
    }
    $resolved = Resolve-Path -LiteralPath $Path
    return [System.IO.Path]::GetFullPath($resolved.ProviderPath)
}

try {
    Assert-NoControlCharacters $Plugin
    Assert-NoControlCharacters $PluginRoot
    Assert-NoControlCharacters $Target
    if ($Plugin -notmatch '^[^@/\\\s]+@[^@/\\\s]+$') {
        throw 'plugin must be an exact name@marketplace identity'
    }

    $canonicalRoot = Get-CanonicalPath $PluginRoot $true
    $canonicalTarget = Get-CanonicalPath $Target $false
    $separator = [System.IO.Path]::DirectorySeparatorChar
    $rootPrefix = $canonicalRoot.TrimEnd('\', '/') + $separator
    $comparison = if ([Environment]::OSVersion.Platform -eq [PlatformID]::Win32NT) {
        [StringComparison]::OrdinalIgnoreCase
    } else {
        [StringComparison]::Ordinal
    }
    if (-not $canonicalTarget.StartsWith($rootPrefix, $comparison)) {
        throw 'target must be contained by plugin root'
    }

    $name, $marketplace = $Plugin.Split('@', 2)
    $agentHome = if ($env:AGENT_HOME) {
        $env:AGENT_HOME
    } else {
        [Environment]::GetFolderPath([Environment+SpecialFolder]::UserProfile)
    }
    $directory = Join-Path $agentHome '.agent-codespaces\config.d'
    [System.IO.Directory]::CreateDirectory($directory) | Out-Null
    $entry = Join-Path $directory "$name@$marketplace.json"
    $payload = @{
        schema_version = 1
        plugin = $Plugin
        plugin_root = $canonicalRoot
        target = $canonicalTarget
    } | ConvertTo-Json -Compress

    $temporary = Join-Path $directory ".$([System.IO.Path]::GetFileName($entry)).$PID.$([Guid]::NewGuid().ToString('N')).tmp"
    $backup = "$entry.$PID.bak"
    try {
        [System.IO.File]::WriteAllText(
            $temporary,
            "$payload`n",
            [System.Text.UTF8Encoding]::new($false)
        )
        if ([System.IO.File]::Exists($entry)) {
            [System.IO.File]::Replace($temporary, $entry, $backup, $true)
        } else {
            try {
                [System.IO.File]::Move($temporary, $entry)
            } catch [System.IO.IOException] {
                if (-not [System.IO.File]::Exists($entry)) {
                    throw
                }
                [System.IO.File]::Replace($temporary, $entry, $backup, $true)
            }
        }
    } finally {
        Remove-Item -LiteralPath $temporary, $backup -Force -ErrorAction SilentlyContinue
    }
    Write-Output $entry
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 2
}
