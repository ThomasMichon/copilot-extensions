# Reconcile registered local marketplace sources on session start.

$ErrorActionPreference = 'SilentlyContinue'

$payload = ''
if ([Console]::IsInputRedirected) {
    try { $payload = [Console]::In.ReadToEnd() } catch { }
}
$AwaitContext = $args -contains '--await-context'
$ContextOnly = ($args -contains '--context-only') -or $AwaitContext
$SideEffectOnly = $args -contains '--side-effect-only'
$ProducerVersion = ''
if ($env:COPILOT_PLUGIN_ROOT) {
    $Manifest = Join-Path $env:COPILOT_PLUGIN_ROOT 'plugin.json'
    try {
        $ProducerVersion = [string](
            (Get-Content -Raw -LiteralPath $Manifest | ConvertFrom-Json).version
        )
    } catch { }
}
if (-not $ProducerVersion) {
    try {
        $ProducerVersion = (
            Get-Content -Raw -LiteralPath (
                Join-Path $env:USERPROFILE '.agent-worktrees\current-version'
            )
        ).Trim()
    } catch { }
}

function Get-LaunchKey([string]$InputPayload, [string]$Version) {
    if (-not $InputPayload -or -not $Version) { return '' }
    try {
        $Data = $InputPayload | ConvertFrom-Json
        $SessionId = [string]$Data.sessionId
        $Cwd = [string]$Data.cwd
        $Source = [string]$Data.source
        $Timestamp = $Data.timestamp
        if (-not $SessionId -or -not $Cwd -or
            -not [IO.Path]::IsPathRooted($Cwd) -or
            $Timestamp -is [bool] -or
            $Timestamp -isnot [ValueType]) {
            return ''
        }
        $CanonicalCwd = if (Test-Path -LiteralPath $Cwd -PathType Container) {
            (Resolve-Path -LiteralPath $Cwd).Path
        } else {
            [IO.Path]::GetFullPath($Cwd)
        }
        if (-not $CanonicalCwd) { return '' }
        $TimestampText = [Convert]::ToString(
            $Timestamp,
            [Globalization.CultureInfo]::InvariantCulture
        )
        if (-not $TimestampText) { return '' }
        $Identity = @(
            $SessionId, $CanonicalCwd, $Source, $Version, $TimestampText
        ) | ConvertTo-Json -Compress
        $Sha = [System.Security.Cryptography.SHA256]::Create()
        try {
            return -join (
                $Sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($Identity)) |
                    ForEach-Object { $_.ToString('x2') }
            )
        } finally {
            $Sha.Dispose()
        }
    } catch {
        return ''
    }
}
$LaunchKey = Get-LaunchKey $payload $ProducerVersion
$ContextDir = Join-Path $env:USERPROFILE '.agent-worktrees\.session-context'
$ContextFile = $null
if ($LaunchKey) {
    $ContextFile = Join-Path $ContextDir "marketplace-overrides-$LaunchKey.json"
}

function Publish-Context([string]$Output) {
    if (-not $ContextOnly -and $LaunchKey -and $ContextFile) {
        New-Item -ItemType Directory -Path $ContextDir -Force | Out-Null
        $State = @{
            launchKey = $LaunchKey
            output = $Output
        } | ConvertTo-Json -Compress
        $Temporary = "$ContextFile.$PID.tmp"
        Set-Content -LiteralPath $Temporary -Value $State -Encoding UTF8
        Move-Item -LiteralPath $Temporary -Destination $ContextFile -Force
    }
    [Console]::Out.Write($(if ($SideEffectOnly) { '{}' } else { $Output }))
}

if ($ContextOnly) {
    if ($AwaitContext -and $LaunchKey -and $ContextFile) {
        $Deadline = [DateTime]::UtcNow.AddSeconds(3)
        while (-not (Test-Path -LiteralPath $ContextFile -PathType Leaf) -and
            [DateTime]::UtcNow -lt $Deadline) {
            Start-Sleep -Milliseconds 50
        }
    }
    if (-not $LaunchKey -or -not $ContextFile -or
        -not (Test-Path -LiteralPath $ContextFile -PathType Leaf)) {
        Publish-Context '{}'
        exit 0
    }
    try {
        $State = Get-Content -Raw -LiteralPath $ContextFile | ConvertFrom-Json
        if ([string]$State.launchKey -ceq $LaunchKey -and $State.output) {
            Publish-Context ([string]$State.output)
            exit 0
        }
    } catch { }
    Publish-Context '{}'
    exit 0
}

$_r = Join-Path $env:USERPROFILE '.agent-worktrees\bin\resolve-runtime.ps1'
$python = if (Test-Path -LiteralPath $_r) { . $_r; $AwPy } else { $null }
if (-not $python) { Publish-Context '{}'; exit 0 }

$env:PYTHONPATH = ''
$out = ''
try {
    $out = ($payload | & $python -m agent_worktrees reconcile-marketplaces `
        --stdin --session-start 2>$null
        | Out-String).Trim()
} catch {
}
if ($out) { Publish-Context $out; exit 0 }
Publish-Context '{}'
exit 0
