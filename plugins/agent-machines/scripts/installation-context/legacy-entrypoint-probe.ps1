[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$PayloadRoot,
    [Parameter(Mandatory = $true)]
    [string]$LegacyRoot
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2

function Fail([string]$Message) {
    [Console]::Error.WriteLine("legacy-entrypoint-probe: $Message")
    exit 1
}

function Get-ExactProperty($Object, [string]$Name) {
    $matches = @($Object.PSObject.Properties | Where-Object { $_.Name -ceq $Name })
    if ($matches.Count -ne 1) { throw "missing or duplicate property: $Name" }
    return $matches[0]
}

function Get-ExactStringProperty($Object, [string]$Name) {
    $value = (Get-ExactProperty $Object $Name).Value
    if ($value -isnot [string] -or -not $value) {
        throw "$Name must be a non-empty string"
    }
    return $value
}

function Test-LegacyPath([string]$Path) {
    if (Test-Path -LiteralPath $Path) { return $true }
    return $null -ne (Get-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue)
}

function Get-ProfileHome {
    if ($env:OS -eq 'Windows_NT') {
        if (-not $env:USERPROFILE) { throw 'USERPROFILE is required on Windows' }
        return (Resolve-Path -LiteralPath $env:USERPROFILE).Path
    }
    $uid = '' + (& id -u 2>$null)
    if ($LASTEXITCODE -ne 0 -or -not $uid.Trim()) {
        throw 'cannot determine the current account identity'
    }
    $entry = ''
    if (Get-Command getent -ErrorAction SilentlyContinue) {
        $entry = '' + (& getent passwd $uid.Trim() 2>$null)
    }
    if (-not $entry.Trim() -and (Test-Path -LiteralPath '/etc/passwd' -PathType Leaf)) {
        $entry = [IO.File]::ReadLines('/etc/passwd') |
            Where-Object { ($_ -split ':')[2] -eq $uid.Trim() } |
            Select-Object -First 1
    }
    $home_path = ''
    $fields = @($entry -split ':')
    if ($fields.Count -ge 6 -and $fields[5]) {
        $home_path = $fields[5]
    }
    elseif (Get-Command dscl -ErrorAction SilentlyContinue) {
        # macOS ships no `getent` and keeps ordinary accounts in
        # DirectoryService, not /etc/passwd (system accounts only), so both
        # lookups above miss for every normal user. `dscl` is the authoritative
        # lookup there -- matching the sh probe and this library's Python
        # implementation (`pwd.getpwuid()`).
        $user = '' + (& id -un 2>$null)
        if ($user.Trim()) {
            # Keep the output as lines: `dscl -read` can emit more than one, and
            # a single collapsed string would let `(.+)$` swallow whatever
            # follows -- or miss the value entirely when it is not last.
            $record = @(& dscl . -read "/Users/$($user.Trim())" NFSHomeDirectory 2>$null)
            foreach ($line in $record) {
                if ($line -match '^NFSHomeDirectory:\s*(.+?)\s*$') {
                    $home_path = $Matches[1]
                    break
                }
            }
        }
    }
    if (-not $home_path -or -not [IO.Path]::IsPathRooted($home_path)) {
        throw 'cannot determine the current account home from the account database (passwd or DirectoryService)'
    }
    return (Resolve-Path -LiteralPath $home_path).Path
}

if (-not [IO.Path]::IsPathRooted($PayloadRoot)) { Fail '-PayloadRoot must be absolute' }
if (-not [IO.Path]::IsPathRooted($LegacyRoot)) { Fail '-LegacyRoot must be absolute' }
$PayloadRoot = [IO.Path]::GetFullPath($PayloadRoot)
$LegacyRoot = [IO.Path]::GetFullPath($LegacyRoot)
$ProfileHome = Get-ProfileHome
$ScriptDir = $PSScriptRoot
$Resolver = Join-Path $ScriptDir 'installation-context.ps1'
$Manifest = Join-Path $PayloadRoot 'payload-invocation.json'
if (-not (Test-Path -LiteralPath $Resolver -PathType Leaf)) {
    Fail 'installation-context resolver is unavailable'
}

$PluginId = ''
$Declared = $false
$Result = 'unknown'
if (Test-Path -LiteralPath $Manifest -PathType Leaf) {
    try {
        $strictUtf8 = New-Object Text.UTF8Encoding($false, $true)
        $document = [IO.File]::ReadAllText($Manifest, $strictUtf8) | ConvertFrom-Json
        $PluginId = Get-ExactStringProperty $document 'command'
        $installation = (Get-ExactProperty $document 'installation').Value
        $footprint = (Get-ExactProperty $installation 'legacyFootprint').Value
        $rawPaths = (Get-ExactProperty $footprint 'paths').Value
        $rawServices = (Get-ExactProperty $footprint 'services').Value
        $rawTasks = (Get-ExactProperty $footprint 'tasks').Value
        if ($rawPaths -isnot [Array] -or $rawServices -isnot [Array] -or $rawTasks -isnot [Array]) {
            throw 'legacyFootprint paths, services, and tasks must be arrays'
        }
        $paths = @($rawPaths)
        $services = @($rawServices)
        $tasks = @($rawTasks)
        $Declared = $true
        $Result = 'absent'
        $unknown = $false

        foreach ($entry in $paths) {
            if ($entry -isnot [string] -or -not $entry) {
                $unknown = $true
                continue
            }
            $path = if ([IO.Path]::IsPathRooted($entry)) {
                $entry
            } elseif ($entry.StartsWith('~/') -or $entry.StartsWith('~\')) {
                Join-Path $ProfileHome $entry.Substring(2)
            } else {
                Join-Path $ProfileHome $entry
            }
            if (Test-LegacyPath $path) {
                $Result = 'present'
                break
            }
        }
        if ($Result -ne 'present' -and (Test-LegacyPath $LegacyRoot)) {
            $Result = 'present'
        }

        $platform = if ($env:OS -eq 'Windows_NT') { 'windows' } else { 'posix' }
        foreach ($entry in $services) {
            if ($Result -eq 'present') { break }
            try {
                $entryPlatform = Get-ExactStringProperty $entry 'platform'
                $manager = Get-ExactStringProperty $entry 'manager'
                $name = Get-ExactStringProperty $entry 'name'
                if ($entryPlatform -notin @('windows', 'posix')) {
                    $unknown = $true
                    continue
                }
                if ($entryPlatform -ne $platform) { continue }
                if ($manager -eq 'systemd-user' -and $platform -eq 'posix') {
                    if (-not (Get-Command systemctl -ErrorAction SilentlyContinue)) { continue }
                    $loadState = '' + (& systemctl --user show $name --property=LoadState --value 2>$null)
                    if ($LASTEXITCODE -ne 0) {
                        $unknown = $true
                    } elseif ($loadState.Trim() -and $loadState.Trim() -ne 'not-found') {
                        $Result = 'present'
                    }
                } else {
                    $unknown = $true
                }
            } catch {
                $unknown = $true
            }
        }

        foreach ($entry in $tasks) {
            if ($Result -eq 'present') { break }
            try {
                $entryPlatform = Get-ExactStringProperty $entry 'platform'
                $manager = Get-ExactStringProperty $entry 'manager'
                $name = Get-ExactStringProperty $entry 'name'
                if ($entryPlatform -notin @('windows', 'posix')) {
                    $unknown = $true
                    continue
                }
                if ($entryPlatform -ne $platform) { continue }
                if ($manager -eq 'windows-scheduled-task' -and $platform -eq 'windows') {
                    if (-not (Get-Command Get-ScheduledTask -ErrorAction SilentlyContinue)) {
                        $unknown = $true
                    } elseif (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) {
                        $Result = 'present'
                    }
                } else {
                    $unknown = $true
                }
            } catch {
                $unknown = $true
            }
        }
        if ($Result -eq 'absent' -and $unknown) { $Result = 'unknown' }
    } catch {
        $Declared = $false
        $Result = 'unknown'
    }
}

if (-not $PluginId) { Fail 'payload-invocation.json has no usable command identity' }
$legacyProbe = [ordered]@{
    declared = $Declared
    result = $Result
    checkedAt = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
} | ConvertTo-Json -Compress

$hostExe = (Get-Process -Id $PID).Path
if (-not $hostExe) { Fail 'PowerShell host executable is unavailable' }
$probeBase64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($legacyProbe))
function Quote-ChildArgument([string]$Value) {
    return "'" + $Value.Replace("'", "''") + "'"
}
$childCommand = @(
    '&'
    (Quote-ChildArgument $Resolver)
    'probe-legacy'
    '-PayloadRoot'
    (Quote-ChildArgument $PayloadRoot)
    '-PluginId'
    (Quote-ChildArgument $PluginId)
    '-LegacyRoot'
    (Quote-ChildArgument $LegacyRoot)
    '-LegacyProbeJson'
    "([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('$probeBase64')))"
) -join ' '
$encodedCommand = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($childCommand))
$clearContext = $false
$inheritedContext = $env:COPILOT_EXTENSIONS_CONTEXT
if ($inheritedContext -and (Test-Path -LiteralPath $inheritedContext -PathType Leaf)) {
    $contextDurableHome = $inheritedContext
    1..5 | ForEach-Object { $contextDurableHome = Split-Path -Parent $contextDurableHome }
    $global:LASTEXITCODE = 1
    $validatedContextJson = & $hostExe -NoProfile -ExecutionPolicy Bypass -File $Resolver validate `
        -Context $inheritedContext -DurableHome $contextDurableHome 2>$null
    if ($LASTEXITCODE -eq 0) {
        try {
            $validatedContext = $validatedContextJson | ConvertFrom-Json
            $contextPluginId = Get-ExactStringProperty $validatedContext 'pluginId'
            $clearContext = $contextPluginId -cne $PluginId
        } catch {}
    }
}
if ($clearContext) { Remove-Item Env:COPILOT_EXTENSIONS_CONTEXT }
try {
    $decisionJson = & $hostExe -NoProfile -ExecutionPolicy Bypass -EncodedCommand $encodedCommand
    $status = $LASTEXITCODE
} finally {
    if ($clearContext) { $env:COPILOT_EXTENSIONS_CONTEXT = $inheritedContext }
}
if ($status -eq 0) { exit 0 }
$reason = ''
$allowMutation = $null
try {
    $decision = $decisionJson | ConvertFrom-Json
    $reason = [string]$decision.probeReason
    $allowMutation = $decision.allowMutation
} catch {}
if ($reason -and $allowMutation -is [bool] -and -not $allowMutation) {
    [Console]::Error.WriteLine("[$PluginId] legacy mutation blocked by installation governance: $reason")
    exit 3
}
[Console]::Error.WriteLine("[$PluginId] legacy mutation probe failed before a safe decision could be made.")
exit 1
