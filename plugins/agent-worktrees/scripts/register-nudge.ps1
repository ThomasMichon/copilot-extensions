<#
    register-nudge -- sessionStart additionalContext hook (hooks.json). Parity of
    register-nudge.sh.

    First-run ONBOARDING nudge: when a session is in a git repo that is NOT a
    registered agent-worktrees project, emit a ONE-TIME (per repo)
    additionalContext nudge inviting `agent-worktrees register <name>`. Nudge
    ONLY -- it NEVER registers/adopts anything (install-vs-adopt boundary).

    Grace-window-cheap + resolver-free: pure PowerShell + a heuristic read of
    projects.yaml, so it works on a tools-half box. Fail-open: emits `{}` on ANY
    uncertainty, and writes only machine-local runtime state
    (~/.agent-worktrees/.register-nudged/), never the repo. PS5.1+.
#>
$ErrorActionPreference = 'SilentlyContinue'

$ContextOnly = $args -contains '--context-only'
$Payload = ''
if ([Console]::IsInputRedirected) {
    try { $Payload = [Console]::In.ReadToEnd() } catch { }
}
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
        $IdentityPython = Get-Command python -ErrorAction SilentlyContinue
        if (-not $IdentityPython) {
            $IdentityPython = Get-Command py -ErrorAction SilentlyContinue
        }
        $CanonicalCwd = if ($IdentityPython) {
            (
                & $IdentityPython.Source -c (
                    'import os,sys;print(os.path.realpath(sys.argv[1]),end="")'
                ) $Cwd 2>$null
            ).Trim()
        } elseif (Test-Path -LiteralPath $Cwd -PathType Container) {
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
$LaunchKey = Get-LaunchKey $Payload $ProducerVersion
$ContextDir = Join-Path $env:USERPROFILE '.agent-worktrees\.session-context'
$ContextFile = $null
if ($LaunchKey) {
    $ContextFile = Join-Path $ContextDir "register-nudge-$LaunchKey.json"
}

function Publish-Context([string]$Output) {
    if (-not $ContextOnly -and $LaunchKey -and $ContextFile) {
        New-Item -ItemType Directory -Path $ContextDir -Force | Out-Null
        $State = @{
            launchKey = $LaunchKey
            output = $Output
        } | ConvertTo-Json -Compress
        Set-Content -LiteralPath $ContextFile -Value $State -Encoding UTF8
    }
    [Console]::Out.Write($Output)
}

if ($ContextOnly) {
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

function Emit-Empty { Publish-Context '{}'; exit 0 }

# Only nudge when agent-worktrees is available to register with.
$binstub = Join-Path $env:USERPROFILE '.local\bin\agent-worktrees'
if (-not (Get-Command agent-worktrees -ErrorAction SilentlyContinue) -and -not (Test-Path $binstub)) { Emit-Empty }

# Must be inside a git work tree.
$top = (git rev-parse --show-toplevel 2>$null)
if (-not $top) { Emit-Empty }
$top = ("$top").Trim()
if (-not $top) { Emit-Empty }

# Derive the repo name: strip a `<repo>.worktrees/<id>` worktree suffix, then
# take the leaf.
$base = $top
$idx = $base.IndexOf('.worktrees/')
if ($idx -ge 0) { $base = $base.Substring(0, $idx) }
$name = Split-Path $base -Leaf
if (-not $name) { Emit-Empty }

# Already registered? (heuristic: the repo name is a project key in
# projects.yaml.) If so, stay silent.
$projects = Join-Path $env:USERPROFILE '.agent-worktrees\projects.yaml'
if (Test-Path $projects) {
    if (Select-String -Path $projects -Pattern "^\s+$([regex]::Escape($name)):\s*$" -Quiet) { Emit-Empty }
}

# Once-per-repo gating.
$markerDir = Join-Path $env:USERPROFILE '.agent-worktrees\.register-nudged'
$sha = [System.Security.Cryptography.SHA1]::Create()
$key = -join ($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($top)) | ForEach-Object { $_.ToString('x2') })
$marker = Join-Path $markerDir $key
if (Test-Path $marker) { Emit-Empty }
New-Item -ItemType Directory -Path $markerDir -Force | Out-Null
Set-Content -LiteralPath $marker -Value '' -NoNewline

$msg = "This repo ($name) is not a registered agent-worktrees project. To enable isolated, concurrent worktree sessions (create/finalize + the PR flow), register it once from the repo root: agent-worktrees register $name . This is an onboarding nudge only -- nothing has been registered, and agent-worktrees never auto-adopts a repo."

$obj = @{ additionalContext = $msg }
Publish-Context ($obj | ConvertTo-Json -Compress)
exit 0
