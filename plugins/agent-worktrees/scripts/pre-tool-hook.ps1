# Native event entry; Python remains the policy path for uncertain input/cell admission.
$ErrorActionPreference = 'Stop'
$utf8 = [Text.UTF8Encoding]::new($false, $true)

function ConvertFrom-PreToolJson([string]$Text) {
    if (-not $Text.TrimStart().StartsWith('{')) { throw 'Expected a JSON object' }
    if ($PSVersionTable.PSVersion.Major -ge 6) {
        return ConvertFrom-Json -InputObject $Text -AsHashtable -ErrorAction Stop
    }
    if (-not $script:preToolJson) {
        [void][Reflection.Assembly]::Load('System.Web.Extensions, Version=4.0.0.0, Culture=neutral, PublicKeyToken=31bf3856ad364e35')
        $script:preToolJson = [System.Web.Script.Serialization.JavaScriptSerializer]::new()
    }
    return $script:preToolJson.DeserializeObject($Text)
}

function Read-PreToolJson([string]$Path, [int]$Limit) {
    $info = [IO.FileInfo]::new($Path)
    if (-not $info.Exists) { throw 'JSON file is unavailable' }
    if (($info.Attributes -band [IO.FileAttributes]::ReparsePoint) -or $info.Length -gt $Limit) {
        throw 'JSON file is linked or exceeds its bound'
    }
    return ConvertFrom-PreToolJson ([IO.File]::ReadAllText($Path, $utf8))
}

function Get-NativePreToolStop {
    if ($env:COPILOT_EXTENSIONS_CONTEXT) { return $false }
    $path = $env:PRETOOL_KILLSWITCH_STATE_FILE
    if ($path -and -not [IO.Path]::IsPathRooted($path)) { return $null }
    if (-not $path) {
        $path = Join-Path $env:USERPROFILE '.copilot-extensions\killSwitches\pretool-killswitch.json'
    }
    if (-not [IO.File]::Exists($path)) { return $false }
    try {
        $state = Read-PreToolJson $path 16384
        if ($state -isnot [Collections.IDictionary] -or $state.active -isnot [bool]) { return $null }
        if ($state.ContainsKey('contract')) {
            $contract = $state.contract
            if ($contract -isnot [Collections.IDictionary] -or
                $contract.id -cne 'copilot-extensions.pretool-killswitch' -or
                ($contract.version -isnot [int] -and $contract.version -isnot [long]) -or $contract.version -ne 1) { return $null }
        }
        return $state.active
    } catch { return $null }
}

function Test-NativePreToolRead([object]$Payload) {
    if ($env:COPILOT_EXTENSIONS_CONTEXT -or $Payload -isnot [Collections.IDictionary]) { return $false }
    $name = $Payload.toolName
    if (-not $name) { $name = $Payload.tool_name }
    if ($name -isnot [string] -or $name -cnotin @('view', 'rg', 'glob')) { return $false }
    try {
        $policy = Read-PreToolJson (Join-Path $PSScriptRoot 'hook-read-policy.json') 4096
        if ($policy.schema -cne 'agent-worktrees.native-read-policy' -or
            ($policy.version -isnot [int] -and $policy.version -isnot [long]) -or $policy.version -ne 1 -or
            $policy.guards -isnot [Collections.IDictionary]) { return $false }
        $names = @('statelessness_guard.py', 'cross_repo_guard.py', 'anchor_write_guard.py', 'pr_supersede_guard.py')
        if ($policy.guards.Count -ne $names.Count) { return $false }
        foreach ($guard in $names) {
            $expected = $policy.guards[$guard]
            if ($expected -isnot [string] -or $expected -cnotmatch '^[0-9a-f]{64}$') { return $false }
            $path = Join-Path $PSScriptRoot $guard
            $info = [IO.FileInfo]::new($path)
            if (-not $info.Exists -or $info.Length -gt 131072 -or
                ($info.Attributes -band [IO.FileAttributes]::ReparsePoint)) { return $false }
            # Exact normalized source binds the exemption across partial helper upgrades.
            $text = [IO.File]::ReadAllText($path, $utf8).Replace("`r`n", "`n")
            $hasher = [Security.Cryptography.SHA256]::Create()
            try {
                $actual = ([BitConverter]::ToString($hasher.ComputeHash($utf8.GetBytes($text)))).Replace('-', '').ToLowerInvariant()
            } finally { $hasher.Dispose() }
            if ($actual -cne $expected) { return $false }
        }
        return $true
    } catch { return $false }
}

try {
    try { Set-Location -LiteralPath $env:USERPROFILE -ErrorAction Stop }
    catch { Set-Location -LiteralPath ([IO.Path]::GetPathRoot($PSScriptRoot)) -ErrorAction Stop }
    [IO.Directory]::SetCurrentDirectory((Get-Location).Path)
    $stop = Get-NativePreToolStop
    if ($stop -eq $true) { exit 0 }
    $reader = [IO.StreamReader]::new([Console]::OpenStandardInput(), $utf8, $false)
    try { $raw = $reader.ReadToEnd() } finally { $reader.Dispose() }
    if ($stop -eq $false) {
        try {
            $payload = ConvertFrom-PreToolJson $raw
            if (Test-NativePreToolRead $payload) { exit 0 }
        } catch { }
    }
    $python = $null
    $resolver = Join-Path $PSScriptRoot 'resolve-runtime.ps1'
    if (-not $env:COPILOT_EXTENSIONS_CONTEXT -and [IO.File]::Exists($resolver)) {
        $previousRoot = $env:AGENT_RT_ROOT
        try {
            $env:AGENT_RT_ROOT = Join-Path $env:USERPROFILE '.agent-worktrees'
            . $resolver -TraceMode read-only
            $python = $AwPy
        } finally {
            if ($null -eq $previousRoot) { Remove-Item Env:AGENT_RT_ROOT -ErrorAction SilentlyContinue }
            else { $env:AGENT_RT_ROOT = $previousRoot }
        }
    }
    if (-not $python) {
        $python = Get-Command python -CommandType Application -All -ErrorAction SilentlyContinue |
            Where-Object { $_.Source -notlike '*\WindowsApps\*' } |
            Select-Object -First 1 -ExpandProperty Source
    }
    $client = Join-Path $PSScriptRoot 'hook_client.py'
    $runner = Join-Path $PSScriptRoot 'versioned_runtime_native.ps1'
    if (-not $python -or -not [IO.File]::Exists($client) -or -not [IO.File]::Exists($runner)) {
        throw 'Required pre-tool policy interpreter/client/runner is unavailable'
    }
    . $runner
    $diagnostic = ''
    $previousPythonEncoding = [Environment]::GetEnvironmentVariable('PYTHONIOENCODING', 'Process')
    try {
        [Environment]::SetEnvironmentVariable('PYTHONIOENCODING', 'utf-8', 'Process')
        $result = Invoke-Hidden -FilePath $python -ArgList @('-X', 'utf8', '-B', $client, 'preToolUseStrict') `
            -InputText $raw -ErrorOutput ([ref]$diagnostic)
    } finally {
        [Environment]::SetEnvironmentVariable('PYTHONIOENCODING', $previousPythonEncoding, 'Process')
    }
    if ($LASTEXITCODE -ne 0) { throw "Pre-tool policy client exited $LASTEXITCODE" }
    if (-not $result -or -not $result.Trim() -or $utf8.GetByteCount($result) -gt 65536) {
        throw 'Strict pre-tool policy returned no bounded response'
    }
    $response = ConvertFrom-PreToolJson $result
    if ($response -isnot [Collections.IDictionary]) { throw 'Invalid pre-tool policy response' }
    foreach ($name in $response.Keys) {
        if ($name -cnotin @('permissionDecision', 'permissionDecisionReason', 'additionalContext')) {
            throw 'Invalid pre-tool policy response'
        }
    }
    if ($response.ContainsKey('permissionDecision') -and (
        $response.permissionDecision -isnot [string] -or
        $response.permissionDecision -cnotin @('allow', 'ask', 'deny')
    )) { throw 'Invalid pre-tool policy decision' }
    foreach ($name in @('permissionDecisionReason', 'additionalContext')) {
        if ($response.ContainsKey($name) -and $response[$name] -isnot [string]) {
            throw 'Invalid pre-tool policy response'
        }
    }
    if ($diagnostic) { [Console]::Error.Write($diagnostic) }
    [Console]::Out.Write($result)
} catch {
    [Console]::Error.WriteLine('agent-worktrees preToolUse: ' + $_.Exception.Message)
    [Console]::Out.Write('{"permissionDecision":"deny","permissionDecisionReason":"agent-worktrees pre-tool policy is unavailable; repair its owning installation"}')
}
exit 0
