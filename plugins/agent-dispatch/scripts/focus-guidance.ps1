# Emit opt-in worktree-focus guidance for an applicable sessionStart payload.

$ErrorActionPreference = 'SilentlyContinue'
$GitEnvironmentNames = @(
    'GIT_DIR', 'GIT_WORK_TREE', 'GIT_COMMON_DIR', 'GIT_INDEX_FILE',
    'GIT_OBJECT_DIRECTORY', 'GIT_ALTERNATE_OBJECT_DIRECTORIES',
    'GIT_CEILING_DIRECTORIES', 'GIT_DISCOVERY_ACROSS_FILESYSTEM',
    'GIT_PREFIX', 'GIT_SUPER_PREFIX', 'GIT_QUARANTINE_PATH', 'GIT_NAMESPACE',
    'GIT_CONFIG', 'GIT_CONFIG_SYSTEM', 'GIT_CONFIG_GLOBAL',
    'GIT_CONFIG_NOSYSTEM', 'GIT_CONFIG_COUNT'
)
$GitEnvironmentNames += @(
    Get-ChildItem Env: |
        Where-Object Name -Match '^GIT_CONFIG_(?:KEY|VALUE)_\d+$' |
        ForEach-Object Name
)

function Emit-Empty {
    [Console]::Out.Write('{}')
    exit 0
}

function Read-PluginVersion {
    $ManifestPath = Join-Path (Split-Path -Parent $PSScriptRoot) 'plugin.json'
    $Stream = [IO.File]::Open(
        $ManifestPath,
        [IO.FileMode]::Open,
        [IO.FileAccess]::Read,
        [IO.FileShare]::Read
    )
    try {
        if ($Stream.Length -gt 4096) { throw 'invalid manifest' }
        $Buffer = New-Object byte[] 4097
        $Count = 0
        while ($Count -lt $Buffer.Length) {
            $Read = $Stream.Read($Buffer, $Count, $Buffer.Length - $Count)
            if ($Read -eq 0) { break }
            $Count += $Read
        }
    } finally {
        $Stream.Dispose()
    }
    if ($Count -gt 4096 -or [Array]::IndexOf($Buffer, [byte]0, 0, $Count) -ge 0) {
        throw 'invalid manifest'
    }
    $Utf8 = New-Object Text.UTF8Encoding($false, $true)
    $ManifestText = $Utf8.GetString($Buffer, 0, $Count)
    if (-not $ManifestText.TrimStart().StartsWith('{')) {
        throw 'invalid manifest'
    }
    $Manifest = $ManifestText | ConvertFrom-Json -ErrorAction Stop
    if ($Manifest -isnot [pscustomobject]) { throw 'invalid manifest' }
    $Version = $Manifest.version
    if ($Version -isnot [string] -or $Version.Length -gt 64 -or
        $Version -cnotmatch '\A[0-9]+\.[0-9]+\.[0-9]+(?:-dev[0-9]+)?\z') {
        throw 'invalid manifest version'
    }
    return $Version
}

function Read-BoundedUtf8Stdin {
    $Stream = [Console]::OpenStandardInput()
    $Buffer = New-Object byte[] 65537
    $Count = 0
    while ($Count -lt $Buffer.Length) {
        $Read = $Stream.Read($Buffer, $Count, $Buffer.Length - $Count)
        if ($Read -eq 0) { break }
        $Count += $Read
    }
    if ($Count -gt 65536 -or [Array]::IndexOf($Buffer, [byte]0, 0, $Count) -ge 0) {
        throw 'invalid payload'
    }
    $Utf8 = New-Object Text.UTF8Encoding($false, $true)
    return $Utf8.GetString($Buffer, 0, $Count)
}

function Test-PathContainsReparsePoint([string] $Path) {
    try {
        $Full = [IO.Path]::GetFullPath($Path)
        $PathRoot = [IO.Path]::GetPathRoot($Full)
        $Current = $PathRoot
        foreach ($Segment in ($Full.Substring($PathRoot.Length) -split '[\\/]')) {
            if (-not $Segment) { continue }
            $Current = Join-Path $Current $Segment
            $Item = Get-Item -LiteralPath $Current -Force -ErrorAction Stop
            if (($Item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                return $true
            }
        }
        return $false
    } catch {
        return $true
    }
}

function Test-ContainedPath([string] $Root, [string] $Path) {
    $RootFull = [IO.Path]::GetFullPath($Root).TrimEnd(
        [IO.Path]::DirectorySeparatorChar,
        [IO.Path]::AltDirectorySeparatorChar
    ) + [IO.Path]::DirectorySeparatorChar
    $PathFull = [IO.Path]::GetFullPath($Path)
    $Comparison = if ($env:OS -eq 'Windows_NT') {
        [StringComparison]::OrdinalIgnoreCase
    } else {
        [StringComparison]::Ordinal
    }
    return $PathFull.StartsWith($RootFull, $Comparison)
}

function Read-FocusOptIn([string] $Root) {
    $ConfigPath = Join-Path (Join-Path $Root '.agent-dispatch') 'session-guidance.json'
    if ((Test-PathContainsReparsePoint $ConfigPath) -or
        -not (Test-ContainedPath $Root $ConfigPath)) {
        return $false
    }

    $ResolvedPath = (Resolve-Path -LiteralPath $ConfigPath -ErrorAction Stop).Path
    if (-not (Test-ContainedPath $Root $ResolvedPath)) {
        return $false
    }

    $Stream = [IO.File]::Open(
        $ResolvedPath,
        [IO.FileMode]::Open,
        [IO.FileAccess]::Read,
        [IO.FileShare]::Read
    )
    try {
        if ($Stream.Length -gt 4096) { return $false }
        $Buffer = New-Object byte[] 4097
        $Count = 0
        while ($Count -lt $Buffer.Length) {
            $Read = $Stream.Read($Buffer, $Count, $Buffer.Length - $Count)
            if ($Read -eq 0) { break }
            $Count += $Read
        }
    } finally {
        $Stream.Dispose()
    }
    if ($Count -gt 4096 -or [Array]::IndexOf($Buffer, [byte]0, 0, $Count) -ge 0) {
        return $false
    }
    $Utf8 = New-Object Text.UTF8Encoding($false, $true)
    $Text = $Utf8.GetString($Buffer, 0, $Count)
    $Config = $Text | ConvertFrom-Json -ErrorAction Stop
    if ($Config -isnot [PSCustomObject]) { return $false }
    $TopKeys = @($Config.PSObject.Properties.Name)
    if ($TopKeys.Count -ne 1 -or $TopKeys[0] -cne 'session_guidance') {
        return $false
    }
    $Guidance = $Config.session_guidance
    if ($Guidance -isnot [PSCustomObject]) { return $false }
    $PolicyKeys = @($Guidance.PSObject.Properties.Name)
    if ($PolicyKeys.Count -ne 1 -or $PolicyKeys[0] -cne 'focus') {
        return $false
    }
    return $Guidance.focus -is [bool] -and $Guidance.focus
}

function Invoke-WithCleanGitEnvironment([scriptblock] $Action) {
    $Saved = @{}
    try {
        foreach ($Name in $GitEnvironmentNames) {
            $Item = Get-Item -LiteralPath "Env:$Name" -ErrorAction SilentlyContinue
            if ($null -ne $Item) {
                $Saved[$Name] = $Item.Value
                Remove-Item -LiteralPath "Env:$Name" -ErrorAction SilentlyContinue
            }
        }
        return & $Action
    } finally {
        foreach ($Name in $GitEnvironmentNames) {
            Remove-Item -LiteralPath "Env:$Name" -ErrorAction SilentlyContinue
            if ($Saved.ContainsKey($Name)) {
                Set-Item -LiteralPath "Env:$Name" -Value $Saved[$Name]
            }
        }
    }
}

try {
    $PluginVersion = Read-PluginVersion
    $Kernel = "[owner: agent-dispatch@$PluginVersion]" + [char]10 +
        'Before choosing or starting new work, use the agent-dispatch session ' +
        "command catalog's exact " +
        '`argv[0]` with `worktree-status`; resume or claim work explicitly ' +
        'targeted at this worktree before self-selecting unless it conflicts ' +
        'with the operator''s current request. Before starting work likely to ' +
        'overlap another worktree, use the ' +
        "agent-dispatch session command catalog's exact " +
        '`argv[0]` with ' +
        '`focus --list`. At the start of substantial operator-led ' +
        'or task-less work, and when its direction changes, advertise it early ' +
        'with that same command plus `focus "<one-line subject>"`; this is shorthand for ' +
        'writing the same agent-worktrees status-core summary, not a separate ' +
        'store. Agent-worktrees conduct and regular ' +
        '`agent-worktrees status --summary` remain authoritative for ongoing ' +
        'disposition, and their normal update cadence still applies.'
    $InputText = Read-BoundedUtf8Stdin
    $Payload = $InputText | ConvertFrom-Json -ErrorAction Stop
    if ($Payload -is [Array] -or -not ($Payload.PSObject.Properties.Name -contains 'cwd')) {
        Emit-Empty
    }
    $Cwd = $Payload.cwd
    if ($Cwd -isnot [string] -or -not [IO.Path]::IsPathRooted($Cwd) -or
        $Cwd.IndexOfAny([char[]](0..31)) -ge 0 -or
        -not (Test-Path -LiteralPath $Cwd -PathType Container)) {
        Emit-Empty
    }

    $Git = Get-Command git -ErrorAction Stop
    $AgentWorktrees = Get-Command agent-worktrees -ErrorAction Stop # marketplace-isolation: allow agent-worktrees-management
    $Result = Invoke-WithCleanGitEnvironment {
        $Root = (& $Git.Source -C $Cwd rev-parse --show-toplevel 2>$null |
            Select-Object -First 1)
        if (-not $Root -or -not (Test-Path -LiteralPath $Root -PathType Container)) {
            return $null
        }
        $Root = (Resolve-Path -LiteralPath $Root -ErrorAction Stop).Path
        if (-not (Read-FocusOptIn $Root)) {
            return $null
        }

        Push-Location -LiteralPath $Cwd
        try {
            $Project = (& $AgentWorktrees.Source get project 2>$null |
                Select-Object -First 1)
            if (-not $Project) {
                return $null
            }
            & $AgentWorktrees.Source status --help *> $null
            if ($LASTEXITCODE -ne 0) {
                return $null
            }
        } finally {
            Pop-Location
        }
        return $true
    }
    if (-not $Result) {
        Emit-Empty
    }

    [Console]::Out.Write((@{ additionalContext = $Kernel } | ConvertTo-Json -Compress))
} catch {
    Emit-Empty
}
exit 0
